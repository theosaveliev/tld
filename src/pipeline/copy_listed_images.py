#!/usr/bin/env python3
"""Copy images listed in a wikiteam3 images.txt from a dump into images/.

Sources can be a MediaWiki image dump (hashed layout: X/XX/Name.ext) and/or
flat directories such as an older wikiteam3 dump's images/ folder.

Only files that are in the list AND match the listed size (and SHA-1, unless
--no-sha1) are copied. Anything else is left for wikiteam3 to download.

Usage:
    python copy_listed_images.py \
        --list tld/thelongdark.fandom.com-20261117-images.txt \
        --src  tld/image_dump \
        --dst  tld/images

    add --dry-run first to see what would happen.

Sources:
    --src       MediaWiki hashed layout (X/XX/Name.ext)
    --flat-src  flat directory (e.g. an old wikiteam3 dump's images/ or
                images_mismatch/); may be given several times, earlier wins.
                Use either or both.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from collections.abc import Iterator

MIN_FIELDS = 5  # name, url, uploader, size, sha1 (timestamp is ignored)
SIZE_COL = 3
SHA1_COL = 4
HASH_DIR_LEN = 2  # MediaWiki uses X/XX/ directories
CHUNK_SIZE = 1 << 20
PROGRESS_EVERY = 2000
END_MARKER = "--END--"
PROBLEMS_FILE = "copy_listed_images_problems.tsv"


class Args(argparse.Namespace):
    """Typed command line arguments (defaults are overwritten by argparse)."""

    list_file: Path = Path()
    src: Path | None = None
    flat_src: list[Path] | None = None
    dst: Path = Path()
    no_sha1: bool = False
    hardlink: bool = False
    mismatch_dir: Path | None = None
    mismatch_deltas: str | None = None
    dry_run: bool = False


class Entry(NamedTuple):
    """One line of images.txt."""

    name: str
    size: int
    sha1: str


@dataclass
class Stats:
    """Counters printed in the final summary."""

    listed: int = 0
    copied: int = 0
    already: int = 0
    missing: int = 0
    size_mismatch: int = 0
    mismatch_copied: int = 0
    sha1_mismatch: int = 0
    error: int = 0


def nfc(s: str) -> str:
    """Normalize to NFC (macOS filesystems may return NFD names)."""
    return unicodedata.normalize("NFC", s)


def sha1_of(path: Path) -> str:
    """Return the hex SHA-1 of a file."""
    h = hashlib.sha1(usedforsecurity=False)
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(CHUNK_SIZE), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_line(line: str, lineno: int) -> Entry | None:
    """Parse one tab-separated images.txt line, or return None to skip it."""
    stripped = line.strip()
    if not stripped or stripped == END_MARKER:
        return None
    parts = line.rstrip("\r\n").split("\t")
    if len(parts) < MIN_FIELDS:
        print(f"warning: line {lineno}: unexpected format, skipped", file=sys.stderr)
        return None
    try:
        size = int(parts[SIZE_COL])
    except ValueError:
        size = -1
    return Entry(parts[0], size, parts[SHA1_COL].strip().lower())


def parse_list(list_path: Path) -> Iterator[Entry]:
    """Yield entries from images.txt."""
    with list_path.open(encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            entry = parse_line(line, lineno)
            if entry is not None:
                yield entry


def _subdirs(path: str | Path, name_len: int) -> Iterator[os.DirEntry[str]]:
    """Yield subdirectories of `path` whose names are `name_len` long."""
    for entry in os.scandir(path):
        if entry.is_dir() and len(entry.name) == name_len:
            yield entry


def build_index(src: Path) -> dict[str, Path]:
    """Index originals only: src/X/XX/<file> (depth exactly 3).

    This excludes thumb/, archive/, deleted/, temp/ etc.
    """
    index: dict[str, Path] = {}
    for d1 in _subdirs(src, 1):
        for d2 in _subdirs(d1.path, HASH_DIR_LEN):
            for f in os.scandir(d2.path):
                if f.is_file():
                    index[nfc(f.name)] = Path(f.path)
    return index


def build_flat_index(dirs: list[Path]) -> dict[str, Path]:
    """Index flat directories; earlier directories take priority."""
    index: dict[str, Path] = {}
    for d in dirs:
        for f in os.scandir(d):
            if f.is_file() and not f.name.endswith((".desc", ".part")):
                index.setdefault(nfc(f.name), Path(f.path))
    return index


@dataclass
class Sources:
    """Where to look for original files."""

    src: Path | None
    index: dict[str, Path]
    flat: dict[str, Path]

    def find(self, name: str) -> Path | None:
        """Find a file by name in the hashed dump, then in the flat dirs."""
        key = nfc(name.replace(" ", "_"))
        if self.src is not None:
            # 1) MediaWiki hashed path: md5 of the underscored name.
            md5 = hashlib.md5(key.encode("utf-8"), usedforsecurity=False).hexdigest()
            path = self.src / md5[0] / md5[:2] / key
            if path.is_file():
                return path
            # 2) scanned index (handles NFD names, odd layouts).
            found = self.index.get(key) or self.index.get(nfc(name))
            if found is not None:
                return found
        return self.flat.get(key) or self.flat.get(nfc(name))


class Copier:
    """Decides what to do with each entry and does it."""

    def __init__(self, args: Args, sources: Sources) -> None:
        """Store options and prepare the delta filter."""
        self.sources = sources
        self.dst: Path = args.dst
        self.mismatch_dir: Path | None = args.mismatch_dir
        self.no_sha1: bool = args.no_sha1
        self.hardlink: bool = args.hardlink
        self.dry_run: bool = args.dry_run
        self.allowed_deltas: set[int] | None = None
        if args.mismatch_deltas is not None:
            self.allowed_deltas = {
                int(x) for x in args.mismatch_deltas.split(",") if x.strip()
            }
        self.stats = Stats()
        self.problems: list[str] = []

    def prepare(self) -> None:
        """Create the destination directories."""
        self.dst.mkdir(parents=True, exist_ok=True)
        if self.mismatch_dir is not None and not self.dry_run:
            self.mismatch_dir.mkdir(parents=True, exist_ok=True)

    def place(self, src_file: Path, dst: Path) -> None:
        """Copy (or hardlink) atomically so no half-written file is left."""
        tmp = dst.with_name(dst.name + ".part")
        tmp.unlink(missing_ok=True)
        if self.hardlink:
            tmp.hardlink_to(src_file)
        else:
            shutil.copy2(src_file, tmp)
        tmp.replace(dst)

    def _safe_place(self, src_file: Path, dst: Path, name: str) -> bool:
        """Place a file, recording errors. Return True on success."""
        if self.dry_run:
            return True
        try:
            self.place(src_file, dst)
        except OSError as e:
            self.stats.error += 1
            self.problems.append(f"error\t{name}\t{e}")
            return False
        return True

    def _delta_allowed(self, delta: int) -> bool:
        return self.allowed_deltas is None or delta in self.allowed_deltas

    def process(self, entry: Entry) -> None:
        """Handle one images.txt entry."""
        self.stats.listed += 1
        dst = self.dst / entry.name

        # Already downloaded (and correct)? leave it alone.
        if dst.is_file() and (entry.size < 0 or dst.stat().st_size == entry.size):
            self.stats.already += 1
            return

        src_file = self.sources.find(entry.name)
        if src_file is None:
            self.stats.missing += 1
            self.problems.append(f"missing\t{entry.name}")
            return

        dump_size = src_file.stat().st_size
        if entry.size >= 0 and dump_size != entry.size:
            self._handle_size_mismatch(entry, src_file, dump_size)
            return
        self._copy_verified(entry, src_file, dst)

    def _handle_size_mismatch(
        self, entry: Entry, src_file: Path, dump_size: int
    ) -> None:
        delta = entry.size - dump_size
        self.stats.size_mismatch += 1
        if self.mismatch_dir is None or not self._delta_allowed(delta):
            fields = (
                "size_mismatch",
                entry.name,
                f"dump={dump_size}",
                f"list={entry.size}",
                f"delta={delta}",
            )
            self.problems.append("\t".join(fields))
            return
        mdst = self.mismatch_dir / entry.name
        if mdst.is_file() and mdst.stat().st_size == dump_size:
            return  # already there
        if self._safe_place(src_file, mdst, entry.name):
            self.stats.mismatch_copied += 1

    def _copy_verified(self, entry: Entry, src_file: Path, dst: Path) -> None:
        if not self.no_sha1 and entry.sha1 and sha1_of(src_file) != entry.sha1:
            self.stats.sha1_mismatch += 1
            self.problems.append(f"sha1_mismatch\t{entry.name}")
            return
        if not self._safe_place(src_file, dst, entry.name):
            return
        self.stats.copied += 1
        if not self.dry_run and self.stats.copied % PROGRESS_EVERY == 0:
            print(f"  copied {self.stats.copied}...", file=sys.stderr)

    def report(self) -> None:
        """Print the summary and write the problems report."""
        s = self.stats
        label = "would copy" if self.dry_run else "copied"
        print(f"\nlisted in images.txt : {s.listed}")
        print(f"{label:<21}: {s.copied}")
        print(f"already in dst       : {s.already}")
        print(f"not in sources       : {s.missing}")
        print(f"size mismatch        : {s.size_mismatch}")
        print(f"  -> to mismatch dir : {s.mismatch_copied}")
        print(f"sha1 mismatch        : {s.sha1_mismatch}")
        print(f"errors               : {s.error}")

        if self.problems:
            report = self.dst.parent / PROBLEMS_FILE
            report.write_text("\n".join(self.problems) + "\n", encoding="utf-8")
            print(f"\nDetails of non-copied files: {report}")


def build_parser() -> argparse.ArgumentParser:
    """Build the command line parser."""
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--list", dest="list_file", required=True, type=Path, help="*-images.txt"
    )
    ap.add_argument("--src", type=Path, help="MediaWiki image dir (hashed layout)")
    ap.add_argument(
        "--flat-src",
        type=Path,
        action="append",
        help="flat source dir (repeatable), e.g. old dump's images/",
    )
    ap.add_argument("--dst", required=True, type=Path, help="wikiteam3 images dir")
    ap.add_argument(
        "--no-sha1", action="store_true", help="verify size only (faster, less strict)"
    )
    ap.add_argument(
        "--hardlink",
        action="store_true",
        help="hardlink instead of copy (same filesystem only)",
    )
    ap.add_argument(
        "--mismatch-dir",
        type=Path,
        help="size-mismatched files go here (e.g. tld/images_mismatch)",
    )
    ap.add_argument(
        "--mismatch-deltas",
        help="only these size deltas (list - dump) go to --mismatch-dir, e.g. 18,879",
    )
    ap.add_argument("--dry-run", action="store_true")
    return ap


def validate_args(ap: argparse.ArgumentParser, args: Args) -> None:
    """Exit with a usage error if the source arguments are unusable."""
    if args.src is None and not args.flat_src:
        ap.error("give --src and/or --flat-src")
    if args.src is not None and not args.src.is_dir():
        ap.error(f"--src is not a directory: {args.src}")
    for d in args.flat_src or []:
        if not d.is_dir():
            ap.error(f"--flat-src is not a directory: {d}")


def main() -> int:
    """Run the copy."""
    ap = build_parser()
    args = ap.parse_args(namespace=Args())
    validate_args(ap, args)

    print("Indexing source dump...", file=sys.stderr)
    sources = Sources(
        src=args.src,
        index=build_index(args.src) if args.src else {},
        flat=build_flat_index(args.flat_src or []),
    )
    print(
        f"  {len(sources.index)} hashed + {len(sources.flat)} flat files found",
        file=sys.stderr,
    )

    copier = Copier(args, sources)
    copier.prepare()
    for entry in parse_list(args.list_file):
        copier.process(entry)
    copier.report()
    return 1 if copier.stats.error else 0


if __name__ == "__main__":
    sys.exit(main())
