from typing import TYPE_CHECKING

from pipeline.filename_audit import (
    audit_titles,
    extract_page_titles,
    filename_for_title,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_filename_for_title_preserves_names_and_sanitizes_separators() -> None:
    assert filename_for_title("Rüdiger's Watch") == "Rüdiger's Watch.md"
    assert filename_for_title("Memories/Transcript") == "Memories — Transcript.md"
    assert (
        filename_for_title("Episode 1: Do Not Go Gentle")
        == "Episode 1 - Do Not Go Gentle.md"
    )
    assert (
        filename_for_title("Episode 1:Do Not Go Gentle")
        == "Episode 1 - Do Not Go Gentle.md"
    )
    assert (
        filename_for_title("Episode 1:  Do Not Go Gentle")
        == "Episode 1 - Do Not Go Gentle.md"
    )
    assert filename_for_title("You Fix My Rifle?") == "You Fix My Rifle?.md"


def test_audit_finds_case_insensitive_collision() -> None:
    report = audit_titles(["Piece of Paper", "Piece Of Paper"])

    assert report["unique_titles"] == 2
    assert any(
        issue["kind"] == "case_insensitive_collision" for issue in report["issues"]
    )


def test_audit_finds_sanitization_collision() -> None:
    report = audit_titles(["Chapter/Transcript", "Chapter — Transcript"])

    assert any(issue["kind"] == "filename_collision" for issue in report["issues"])


def test_audit_finds_long_utf8_filename() -> None:
    report = audit_titles(["é" * 130])

    assert any(issue["kind"] == "filename_too_long" for issue in report["issues"])


def test_extract_page_titles_skips_redirects_and_non_article_namespaces(
    tmp_path: Path,
) -> None:
    dump = tmp_path / "sample.xml"
    dump.write_text(
        """<?xml version="1.0"?>
<mediawiki xmlns="http://www.mediawiki.org/xml/export-0.11/">
  <siteinfo />
  <page>
    <title>Bear</title><ns>0</ns>
    <revision><text>Article body</text></revision>
  </page>
  <page>
    <title>Bear alias</title><ns>0</ns>
    <redirect title="Bear" />
    <revision><text>#REDIRECT [[Bear]]</text></revision>
  </page>
  <page>
    <title>Category:Wildlife</title><ns>14</ns>
    <revision><text>Category page</text></revision>
  </page>
</mediawiki>
""",
        encoding="utf-8",
    )

    titles, redirects = extract_page_titles(dump)
    assert titles == ["Bear"]
    assert redirects == 1

    titles_with_redirects, redirects = extract_page_titles(dump, include_redirects=True)
    assert titles_with_redirects == ["Bear", "Bear alias"]
    assert redirects == 1
