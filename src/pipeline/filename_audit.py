"""Audit MediaWiki article titles for portable Markdown filenames.

The audit targets Ubuntu filesystems and the default case-insensitive macOS
APFS configuration. It does not create or rename any files.
"""

from __future__ import annotations

import unicodedata
from collections import Counter, defaultdict
from typing import TYPE_CHECKING, TypedDict, cast

import defusedxml.ElementTree as SafeET

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path
    from xml.etree.ElementTree import Element

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
    stem = stem.replace("/", " — ")
    stem = " - ".join(part.strip() for part in stem.split(":"))
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
    events = cast(
        "Iterator[tuple[str, Element]]",
        SafeET.iterparse(str(dump_path), events=("start", "end")),
    )

    try:
        _, root = next(events)
    except StopIteration as exc:
        msg = f"Wiki dump is empty: {dump_path}"
        raise ValueError(msg) from exc

    for event, element in events:
        if event != "end" or element.tag.rsplit("}", 1)[-1] != "page":
            continue

        fields = {child.tag.rsplit("}", 1)[-1]: child for child in element}
        title_element = fields.get("title")
        namespace_element = fields.get("ns")
        redirect_element = fields.get("redirect")

        title = title_element.text if title_element is not None else None
        namespace = namespace_element.text if namespace_element is not None else None

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


def _append_issue(
    issues: list[FilenameIssue], kind: str, affected: list[FilenameMapping], detail: str
) -> None:
    """Append one normalized filename audit issue."""
    issues.append(
        {
            "kind": kind,
            "titles": sorted(item["title"] for item in affected),
            "filenames": sorted({item["filename"] for item in affected}),
            "detail": detail,
        }
    )


def _check_filename_limits(
    mappings: list[FilenameMapping], issues: list[FilenameIssue]
) -> None:
    """Check filename byte lengths and control characters."""
    for mapping in mappings:
        filename = mapping["filename"]
        encoded_length = len(filename.encode("utf-8"))
        if encoded_length > MAX_FILENAME_BYTES:
            _append_issue(
                issues,
                "filename_too_long",
                [mapping],
                f"Length: {encoded_length} UTF-8 bytes; limit: {MAX_FILENAME_BYTES}.",
            )

        controls = [
            f"U+{ord(char):04X}"
            for char in filename
            if unicodedata.category(char) == "Cc"
        ]
        if controls:
            _append_issue(
                issues,
                "control_character",
                [mapping],
                "Filename contains control character(s): "
                + ", ".join(sorted(set(controls))),
            )


def _check_filename_collisions(
    mappings: list[FilenameMapping], issues: list[FilenameIssue]
) -> None:
    """Find exact and case-insensitive filename collisions."""
    exact_groups: dict[str, list[FilenameMapping]] = defaultdict(list)
    folded_groups: dict[str, list[FilenameMapping]] = defaultdict(list)
    for mapping in mappings:
        filename = mapping["filename"]
        exact_groups[filename].append(mapping)
        folded_groups[filename.casefold()].append(mapping)

    for group in exact_groups.values():
        if len(group) > 1:
            _append_issue(
                issues,
                "filename_collision",
                group,
                "Wiki titles resolve to the same filename after sanitization.",
            )

    for group in folded_groups.values():
        distinct_filenames = {item["filename"] for item in group}
        if len(distinct_filenames) > 1:
            _append_issue(
                issues,
                "case_insensitive_collision",
                group,
                "These filenames differ only by case and may collide on macOS.",
            )


def _check_duplicate_source_titles(
    mappings: list[FilenameMapping],
    duplicate_titles: list[str],
    issues: list[FilenameIssue],
) -> None:
    """Report duplicate non-redirect article titles in the source dump."""
    mappings_by_title = {item["title"]: item for item in mappings}
    for title in duplicate_titles:
        _append_issue(
            issues,
            "duplicate_source_title",
            [mappings_by_title[title]],
            "The same non-redirect page title appears more than once in the dump.",
        )


def audit_titles(
    titles: list[str], *, redirects_seen: int = 0, include_redirects: bool = False
) -> FilenameAuditReport:
    """Build filename mappings and report portability or collision issues."""
    counts = Counter(titles)
    unique_titles = list(dict.fromkeys(titles))
    duplicate_titles = sorted(title for title, count in counts.items() if count > 1)
    mappings: list[FilenameMapping] = [
        {"title": title, "filename": filename_for_title(title)}
        for title in unique_titles
    ]
    issues: list[FilenameIssue] = []

    _check_filename_limits(mappings, issues)
    _check_filename_collisions(mappings, issues)
    _check_duplicate_source_titles(mappings, duplicate_titles, issues)

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
        titles, redirects_seen=redirects_seen, include_redirects=include_redirects
    )
