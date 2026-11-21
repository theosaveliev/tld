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


def test_audit_allocates_case_insensitive_collisions_deterministically() -> None:
    titles = ["Piece of Paper", "Piece Of Paper"]
    report = audit_titles(titles)
    reversed_report = audit_titles(list(reversed(titles)))
    mappings = {item["title"]: item for item in report["mappings"]}

    assert report["unique_titles"] == 2
    assert mappings["Piece Of Paper"]["filename"] == "Piece Of Paper.md"
    assert mappings["Piece of Paper"]["filename"] == "Piece of Paper (2).md"
    assert report["mappings"] == reversed_report["mappings"]
    assert not report["issues"]


def test_audit_allocates_three_way_collision_with_unique_suffixes() -> None:
    report = audit_titles(["Title", "TITLE", "title"])
    filenames = [item["filename"].casefold() for item in report["mappings"]]

    assert len(filenames) == len(set(filenames)) == 3
    assert {item["filename"] for item in report["mappings"]} == {
        "TITLE.md",
        "Title (2).md",
        "title (3).md",
    }


def test_collision_suffix_skips_a_real_page_filename() -> None:
    report = audit_titles(["Piece Of Paper", "Piece of Paper", "Piece of Paper (2)"])
    mappings = {item["title"]: item for item in report["mappings"]}

    assert mappings["Piece Of Paper"]["filename"] == "Piece Of Paper.md"
    assert mappings["Piece of Paper"]["filename"] == "Piece of Paper (3).md"
    assert mappings["Piece of Paper (2)"]["filename"] == "Piece of Paper (2).md"


def test_audit_allocates_sanitization_collisions() -> None:
    report = audit_titles(["Chapter/Transcript", "Chapter — Transcript"])
    filenames = [item["filename"].casefold() for item in report["mappings"]]

    assert len(filenames) == len(set(filenames)) == 2
    assert not report["issues"]


def test_audit_finds_long_utf8_filename() -> None:
    report = audit_titles(["é" * 130])

    assert any(issue["kind"] == "filename_too_long" for issue in report["issues"])


def test_collision_suffix_respects_filename_byte_limit() -> None:
    shared_stem = "é" * 122
    report = audit_titles([f"{shared_stem}/Foo", f"{shared_stem} — Foo"])

    assert len(report["mappings"]) == 2
    assert all(
        len(mapping["filename"].encode("utf-8")) <= 255
        for mapping in report["mappings"]
    )
    assert len({mapping["filename"].casefold() for mapping in report["mappings"]}) == 2


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
