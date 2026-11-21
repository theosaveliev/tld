"""Audit MediaWiki article titles for portable Markdown filenames.

The audit targets Ubuntu filesystems and the default case-insensitive macOS
APFS configuration. It does not create or rename any files.
"""

from __future__ import annotations

import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import TypedDict

import defusedxml.ElementTree as ET

MAX_FILENAME_BYTES = 255
MARKDOWN_SUFFIX = ".md"


class FilenameMapping(TypedDict):
    title: str
    filename: str


class FilenameIssue(TypedDict):
    kind: str
    titles: list[str]
    filenames: list[str]
    detail: str


class FilenameAuditReport(TypedDict):
    article_pages: int
    unique_titles: int
    redirects_seen: int
    redirects_excluded: int
    duplicate_source_titles: list[str]
    mappings: list[FilenameMapping]
    issues: list[FilenameIssue]


def filename_for_title(title: str) -> str:
    """Return the proposed portable Markdown filename for a wiki title.

    Preserve the title wherever possible. A slash cannot occur inside a
    filename, and a colon is unsuitable for Finder on macOS.
    """
    stem = unicodedata.normalize("NFC", title)
    stem = stem.replace("/", " — ").replace(":", " - ")
    return f"{stem}{MARKDOWN_SUFFIX}"


def extract_page_titles(
    dump_path: Path, *, include_redirects: bool = False
) -> tuple[list[str], int]:
    """Stream a MediaWiki XML dump and return main-namespace page titles.

    Redirect pages are counted but excluded by default because they should
    normally resolve to their target article in the Markdown workspace.
    """
    titles: list[str] = []
    redirects_seen = 0
    events = ET.iterparse(str(dump_path), events=("start", "end"))

    try:
        _, root = next(events)
    except StopIteration as exc:
        msg = f"Wiki dump is empty: {dump_path}"
        raise ValueError(msg) from exc

    for event, element in events:
        if event != "end" or element.tag.rsplit("}", 1)[-1] != "page":
            continue

        fields = {
            child.tag.rsplit("}", 1)[-1]: child for child in element
        }
        title_element = fields.get("title")
        namespace_element = fields.get("ns")
        redirect_element = fields.get("redirect")

        title = title_element.text if title_element is not None else None
        namespace = (
            namespace_element.text if namespace_element is not None else None
        )

        if title and namespace == "0":
            if redirect_element is not None:
                redirects_seen += 1
                if include_redirects:
                    titles.append(title)
            else:
                titles.append(title)

        # Clearing the root after each completed page keeps memory usage low.
        root.clear()

    return titles, redirects_seen


def audit_titles(
    titles: list[str], *, redirects_seen: int = 0, include_redirects: bool = False
) -> FilenameAuditReport:
    """Build filename mappings and report portability or collision issues."""
    counts = Counter(titles)
    unique_titles = list(dict.fromkeys(titles))
    duplicate_titles = sorted(
        title for title, count in counts.items() if count > 1
    )
    mappings: list[FilenameMapping] = [
        {"title": title, "filename": filename_for_title(title)}
        for title in unique_titles
    ]
    issues: list[FilenameIssue] = []

    def add_issue(
        kind: str, affected: list[FilenameMapping], detail: str
    ) -> None:
        issues.append(
            {
                "kind": kind,
                "titles": sorted(item["title"] for item in affected),
                "filenames": sorted({item["filename"] for item in affected}),
                "detail": detail,
            }
        )

    for mapping in mappings:
        filename = mapping["filename"]
        encoded_length = len(filename.encode("utf-8"))
        if encoded_length > MAX_FILENAME_BYTES:
            add_issue(
                "filename_too_long",
                [mapping],
                f"Filename is {encoded_length} UTF-8 bytes; "
                f"the portability limit is {MAX_FILENAME_BYTES} bytes.",
            )

        controls = [
            f"U+{ord(char):04X}"
            for char in filename
            if unicodedata.category(char) == "Cc"
        ]
        if controls:
            add_issue(
                "control_character",
                [mapping],
                "Filename contains control character(s): "
                + ", ".join(sorted(set(controls))),
            )

    exact_groups: dict[str, list[FilenameMapping]] = defaultdict(list)
    folded_groups: dict[str, list[FilenameMapping]] = defaultdict(list)
    for mapping in mappings:
        filename = mapping["filename"]
        exact_groups[filename].append(mapping)
        folded_groups[filename.casefold()].append(mapping)

    for filename, group in sorted(exact_groups.items()):
        if len(group) > 1:
            add_issue(
                "filename_collision",
                group,
                "Different wiki titles produce the same filename after "
                "slash/colon replacement and Unicode NFC normalization.",
            )

    for _, group in sorted(folded_groups.items()):
        distinct_filenames = {item["filename"] for item in group}
        if len(distinct_filenames) > 1:
            add_issue(
                "case_insensitive_collision",
                group,
                "These filenames differ by case only and can collide on "
                "the default case-insensitive macOS APFS configuration.",
            )

    for title in duplicate_titles:
        duplicate_mapping = next(item for item in mappings if item["title"] == title)
        add_issue(
            "duplicate_source_title",
            [duplicate_mapping],
            "The same non-redirect page title appears more than once in the dump.",
        )

    return {
        "article_pages": len(titles),
        "unique_titles": len(unique_titles),
        "redirects_seen": redirects_seen,
        "redirects_excluded": 0 if include_redirects else redirects_seen,
        "duplicate_source_titles": duplicate_titles,
        "mappings": mappings,
        "issues": issues,
    }


def audit_dump(
    dump_path: Path, *, include_redirects: bool = False
) -> FilenameAuditReport:
    """Extract page names from a dump and audit their proposed filenames."""
    titles, redirects_seen = extract_page_titles(
        dump_path, include_redirects=include_redirects
    )
    return audit_titles(
        titles,
        redirects_seen=redirects_seen,
        include_redirects=include_redirects,
    )
