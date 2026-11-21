import json
from typing import TYPE_CHECKING

import pytest

from pipeline.markdown_export import (
    _iter_dump_pages,  # pyright: ignore[reportPrivateUsage]
    DumpPage,
    load_filename_mapping,
    normalize_image_name,
    normalize_title,
    resolve_internal_href,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_normalize_title_matches_mediawiki_title_rules() -> None:
    assert normalize_title("the_long_dark") == "The long dark"
    assert normalize_title("  bear%20meat  ") == "Bear meat"
    assert normalize_title("Rüdiger") == "Rüdiger"


def test_normalize_image_name_strips_mediawiki_namespace() -> None:
    assert normalize_image_name("File:Bear Meat.png") == "bear_meat.png"
    assert normalize_image_name("../images/BEAR_Meat.png") == "bear_meat.png"


def test_resolve_internal_href_rewrites_local_page_links() -> None:
    mapping = {
        "Main Page": "Main Page.md",
        "Bear": "Bear.md",
    }

    assert (
        resolve_internal_href("Bear#Habitat", "Main Page", mapping, {})
        == "./Bear.md#Habitat"
    )
    assert (
        resolve_internal_href(
            "Bear_alias", "Main Page", mapping, {"Bear alias": "Bear"}
        )
        == "./Bear.md"
    )
    assert resolve_internal_href("#History", "Main Page", mapping, {}) == "#History"


def test_resolve_internal_href_preserves_external_links() -> None:
    mapping = {"Main Page": "Main Page.md"}

    assert (
        resolve_internal_href(
            "https://example.com/page", "Main Page", mapping, {}
        )
        == "https://example.com/page"
    )
    assert (
        resolve_internal_href("mailto:test@example.com", "Main Page", mapping, {})
        == "mailto:test@example.com"
    )


def test_resolve_internal_href_returns_none_for_unknown_wiki_page() -> None:
    assert resolve_internal_href("Missing page", "Main Page", {}, {}) is None
    assert resolve_internal_href("Special:RecentChanges", "Main Page", {}, {}) is None


def test_filename_mapping_loads_audited_entries(tmp_path: Path) -> None:
    mapping_file = tmp_path / "mapping.json"
    mapping_file.write_text(
        json.dumps(
            {
                "mappings": [
                    {
                        "title": "Piece Of Paper",
                        "original_filename": "Piece Of Paper.md",
                        "filename": "Piece Of Paper.md",
                    },
                    {
                        "title": "Piece of Paper",
                        "original_filename": "Piece of Paper.md",
                        "filename": "Piece of Paper (2).md",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    mapping = load_filename_mapping(mapping_file)

    assert mapping == {
        "Piece Of Paper": "Piece Of Paper.md",
        "Piece of Paper": "Piece of Paper (2).md",
    }


def test_filename_mapping_rejects_case_insensitive_collisions(tmp_path: Path) -> None:
    mapping_file = tmp_path / "mapping.json"
    mapping_file.write_text(
        json.dumps(
            {
                "mappings": [
                    {"title": "Bear", "filename": "Bear.md"},
                    {"title": "Bear meat", "filename": "bear.md"},
                ]
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Filename collision"):
        load_filename_mapping(mapping_file)


def test_filename_mapping_rejects_path_traversal(tmp_path: Path) -> None:
    mapping_file = tmp_path / "mapping.json"
    mapping_file.write_text(
        json.dumps({"mappings": [{"title": "Bear", "filename": "../Bear.md"}]}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Unsafe Markdown filename"):
        load_filename_mapping(mapping_file)

def test_iter_dump_pages_extracts_page_text_and_redirects(tmp_path: Path) -> None:
    dump_file = tmp_path / "dump.xml"
    dump_file.write_text(
        """<?xml version="1.0"?>
<mediawiki>
  <page>
    <title>Bear</title><ns>0</ns>
    <revision><model>wikitext</model><text>Bear article</text></revision>
  </page>
  <page>
    <title>Bear alias</title><ns>0</ns><redirect title="Bear" />
    <revision><text>#REDIRECT [[Bear]]</text></revision>
  </page>
</mediawiki>
""",
        encoding="utf-8",
    )

    pages = list(_iter_dump_pages(dump_file))

    assert pages == [
        DumpPage("Bear", 0, "wikitext", "Bear article", None),
        DumpPage("Bear alias", 0, "wikitext", "#REDIRECT [[Bear]]", "Bear"),
    ]


