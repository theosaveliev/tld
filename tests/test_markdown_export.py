from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

import pytest

from pipeline import markdown_export as markdown_export_module
from pipeline.markdown_export import (
    DumpPage,
    RenderStats,
    WikiContext,
    _export_one_page,  # pyright: ignore[reportPrivateUsage]
    _html_fragment_to_markdown,  # pyright: ignore[reportPrivateUsage]
    _iter_dump_pages,  # pyright: ignore[reportPrivateUsage]
    _markdown_label,  # pyright: ignore[reportPrivateUsage]
    _portable_asset_name,  # pyright: ignore[reportPrivateUsage]
    _repair_unclosed_mediawiki_tables,  # pyright: ignore[reportPrivateUsage]
    _rewrite_html_links,  # pyright: ignore[reportPrivateUsage]
    load_filename_mapping,
    normalize_image_name,
    normalize_title,
    resolve_internal_href,
)

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path


class FakeContext:
    """Minimal context implementing the renderer protocol for export tests."""

    def add_page(self, model: str, title: str, text: str) -> object:
        return (model, title, text)

    def analyze_templates(self) -> None:
        pass

    def start_page(self, title: str) -> None:
        pass

    def expand(self, text: str, timeout: int) -> str:
        _ = timeout
        return text


def identity_preprocess(
    text: str,
    context: WikiContext,
    infobox_defs: Mapping[str, object],
    page_title: str,
    stats: RenderStats,
) -> str:
    """Return the input text unchanged using the production callback signature."""
    _ = (context, infobox_defs, page_title, stats)
    return text


def identity_postprocess(text: str, categories: list[str], stats: RenderStats) -> str:
    """Return expanded text unchanged using the production callback signature."""
    _ = (categories, stats)
    return text


def test_markdown_label_escapes_punctuation() -> None:
    assert _markdown_label("The Long Dark (Episode 1) *Test*") == (
        "The Long Dark \\(Episode 1\\) \\*Test\\*"
    )


def test_normalize_title_matches_mediawiki_title_rules() -> None:
    assert normalize_title("the_long_dark") == "The long dark"
    assert normalize_title("  bear%20meat  ") == "Bear meat"
    assert normalize_title("Rüdiger") == "Rüdiger"


def test_normalize_image_name_strips_mediawiki_namespace() -> None:
    assert normalize_image_name("File:Bear Meat.png") == "bear_meat.png"
    assert normalize_image_name("../images/BEAR_Meat.png") == "bear_meat.png"


def test_portable_asset_name_sanitizes_and_limits_image_names() -> None:
    assert _portable_asset_name("Bear: Meat.png") == "Bear - Meat.png"
    assert len(_portable_asset_name("é" * 200 + ".png").encode("utf-8")) <= 255


def test_resolve_internal_href_rewrites_local_page_links() -> None:
    mapping = {"Main Page": "Main Page.md", "Bear": "Bear.md"}

    assert (
        resolve_internal_href("Bear#Habitat", "Main Page", mapping, {})
        == "./Bear.md#Habitat"
    )
    assert (
        resolve_internal_href("Bear#Habitat Area", "Main Page", mapping, {})
        == "./Bear.md#Habitat_Area"
    )
    assert (
        resolve_internal_href(
            "Bear_alias", "Main Page", mapping, {"Bear alias": "Bear"}
        )
        == "./Bear.md"
    )
    assert resolve_internal_href("#History", "Main Page", mapping, {}) == "#History"
    assert (
        resolve_internal_href("#Habitat Area", "Main Page", mapping, {})
        == "#Habitat_Area"
    )


def test_resolve_internal_href_preserves_external_links() -> None:
    mapping = {"Main Page": "Main Page.md"}

    assert (
        resolve_internal_href("https://example.com/page", "Main Page", mapping, {})
        == "https://example.com/page"
    )
    assert (
        resolve_internal_href("mailto:test@example.com", "Main Page", mapping, {})
        == "mailto:test@example.com"
    )
    assert (
        resolve_internal_href("wikipedia:Bear", "Main Page", mapping, {})
        == "wikipedia:Bear"
    )
    assert (
        resolve_internal_href("de:Überleben", "Main Page", mapping, {})
        == "de:Überleben"
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
  <page>
    <title>Bear behavior</title><ns>0</ns>
    <revision>
      <slots>
        <slot role="aux"><model>json</model><text>Auxiliary content</text></slot>
        <slot role="main">
          <model>wikitext</model><text>Slot article body</text>
        </slot>
      </slots>
    </revision>
  </page>
</mediawiki>
""",
        encoding="utf-8",
    )

    pages = list(_iter_dump_pages(dump_file))

    assert pages == [
        DumpPage("Bear", 0, "wikitext", "Bear article", None),
        DumpPage("Bear alias", 0, "wikitext", "#REDIRECT [[Bear]]", "Bear"),
        DumpPage("Bear behavior", 0, "wikitext", "Slot article body", None),
    ]


def test_rewrite_html_links_resolves_articles_and_reports_missing_pages(
    tmp_path: Path,
) -> None:
    missing_links: set[str] = set()
    missing_images: set[str] = set()
    html_fragment = """<p>
<a href="Bear">Bear</a>
<a href="Missing page">Missing page</a>
<a href="https://example.com">External</a>
</p>"""

    rewritten = _rewrite_html_links(
        html_fragment,
        "Main Page",
        {"Bear": "Bear.md"},
        {},
        {},
        None,
        tmp_path / "assets",
        {},
        {},
        missing_links,
        missing_images,
    )

    assert 'href="./Bear.md"' in rewritten
    assert 'href="https://example.com"' in rewritten
    assert "Missing page" in rewritten
    assert missing_links == {"Missing page"}
    assert missing_images == set()


def test_html_rewriter_restores_infoboxes_thumbnails_and_section_ids(
    tmp_path: Path,
) -> None:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    image = image_dir / "Bear.png"
    image.write_bytes(b"image")
    asset_dir = tmp_path / "workspace" / "assets"
    missing_links: set[str] = set()
    missing_images: set[str] = set()
    fragment = """<table><caption>XSENT:infobox:Wolf</caption>
<tr><td>Habitat</td><td>Forest</td></tr></table>
<figure><img src="File:Bear.png" alt="XTHUMB A bear" /></figure>
<h2>Section One</h2><h2>Section Two</h2>
<h2>Section Three</h2><h2>Section Four</h2>"""

    rewritten = _rewrite_html_links(
        fragment,
        "Main Page",
        {},
        {},
        {"bear.png": image},
        image_dir,
        asset_dir,
        {},
        {},
        missing_links,
        missing_images,
    )

    assert '<table class="infobox">' in rewritten
    assert "<th>Habitat</th>" in rewritten
    assert '<figure class="thumb">' in rewritten
    assert "<figcaption>A bear</figcaption>" in rewritten
    assert 'id="Section_One"' in rewritten
    assert 'href="#Section_One"' in rewritten
    assert (asset_dir / "Bear.png").read_bytes() == b"image"
    assert missing_links == set()
    assert missing_images == set()


def test_html_fragment_to_markdown_converts_fandom_elements() -> None:
    fragment = """<div>
<h2>Overview</h2>
<p><strong>Important</strong> <a href="./Bear.md">Bear</a> <span>details</span></p>
<ul><li>First item</li><li>Second <em>item</em></li></ul>
<table class="infobox">
<caption>Bear</caption>
<tbody>
<tr><th>Release</th><td>2017</td></tr>
<tr><td colspan="2" class="section"><strong>Properties</strong></td></tr>
</tbody>
</table>
<figure><img src="../assets/Bear.png" alt="Bear" />
<figcaption>Bear icon</figcaption></figure>
</div>"""

    markdown = _html_fragment_to_markdown(fragment)

    assert "## Overview" in markdown
    assert "**Important** [Bear](./Bear.md) details" in markdown
    assert "- First item" in markdown
    assert "- Second *item*" in markdown
    assert "**Bear**" in markdown
    assert "| Property | Details |" in markdown
    assert "| Release | 2017 |" in markdown
    assert "**Properties**" in markdown
    assert "![Bear](../assets/Bear.png)" in markdown
    assert "*Bear icon*" in markdown
    assert not re.search(r"</?[A-Za-z][A-Za-z0-9-]*(?=\s|/?>)[^>]*>", markdown)


def test_html_fragment_to_markdown_rewrites_mediawiki_heading_anchors() -> None:
    fragment = (
        '<nav><a href="#Section_One">Section One</a></nav>'
        '<h2 id="Section_One">Section One</h2>'
        '<a href="#cite_note-1">[1]</a>'
    )

    markdown = _html_fragment_to_markdown(fragment)

    assert "[Section One](#section-one)" in markdown
    assert "[1](#cite_note-1)" in markdown
    assert "## Section One" in markdown


def test_repair_unclosed_mediawiki_tables_appends_missing_closers() -> None:
    source = """{| class="outer"
| row
{| class="inner"
| nested row
"""
    repaired, count = _repair_unclosed_mediawiki_tables(source)

    assert count == 2
    assert (
        repaired
        == source
        + """|}
|}
"""
    )


def test_repair_unclosed_mediawiki_tables_ignores_literal_and_comment_content() -> None:
    source = """<nowiki>
{|
|}
</nowiki>
<!--
{|
|}
-->
{| class="real"
| row
|}
"""

    repaired, count = _repair_unclosed_mediawiki_tables(source)

    assert repaired == source
    assert count == 0


def test_export_retries_pandoc_after_closing_unterminated_wikitext_table(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_pandoc(
        _pandoc: str, text: str, source_format: str, target_format: str
    ) -> str:
        if source_format == "mediawiki":
            if "|}" not in text:
                raise RuntimeError("unexpected end of input at line 4, column 1")
            return "<p>Recovered article text</p>"
        assert (source_format, target_format) == ("html", "gfm")
        return "Recovered article text"

    def preprocess(
        text: str,
        context: WikiContext,
        infobox_defs: Mapping[str, object],
        page_title: str,
        stats: RenderStats,
    ) -> str:
        _ = (text, context, infobox_defs, page_title, stats)
        return """{| class="infobox"
| an open row"""

    monkeypatch.setattr(markdown_export_module, "_run_pandoc", fake_pandoc)
    pages_dir = tmp_path / "pages"
    pages_dir.mkdir()

    result, expansion_errors, conversion_errors = _export_one_page(
        DumpPage("Sample", 0, "wikitext", "original source", None),
        {"Sample": "Sample.md"},
        FakeContext(),
        preprocess,
        identity_postprocess,
        {},
        "pandoc",
        {},
        {},
        None,
        pages_dir,
        tmp_path / "assets",
        {},
        {},
        set(),
        set(),
        set(),
        None,
    )

    output = (pages_dir / "Sample.md").read_text(encoding="utf-8")
    assert result["status"] == "repaired"
    assert "closing 1 unterminated MediaWiki table" in result["error"]
    assert "Recovered article text" in output
    assert not output.startswith("> ")
    assert expansion_errors == 0
    assert conversion_errors == 0


def test_export_converts_rendered_html_when_gfm_conversion_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_pandoc(
        _pandoc: str, _text: str, source_format: str, target_format: str
    ) -> str:
        if source_format == "mediawiki" and target_format == "html":
            return "<p>Rendered article text</p>"
        raise RuntimeError("simulated HTML-to-GFM failure")

    monkeypatch.setattr(markdown_export_module, "_run_pandoc", fake_pandoc)
    pages_dir = tmp_path / "pages"
    pages_dir.mkdir()

    result, expansion_errors, conversion_errors = _export_one_page(
        DumpPage("Sample", 0, "wikitext", "original source", None),
        {"Sample": "Sample.md"},
        FakeContext(),
        identity_preprocess,
        identity_postprocess,
        {},
        "pandoc",
        {},
        {},
        None,
        pages_dir,
        tmp_path / "assets",
        {},
        {},
        set(),
        set(),
        set(),
        None,
    )

    output = (pages_dir / "Sample.md").read_text(encoding="utf-8")
    assert result["status"] == "html_fallback"
    assert "Rendered article text" in output
    assert "<p>" not in output
    assert not output.startswith("> ")
    assert expansion_errors == 0
    assert conversion_errors == 0


def test_export_converts_html_retained_by_pandoc_to_markdown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_pandoc(
        _pandoc: str, _text: str, source_format: str, target_format: str
    ) -> str:
        if source_format == "mediawiki" and target_format == "html":
            return "<p><strong>Important</strong> article text</p>"
        assert (source_format, target_format) == ("html", "gfm")
        return "<p><strong>Important</strong> article text</p>"

    monkeypatch.setattr(markdown_export_module, "_run_pandoc", fake_pandoc)
    pages_dir = tmp_path / "pages"
    pages_dir.mkdir()

    result, expansion_errors, conversion_errors = _export_one_page(
        DumpPage("Sample", 0, "wikitext", "original source", None),
        {"Sample": "Sample.md"},
        FakeContext(),
        identity_preprocess,
        identity_postprocess,
        {},
        "pandoc",
        {},
        {},
        None,
        pages_dir,
        tmp_path / "assets",
        {},
        {},
        set(),
        set(),
        set(),
        None,
    )

    output = (pages_dir / "Sample.md").read_text(encoding="utf-8")
    assert result["status"] == "html_fallback"
    assert "**Important** article text" in output
    assert "<p>" not in output
    assert "<strong>" not in output
    assert expansion_errors == 0
    assert conversion_errors == 0


def test_export_normalizes_html_in_direct_source_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_pandoc(
        _pandoc: str, _text: str, source_format: str, target_format: str
    ) -> str:
        if source_format == "mediawiki" and target_format == "gfm":
            return "<p><strong>Source fallback</strong> text</p>"
        if source_format == "mediawiki" and target_format == "html":
            return "<p><strong>Source fallback</strong> text</p>"
        raise AssertionError("Unexpected Pandoc conversion")

    def fail_postprocess(
        text: str, categories: list[str], stats: RenderStats
    ) -> str:
        _ = (text, categories, stats)
        raise RuntimeError("simulated postprocess failure")

    monkeypatch.setattr(markdown_export_module, "_run_pandoc", fake_pandoc)
    pages_dir = tmp_path / "pages"
    pages_dir.mkdir()

    result, expansion_errors, conversion_errors = _export_one_page(
        DumpPage("Sample", 0, "wikitext", "original source", None),
        {"Sample": "Sample.md"},
        FakeContext(),
        identity_preprocess,
        fail_postprocess,
        {},
        "pandoc",
        {},
        {},
        None,
        pages_dir,
        tmp_path / "assets",
        {},
        {},
        set(),
        set(),
        set(),
        None,
    )

    output = (pages_dir / "Sample.md").read_text(encoding="utf-8")
    assert result["status"] == "raw_fallback"
    assert "**Source fallback** text" in output
    assert "<p>" not in output
    assert "<strong>" not in output
    assert expansion_errors == 0
    assert conversion_errors == 0


def test_export_renders_original_source_if_expanded_wikitext_stays_invalid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mediawiki_html_inputs: list[str] = []

    def fake_pandoc(
        _pandoc: str, text: str, source_format: str, target_format: str
    ) -> str:
        if source_format == "mediawiki" and target_format == "html":
            mediawiki_html_inputs.append(text)
            if text == "original source":
                return "<p>Original source prose</p>"
            raise RuntimeError("unexpected end of input at line 4, column 1")
        assert (source_format, target_format) == ("html", "gfm")
        return "Original source prose"

    def preprocess(
        text: str,
        context: WikiContext,
        infobox_defs: Mapping[str, object],
        page_title: str,
        stats: RenderStats,
    ) -> str:
        _ = (text, context, infobox_defs, page_title, stats)
        return """{| class="infobox"
| malformed expansion"""

    monkeypatch.setattr(markdown_export_module, "_run_pandoc", fake_pandoc)
    pages_dir = tmp_path / "pages"
    pages_dir.mkdir()

    result, expansion_errors, conversion_errors = _export_one_page(
        DumpPage("Sample", 0, "wikitext", "original source", None),
        {"Sample": "Sample.md"},
        FakeContext(),
        preprocess,
        identity_postprocess,
        {},
        "pandoc",
        {},
        {},
        None,
        pages_dir,
        tmp_path / "assets",
        {},
        {},
        set(),
        set(),
        set(),
        None,
    )

    output = (pages_dir / "Sample.md").read_text(encoding="utf-8")
    assert result["status"] == "raw_fallback"
    assert len(mediawiki_html_inputs) == 3
    assert "Original source prose" in output
    assert not output.startswith("> ")
    assert expansion_errors == 0
    assert conversion_errors == 0
