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
    _build_image_index,  # pyright: ignore[reportPrivateUsage]
    _build_material_usage_index,  # pyright: ignore[reportPrivateUsage]
    _convert_html_to_markdown,  # pyright: ignore[reportPrivateUsage]
    _copy_image,  # pyright: ignore[reportPrivateUsage]
    _expand_page,  # pyright: ignore[reportPrivateUsage]
    _export_one_page,  # pyright: ignore[reportPrivateUsage]
    _html_fragment_to_markdown,  # pyright: ignore[reportPrivateUsage]
    _iter_dump_pages,  # pyright: ignore[reportPrivateUsage]
    _markdown_label,  # pyright: ignore[reportPrivateUsage]
    _page_markdown,  # pyright: ignore[reportPrivateUsage]
    _portable_asset_name,  # pyright: ignore[reportPrivateUsage]
    _render_mediawiki_html,  # pyright: ignore[reportPrivateUsage]
    _repair_unclosed_mediawiki_tables,  # pyright: ignore[reportPrivateUsage]
    _restore_deferred_material_tables_in_html,  # pyright: ignore[reportPrivateUsage]
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


def test_page_markdown_has_no_yaml_and_keeps_readable_metadata() -> None:
    fence = chr(96) * 3
    body = (
        "## Gallery\n\n"
        "*Midnight Badge.*\n\n"
        "**Lua execution error in Module:Navbox function navbox**"
        "[ru:4 Days fo Night](ru:4_Days_fo_Night)\n\n"
        f"{fence}text\n*literal asterisks*\n{fence}\n"
    )
    markdown = _page_markdown(
        "4 Days of Night Event",
        ["Challenges", "Events"],
        body,
        "https://thelongdark.fandom.com",
    )

    assert markdown.startswith("# 4 Days of Night Event\n")
    assert not markdown.startswith("---")
    assert "wiki_namespace:" not in markdown
    assert "**Categories:** Challenges, Events" in markdown
    assert (
        "**Source:** [Fandom]("
        "https://thelongdark.fandom.com/wiki/4_Days_of_Night_Event)"
    ) in markdown
    assert "_Midnight Badge._" in markdown
    assert "*Midnight Badge.*" not in markdown
    assert "Lua execution error" not in markdown
    assert "[ru:4 Days fo Night]" not in markdown
    assert f"{fence}text\n*literal asterisks*\n{fence}" in markdown


def test_normalize_title_matches_mediawiki_title_rules() -> None:
    assert normalize_title("the_long_dark") == "The long dark"
    assert normalize_title("  bear%20meat  ") == "Bear meat"
    assert normalize_title("Rüdiger") == "Rüdiger"


def test_normalize_image_name_strips_mediawiki_namespace() -> None:
    assert normalize_image_name("File:Bear Meat.png") == "bear_meat.png"
    assert normalize_image_name("../images/BEAR_Meat.png") == "bear_meat.png"


def test_image_index_uses_mismatch_directory_as_fallback(tmp_path: Path) -> None:
    image_dir = tmp_path / "images"
    mismatch_dir = tmp_path / "images_mismatch"
    image_dir.mkdir()
    mismatch_dir.mkdir()
    primary_image = image_dir / "Bear.png"
    primary_image.write_bytes(b"primary")
    (mismatch_dir / "Bear.png").write_bytes(b"mismatch")
    fallback_image = mismatch_dir / "Wolf.jpg"
    fallback_image.write_bytes(b"fallback")

    image_index = _build_image_index(image_dir)

    assert image_index["bear.png"] == primary_image
    assert image_index["wolf.jpg"] == fallback_image

    asset_dir = tmp_path / "workspace" / "assets"
    rewritten = _rewrite_html_links(
        '<img src="File:Wolf.jpg" alt="Wolf" />',
        "Main Page",
        {},
        {},
        image_index,
        image_dir,
        asset_dir,
        {},
        {},
        set(),
        set(),
    )

    assert 'src="../assets/Wolf.jpg"' in rewritten
    assert (asset_dir / "Wolf.jpg").read_bytes() == b"fallback"


def test_copy_image_adds_a_detected_suffix_to_extensionless_jpegs(
    tmp_path: Path,
) -> None:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    source = image_dir / "The_Long_Dark_Image"
    source.write_bytes(b"\xff\xd8\xff\xe0jpeg data")
    asset_dir = tmp_path / "workspace" / "assets"

    copied_name = _copy_image(
        source.name,
        {normalize_image_name(source.name): source},
        image_dir,
        asset_dir,
        {},
        {},
    )

    assert copied_name == "The_Long_Dark_Image.jpg"
    assert (asset_dir / copied_name).read_bytes() == source.read_bytes()


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


def test_convert_html_gallery_to_standalone_markdown_images(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_pandoc(
        _pandoc: str, _text: str, _source_format: str, _target_format: str
    ) -> str:
        raise AssertionError("Gallery conversion should use the Markdown renderer")

    monkeypatch.setattr(markdown_export_module, "_run_pandoc", fail_pandoc)
    fragment = """<table class="gallery"><tbody><tr>
<td><img src="../assets/Badge.png" alt="Badge" /> /
<em>Midnight Badge</em></td>
<td><img src="../assets/Wolf.jpg" alt="Wolf" /> /
<em>Demon wolf</em></td>
</tr></tbody></table>"""

    markdown, note, failed = _convert_html_to_markdown("pandoc", fragment, "")

    assert not failed
    assert not note
    assert "![Badge](../assets/Badge.png)\n\n_Midnight Badge_" in markdown
    assert "![Wolf](../assets/Wolf.jpg)\n\n_Demon wolf_" in markdown
    assert "| Column 1 |" not in markdown
    assert "<table" not in markdown


def test_page_markdown_keeps_gallery_heading_with_nested_image_subsection() -> None:
    body = """## Contents

- [Gallery](#gallery)

## Gallery

### Photos

![Badge](../assets/Badge.png)

## Update history

Added to the game.
"""

    markdown = _page_markdown("Sample", [], body, None)

    assert "- [Gallery](#gallery)" in markdown
    assert "## Gallery" in markdown
    assert "### Photos" in markdown
    assert "![Badge](../assets/Badge.png)" in markdown


def test_page_markdown_removes_empty_gallery_before_next_sibling_section() -> None:
    body = """## Contents

- [Gallery](#gallery)

## Gallery

## Update history

Added to the game.
"""

    markdown = _page_markdown("Sample", [], body, None)

    assert "- [Gallery](#gallery)" not in markdown
    assert "## Gallery" not in markdown
    assert "## Update history" in markdown


def test_recipe_infobox_renders_skill_time_and_material_pairs() -> None:
    fragment = (
        '<table class="wikitable"><tbody>\n'
        '<tr><td><a href="./Gunsmithing.md">'
        '<img src="../assets/Gunsmithing.png" alt="G" />'
        "Gunsmithing</a></td><td></td></tr>\n"
        '<tr><td colspan="2">1</td></tr>\n'
        '<tr><td colspan="2">5 minutes</td></tr>\n'
        '<tr><td colspan="2"><strong>Requirements</strong></td></tr>\n'
        "<tr><td>Facility</td><td>Template:iconlink</td></tr>\n"
        '<tr><td colspan="2"><img src="../assets/Materials.png" alt="" />'
        "<strong></strong></td></tr>\n"
        '<tr><td>Item</td><td><a href="./Bullet.md">'
        '<img src="../assets/Bullet.png" alt="B" />'
        "Bullet</a></td></tr>\n"
        "<tr><td>Quantity</td><td>1</td></tr>\n"
        '<tr><td colspan="2">Template:iconlink plain</td></tr>\n'
        '<tr><td colspan="2"><a href="./Casing.md">'
        '<img src="../assets/Casing.png" alt="C" />'
        "Revolver Shell Casing</a></td></tr>\n"
        '<tr><td colspan="2">1</td></tr>\n'
        '<tr><td colspan="2">Template:iconlink plain</td></tr>\n'
        '<tr><td colspan="2"><a href="./Can_of_Gunpowder.md">'
        '<img src="../assets/Gunpowder.png" alt="G" />'
        "Can of Gunpowder</a></td></tr>\n"
        '<tr><td colspan="2">0.01 kg</td></tr>\n'
        "</tbody></table>"
    )

    markdown = _html_fragment_to_markdown(fragment)

    assert "**Skill:** [Gunsmithing](./Gunsmithing.md) (Level 1)" in markdown
    assert "**Crafting time:** 5 minutes" in markdown
    assert "**Materials**" in markdown
    assert "| Item | Quantity |" in markdown
    assert "| [Bullet](./Bullet.md) | 1 |" in markdown
    assert "| [Revolver Shell Casing](./Casing.md) | 1 |" in markdown
    assert "| [Can of Gunpowder](./Can_of_Gunpowder.md) | 0.01 kg |" in markdown
    assert "Template:" not in markdown
    assert "![" not in markdown
    assert "| Property | Details |" not in markdown
    assert "**Requirements**" not in markdown


def test_background_styled_transcript_div_is_not_rendered_as_bold() -> None:
    fragment = """<div style="background: #2D3035; padding: 2em;">
Find me at the mine. East and up the hill, looking up at the mountain.
I got a lead on this door.
<br/><br/>
-- M (Who is M?)
</div>"""

    markdown = _html_fragment_to_markdown(fragment)

    assert "Find me at the mine." in markdown
    assert "-- M (Who is M?)" in markdown
    assert "**Find me at the mine." not in markdown
    assert "**-- M" not in markdown


def test_material_usage_index_reads_recipe_infoboxes() -> None:
    pages = [
        DumpPage(
            "Meat Curing Box",
            0,
            "wikitext",
            """{{Infobox recipe
|name = Meat Curing Box
|mat1 = Fir Firewood
|mat1qty = 10
|mat2 = Salt
|mat2qty = 8.00 kg
}}""",
            None,
        ),
        DumpPage(
            "Coffee Table",
            0,
            "wikitext",
            """{{Infobox recipe|name=Coffee Table|mat1=Fir Firewood|mat1qty=6}}""",
            None,
        ),
    ]

    usage = _build_material_usage_index(pages)

    assert [(row[0], row[2]) for row in usage["fir firewood"]] == [
        ("Coffee Table", [("6", "Fir Firewood")]),
        ("Meat Curing Box", [("10", "Fir Firewood"), ("8.00 kg", "Salt")]),
    ]


def test_restore_deferred_material_tables_binds_each_table_value() -> None:
    html_fragment = "<p>FIRSTTOKEN</p><p>SECONDTOKEN</p>"
    deferred_tables = {
        "FIRSTTOKEN": '<table id="first"></table>',
        "SECONDTOKEN": '<table id="second"></table>',
    }

    restored = _restore_deferred_material_tables_in_html(html_fragment, deferred_tables)

    assert restored == '<table id="first"></table><table id="second"></table>'


def test_uses_material_list_template_is_replaced_with_recipe_table(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pages = [
        DumpPage(
            "Meat Curing Box",
            0,
            "wikitext",
            """{{Infobox recipe
|name = Meat Curing Box
|mat1 = Fir Firewood
|mat1qty = 10
|mat2 = Salt
|mat2qty = 8.00 kg
}}""",
            None,
        )
    ]
    usage = _build_material_usage_index(pages)
    icon_path = tmp_path / "Meat_Curing_Box_icon.png"
    icon_path.write_bytes(b"test image")
    image_index = {normalize_image_name(icon_path.name): icon_path}

    class ContextThatRejectsGeneratedHtml(FakeContext):
        def expand(self, text: str, timeout: int) -> str:
            assert "<table" not in text
            assert "<a href=" not in text
            return super().expand(text, timeout)

    expanded, error, count, deferred_tables = _expand_page(
        DumpPage("Fir Firewood", 0, "wikitext", "{{Uses material list}}", None),
        ContextThatRejectsGeneratedHtml(),
        identity_preprocess,
        {},
        {
            "infoboxes": 0,
            "missing_templates": [],
            "infobox_left": 0,
            "missing_images": [],
            "redlinks": 0,
            "unrendered": 0,
        },
        usage,
    )

    assert not error
    assert count == 0
    assert "<table" not in expanded
    assert "<a href=" not in expanded
    assert len(deferred_tables) == 1
    token = next(iter(deferred_tables))

    def render_placeholder(
        _pandoc: str, text: str, source_format: str, target_format: str
    ) -> str:
        assert source_format == "mediawiki"
        assert target_format == "html"
        assert token in text
        return f"<p>{token}</p>"

    monkeypatch.setattr(markdown_export_module, "_run_pandoc", render_placeholder)
    html_fragment, status, note = _render_mediawiki_html(
        DumpPage("Fir Firewood", 0, "wikitext", "{{Uses material list}}", None),
        expanded,
        "unused",
        deferred_tables,
    )

    assert status == "ok"
    assert not note
    assert 'class="wikitable uses-material-list"' in html_fragment
    assert "<p>TLDGENERATEDMATERIALLIST" not in html_fragment

    rewritten_html = _rewrite_html_links(
        html_fragment,
        "Fir Firewood",
        {
            normalize_title("Fir Firewood"): "Fir Firewood.md",
            normalize_title("Meat Curing Box"): "Meat Curing Box.md",
            normalize_title("Salt"): "Salt.md",
        },
        {},
        image_index,
        tmp_path,
        tmp_path / "assets",
        {},
        {},
        set(),
        set(),
    )
    markdown, note, failed = _convert_html_to_markdown("unused", rewritten_html, "")

    assert not note
    assert not failed
    assert "| Item | Skills | Materials |" in markdown
    assert ("[Meat Curing Box](./Meat%20Curing%20Box.md)") in markdown
    assert "Meat_Curing_Box_icon.png" not in markdown
    assert not (tmp_path / "assets" / "Meat_Curing_Box_icon.png").exists()
    assert "[Meat Curing Box](./Meat%20Curing%20Box.md)" in markdown
    assert "[Salt](./Salt.md)" in markdown
    assert "8.00 kg" in markdown
    assert not re.search(r"</?a\b|<img\b", markdown, re.IGNORECASE)
    assert "&lt;a" not in markdown


def test_recovered_html_tables_use_custom_renderer_after_table_repair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_pandoc(
        _pandoc: str, _text: str, _source_format: str, _target_format: str
    ) -> str:
        raise AssertionError("Recovered custom tables should use the Markdown renderer")

    monkeypatch.setattr(markdown_export_module, "_run_pandoc", fail_pandoc)
    fragment = """<table><tbody><tr>
<td><img src="../assets/Ptarmigan_icon.png" alt="Ptarmigan_icon.png" /></td>
<td><strong>DLC-Exclusive content.</strong></td>
</tr></tbody></table>"""
    recovery_note = (
        "Recovered from Pandoc's unexpected end of input by closing "
        "1 unterminated MediaWiki table(s)."
    )

    markdown, note, failed = _convert_html_to_markdown(
        "pandoc", fragment, recovery_note
    )

    assert not failed
    assert note == recovery_note
    assert "![](../assets/Ptarmigan_icon.png)" in markdown
    assert "**DLC-Exclusive content.**" in markdown
    assert "Ptarmigan\\_icon.png" not in markdown


def test_wide_sparse_table_keeps_product_rows_and_compacts_columns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_pandoc(
        _pandoc: str, _text: str, _source_format: str, _target_format: str
    ) -> str:
        raise AssertionError("Wide sparse tables should use the Markdown renderer")

    monkeypatch.setattr(markdown_export_module, "_run_pandoc", fail_pandoc)
    fragment = """<table class="wikitable"><tbody>
<tr><td></td><td></td><td></td><td></td><td></td><td></td>
<td></td><td></td><td></td><td></td><td></td><td></td>
<th>Item</th><th>Skills</th><th>Materials</th></tr>
<tr><td></td><td></td><td></td><td></td><td></td><td></td>
<td></td><td></td><td></td><td></td><td></td><td></td>
<td>Meat Curing Box</td><td>None</td>
<td><ul><li>10 &#215; Fir Firewood</li><li>8 &#215; Salt</li></ul></td></tr>
</tbody></table>"""

    markdown, note, failed = _convert_html_to_markdown("pandoc", fragment, "")

    assert not failed
    assert not note
    assert "| Item | Skills | Materials |" in markdown
    assert "Meat Curing Box" in markdown
    assert "10 \u00d7 Fir Firewood" in markdown
    assert "8 \u00d7 Salt" in markdown
    assert "Column 1" not in markdown
    assert markdown.startswith("| Item | Skills | Materials |")


def test_image_bearing_infobox_renders_images_outside_pipe_tables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_pandoc(
        _pandoc: str, _text: str, _source_format: str, _target_format: str
    ) -> str:
        raise AssertionError("Image-bearing tables should use the Markdown renderer")

    monkeypatch.setattr(markdown_export_module, "_run_pandoc", fail_pandoc)
    fragment = r"""<table class="infobox"><tbody>
<tr><td colspan="2"><img src="../assets/Balaclava_icon.png" alt="" /> /
<em>Balaclava_icon.png</em></td></tr>
<tr><td colspan="2"><strong>General</strong></td></tr>
<tr><th>Item Type</th><td>Clothing\|-</td></tr>
<tr><td colspan="2"><strong>Statistics</strong></td></tr>
<tr><th>Weight</th>
<td><img src="../assets/Weight_icon.png" alt="weight_icon.png" />0.25 kg</td></tr>
</tbody></table>"""

    markdown, note, failed = _convert_html_to_markdown("pandoc", fragment, "")

    assert not failed
    assert not note
    assert "![](../assets/Balaclava_icon.png)" in markdown
    assert "_Balaclava\\_icon.png_" not in markdown
    assert "**General**" in markdown
    assert "**Item Type:** Clothing" in markdown
    assert "**Statistics**" in markdown
    expected_weight = (
        "**Weight:** ![](../assets/Weight_icon.png)0.25 kg"
    )
    assert expected_weight in markdown
    assert "| Property | Details |" not in markdown
    assert r"\|-" not in markdown


def test_image_bearing_layout_table_renders_images_and_quote_as_blocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_pandoc(
        _pandoc: str, _text: str, _source_format: str, _target_format: str
    ) -> str:
        raise AssertionError("Image-bearing tables should use the Markdown renderer")

    monkeypatch.setattr(markdown_export_module, "_run_pandoc", fail_pandoc)
    fragment = """<table><tbody>
<tr><td><img src="../assets/Far_range_line_first_look.png" alt="" /> /
<em>First look</em></td><td></td><td></td></tr>
<tr><td colspan="3"><strong>Location on Great Bear Island</strong></td></tr>
<tr><td><img src="../assets/Far_Range_Branch_Line_Location.png" alt="" /></td>
<td></td><td></td></tr>
<tr><td colspan="3"><strong>Details</strong></td></tr>
<tr><td>Description</td><td>A railway line in the mountains</td><td></td></tr>
<tr><td>“</td>
<td>Travel down the Far Range Branch Line. / Official Marketing Description</td>
<td>”</td></tr>
</tbody></table>"""

    markdown, note, failed = _convert_html_to_markdown("pandoc", fragment, "")

    assert not failed
    assert not note
    assert "Far_range_line_first_look.png" in markdown
    assert "Far_Range_Branch_Line_Location.png" in markdown
    assert "**Location on Great Bear Island**" in markdown
    assert "**Description:** A railway line in the mountains" in markdown
    expected_quote = (
        "> “Travel down the Far Range Branch Line. / Official Marketing Description”"
    )
    assert expected_quote in markdown
    assert "| Column 1 |" not in markdown
    assert "<table" not in markdown


def test_html_fragment_to_markdown_converts_wikitext_strong_markers() -> None:
    markdown = _html_fragment_to_markdown(
        "<p>__Full wool head covering with holes for the eyes.__ "
        "and __gear_hardwood__</p>"
    )

    assert "**Full wool head covering with holes for the eyes.**" in markdown
    assert "__Full wool" not in markdown
    assert r"**gear\_hardwood**" in markdown


def test_html_strong_formatting_handles_blank_lines_from_breaks() -> None:
    fragment = """<p><strong>First statement.<br/><br/>Second statement.</strong></p>"""

    markdown = _html_fragment_to_markdown(fragment)

    assert "**First statement.**" in markdown
    assert "**Second statement.**" in markdown
    assert "**First statement.  \n  \nSecond statement.**" not in markdown


def test_page_markdown_converts_leaked_quote2_tables_to_blockquotes() -> None:
    body = (
        '**Template:quote2**{| style="margin:auto" '
        '| style="vertical-align:top; font-size:3em; color:#5d5980; '
        'padding-right:10px" |“ | **A quotation spanning multiple lines.\n\n'
        "The second sentence.**\n\n"
        "Speaker Name\n"
        '| style="vertical-align:bottom; font-size:3em; color:#5d5980; '
        'padding-left:10px" |” |} **Following paragraph.**\n'
    )

    markdown = _page_markdown("Sample", [], body, None)

    assert "> **A quotation spanning multiple lines." in markdown
    assert "> The second sentence.**" in markdown
    assert "> — Speaker Name" in markdown
    assert "\n\n**Following paragraph.**" in markdown
    assert "Template:quote2" not in markdown
    assert "{|" not in markdown
    assert "|}" not in markdown


def test_page_markdown_removes_inline_wikitext_table_wrappers() -> None:
    body = (
        r'Details \|} after text {\| class="" '
        r'style="background-color:transparent; color:inherit; width:100%;"'
    )

    markdown = _page_markdown("Sample", [], body, None)

    assert "|}" not in markdown
    assert "{|" not in markdown
    assert "background-color:transparent" not in markdown
    assert "Details  after text" in markdown


def test_html_wrapper_table_renders_mixed_block_content_without_flattening() -> None:
    fragment = """<table><tbody><tr><td>
<p>Port Remnant introduction.</p>
<h2>Overview</h2>
<p>First overview paragraph.</p>
<table class="infobox"><tbody>
<tr><td>Details</td><td>Abandoned port</td></tr>
</tbody></table>
</td></tr></tbody></table>"""

    markdown = _html_fragment_to_markdown(fragment)

    assert "Port Remnant introduction." in markdown
    assert "## Overview" in markdown
    assert "First overview paragraph." in markdown
    assert "| Property | Details |" in markdown
    assert "| Details | Abandoned port |" in markdown
    assert " / ## Overview / " not in markdown


def test_html_table_with_list_cells_renders_as_markdown_lists() -> None:
    fragment = """<table><tbody>
<tr><td><ul>
<li><a href="./Barn.md">Barn</a></li>
<li><a href="./Burned_Ridge_Cave.md">Burned Ridge Cave</a></li>
<li><a href="./Bunker_Beta.md">Bunker Beta</a></li>
</ul></td></tr>
</tbody></table>"""

    markdown, note, failed = _convert_html_to_markdown("pandoc", fragment, "")

    assert not failed
    assert not note
    assert "- [Barn](./Barn.md)" in markdown
    assert "- [Burned Ridge Cave](./Burned_Ridge_Cave.md)" in markdown
    assert "- [Bunker Beta](./Bunker_Beta.md)" in markdown
    assert " / " not in markdown
    assert "| Column 1 |" not in markdown


def test_multi_column_list_table_omits_redundant_image_filename_caption() -> None:
    fragment = """<table><tbody>
<tr><th>Image</th><th>Item</th><th>Notes</th></tr>
<tr>
<td><img src="../assets/Canned_Corn_icon.png" alt="Canned_Corn_icon.png" /> /
<em>Canned_Corn_icon.png</em></td>
<td>Canned Corn</td>
<td><ul><li>Food item</li><li>Trader inventory</li></ul></td>
</tr>
<tr>
<td><img src="../assets/Water_%28Potable%29_icon.png"
alt="Water_(Potable)_icon.png" /> / <em>
Water_(Potable)_icon.png</em></td>
<td>Water (Potable)</td>
<td>Ingredient</td>
</tr>
</tbody></table>"""

    markdown = _html_fragment_to_markdown(fragment)

    assert "![](../assets/Canned_Corn_icon.png)" in markdown
    assert " / _Canned\\_Corn\\_icon.png_" not in markdown
    assert "![Canned\\_Corn\\_icon.png]" not in markdown
    assert "| Canned Corn |" in markdown
    assert "![](../assets/Water_%28Potable%29_icon.png)" in markdown
    assert "![Water\\_(Potable)\\_icon.png]" not in markdown


def test_image_bearing_multi_column_list_table_preserves_line_breaks() -> None:
    fragment = """<table><tbody>
<tr><th>Image</th><th>Item</th><th>Details</th></tr>
<tr>
<td><img src="../assets/Badge_icon.png" alt="Badge_icon.png" /></td>
<td>Badge</td>
<td><ul><li>First detail</li><li>Second detail</li></ul></td>
</tr>
</tbody></table>"""

    markdown = _html_fragment_to_markdown(fragment)

    assert "| Image | Item | Details |" in markdown
    expected = (
        "| ![](../assets/Badge_icon.png) | Badge | "
        "- First detail<br>- Second detail |"
    )
    assert expected in markdown
    assert " / - First detail / - Second detail" not in markdown


def test_multi_column_table_with_long_lists_keeps_list_line_breaks() -> None:
    fragment = """<table><tbody>
<tr><th>Region</th><th>Locations</th></tr>
<tr><td>Sample Region</td><td><ul>
<li>Location 1</li><li>Location 2</li><li>Location 3</li>
<li>Location 4</li><li>Location 5</li><li>Location 6</li>
<li>Location 7</li><li>Location 8</li><li>Location 9</li>
</ul></td></tr>
</tbody></table>"""

    markdown = _html_fragment_to_markdown(fragment)

    assert "| Region | Locations |" in markdown
    assert "| Sample Region | - Location 1<br>- Location 2" in markdown
    assert "- Location 9 |" in markdown
    assert " / - Location 1" not in markdown


def test_multi_column_html_table_with_list_cells_keeps_table_structure() -> None:
    fragment = """<table><tbody>
<tr><th>Weather</th><th>Effects</th></tr>
<tr><td>Clear</td><td><ul><li>Warm.</li><li>High visibility.</li></ul></td></tr>
</tbody></table>"""

    markdown, note, failed = _convert_html_to_markdown("pandoc", fragment, "")

    assert not failed
    assert not note
    assert "| Weather | Effects |" in markdown
    assert "| Clear | - Warm. / - High visibility. |" in markdown


def test_page_markdown_removes_additional_leaked_template_labels() -> None:
    body = """==References==
References are listed below.
**Template:update history**
**Template:reflist**
**Template:quote2**A **Torch** is a craftable tool.
25.00 kg / _(55 lb)_**Template:pic link**
"""

    markdown = _page_markdown("Sample", [], body, None)

    assert "Template:" not in markdown
    assert "## References" in markdown
    assert "==References==" not in markdown
    assert "A **Torch** is a craftable tool." in markdown
    assert "25.00 kg / _(55 lb)_" in markdown


def test_html_wrapper_table_renders_nested_table_without_flattening() -> None:
    fragment = """<table><tbody><tr><td>
<p>Before the nested table.</p>
<table class="wikitable"><tbody>
<tr><th>Name</th><th>Details</th></tr>
<tr><td>Revolver</td><td>Cartridge</td></tr>
</tbody></table>
<p>After the nested table.</p>
</td></tr></tbody></table>"""

    markdown = _html_fragment_to_markdown(fragment)

    assert "Before the nested table." in markdown
    assert "| Name | Details |" in markdown
    assert "| Revolver | Cartridge |" in markdown
    assert "After the nested table." in markdown
    assert "Before the nested table. /" not in markdown


def test_page_markdown_strips_wikitext_table_row_artifacts() -> None:
    body = r"**Item type:** [Collectibles](./Collectibles.md)\|-"

    markdown = _page_markdown("Sample", [], body, None)

    assert r"\|-" not in markdown
    assert "**Item type:** [Collectibles](./Collectibles.md)" in markdown


def test_page_markdown_removes_leaked_template_and_wikitext_artifacts() -> None:
    body = r"""## Contents

- [Gallery](#gallery)

## Recipe

| Item | Details |
| --- | --- |
| **Template:pic link plain**[Cooking](./Cooking.md)** | |
| **Template:iconlink plain** |
|  | - |
|  | - |

- **Template:iconlink**, wooden matches used as a temporary light source
- **Template:iconlink plain**
- **Template:stub** A stub article awaiting details.

\|}{\| style="margin:auto"

\[\[File: / ![Box](../assets/Box.png) / _Box_ / Box icon.png\|250px\]\]
![Box](../assets/Box.png)

-
-

## Gallery
"""

    markdown = _page_markdown("Sample", [], body, None)

    assert "[Cooking](./Cooking.md)" in markdown
    assert "wooden matches used as a temporary light source" in markdown
    assert "Template:" not in markdown
    assert "margin:auto" not in markdown
    assert "File:" not in markdown
    assert "|  | - |" not in markdown
    assert "|  |" not in markdown
    assert "\n- \n" not in markdown
    assert "## Gallery" not in markdown
    assert "- [Gallery](#gallery)" not in markdown
    assert "![Box](../assets/Box.png)" in markdown


def test_adjacent_strong_elements_keep_a_word_boundary() -> None:
    fragment = (
        "<p><strong>Spoiler Warning:</strong>"
        "<strong>This page may contain spoilers from the "
        '<a href="./Wintermute.md">story</a> of The Long Dark.</strong></p>'
    )

    markdown = _html_fragment_to_markdown(fragment)

    assert "**Spoiler Warning:** **This page may contain spoilers from the" in markdown
    assert "**Spoiler Warning:****This page" not in markdown


def test_page_markdown_separates_adjacent_bold_spans() -> None:
    body = "**Spoiler Warning:****This page may contain spoilers from the story.**"

    markdown = _page_markdown("Sample", [], body, None)

    assert "**Spoiler Warning:** **This page may contain spoilers" in markdown
    assert "**Spoiler Warning:****This page" not in markdown


def test_page_markdown_uppercases_first_letter_of_all_headings() -> None:
    body = """## trivia
### gameplay
#### **notes and tips**
## [survival guide](./survival.md)
### _wildlife_ and plants
## überleben
```markdown
## leave this code example unchanged
```
"""

    markdown = _page_markdown("Sample", [], body, None)

    assert "## Trivia" in markdown
    assert "### Gameplay" in markdown
    assert "#### **Notes and tips**" in markdown
    assert "## [Survival guide](./survival.md)" in markdown
    assert "### _Wildlife_ and plants" in markdown
    assert "## Überleben" in markdown
    assert "## leave this code example unchanged" in markdown


def test_page_markdown_normalizes_strong_underscores() -> None:
    markdown = _page_markdown("Sample", [], "__Bold text__", None)

    assert "**Bold text**" in markdown
    assert "__Bold text__" not in markdown


def test_rewrite_html_links_removes_orphaned_language_interwiki_targets(
    tmp_path: Path,
) -> None:
    rewritten = _rewrite_html_links(
        '<p><a href="ru:Russian_article">ru:Russian_article</a></p>',
        "Blackrock Memo",
        {},
        {},
        {},
        None,
        tmp_path / "assets",
        {},
        {},
        set(),
        set(),
    )

    assert "ru:" not in rewritten
    assert "Russian_article" not in rewritten


def test_html_fragment_to_markdown_converts_fandom_elements() -> None:
    fragment = """<div>
<h2>Overview</h2>
<p><strong>Important</strong> <a href="./Bear.md">Bear</a> <span>details</span></p>
<ul><li>First item</li><li>Second <em>item</em></li></ul>
<table class="infobox">
<caption>Bear</caption>
<tbody>
<tr><th>Release</th><td>2017</td></tr>
<tr><td colspan="2"><strong>Properties</strong></td></tr>
</tbody>
</table>
<figure><img src="../assets/Bear.png" alt="Bear" />
<figcaption>Bear icon</figcaption></figure>
</div>"""

    markdown = _html_fragment_to_markdown(fragment)

    assert "## Overview" in markdown
    assert "**Important** [Bear](./Bear.md) details" in markdown
    assert "- First item" in markdown
    assert "- Second _item_" in markdown
    assert "**Bear**" in markdown
    assert "| Property | Details |" in markdown
    assert "| Release | 2017 |" in markdown
    assert "**Properties**" in markdown
    assert "![Bear](../assets/Bear.png)" in markdown
    assert "_Bear icon_" in markdown
    assert not re.search(r"</?[A-Za-z][A-Za-z0-9-]*(?=\s|/?>)[^>]*>", markdown)


def test_html_fragment_to_markdown_handles_custom_tags() -> None:
    fragment = """<fandom-widget>
<header><h3>Details</h3></header>
<p><q>Stay warm</q> <abbr title="The Long Dark">TLD</abbr> <mark>Warning</mark></p>
<p style="font-weight: bold">Styled paragraph</p>
<custom-card style="display: block"><p>Second block</p></custom-card>
<!-- This comment is not visible content. -->
<script>hidden script content</script>
<style>.hidden { display: none; }</style>
</fandom-widget>"""

    markdown = _html_fragment_to_markdown(fragment)

    assert "### Details" in markdown
    assert "“Stay warm”" in markdown
    assert "TLD (The Long Dark)" in markdown
    assert "**Warning**" in markdown
    assert "**Styled paragraph**" in markdown
    assert "Second block" in markdown
    assert "This comment is not visible content" not in markdown
    assert "hidden script content" not in markdown
    assert ".hidden" not in markdown
    assert not markdown_export_module.HTML_TAG_RE.search(markdown)
    assert "<!--" not in markdown


def test_html_markup_detection_preserves_markdown_url_autolinks() -> None:
    assert not markdown_export_module.HTML_TAG_RE.search("<https://example.com>")
    assert markdown_export_module.HTML_TAG_RE.search("<mw:widget>visible</mw:widget>")


def test_html_fragment_to_markdown_rewrites_mediawiki_heading_anchors() -> None:
    fragment = (
        '<nav><a href="#Section_One">Section One</a></nav>'
        '<h2 id="Section_One">Section One</h2>'
        '<a href="#cite_note-1">[1]</a>'
    )

    markdown = _html_fragment_to_markdown(fragment)

    assert "[Section One](#section-one)" in markdown
    assert r"[\[1\]](#cite_note-1)" in markdown
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


def test_repair_unclosed_mediawiki_table_closes_before_following_section() -> None:
    source = """{| class="infobox"
|-
| Infobox content
== Overview ==
The article body must remain outside the infobox.
"""

    repaired, count = _repair_unclosed_mediawiki_tables(source)

    assert count == 1
    assert repaired == """{| class="infobox"
|-
| Infobox content
|}
== Overview ==
The article body must remain outside the infobox.
"""


def test_repair_unclosed_mediawiki_table_ignores_fake_heading_in_comment() -> None:
    source = """{| class="infobox"
|-
| Infobox content
<nowiki>
== Literal heading ==
</nowiki>
<!--
<nowiki>
== Commented heading ==
-->
== Overview ==
The article body must remain outside the infobox.
"""

    repaired, count = _repair_unclosed_mediawiki_tables(source)

    assert count == 1
    assert "== Literal heading ==\n</nowiki>" in repaired
    assert "== Commented heading ==" in repaired
    assert "|}\n== Overview ==" in repaired
    assert "|}\n== Literal heading ==" not in repaired


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

    def fail_postprocess(text: str, categories: list[str], stats: RenderStats) -> str:
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


def test_export_uses_html_to_markdown_when_direct_source_conversion_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_pandoc(
        _pandoc: str, _text: str, source_format: str, target_format: str
    ) -> str:
        if source_format == "mediawiki" and target_format == "gfm":
            raise RuntimeError("simulated direct Markdown failure")
        if source_format == "mediawiki" and target_format == "html":
            opening = "<div><h2>Source fallback</h2>"
            paragraph = "<p><strong>Readable</strong> text</p></div>"
            return opening + paragraph
        raise AssertionError("Unexpected Pandoc conversion")

    def fail_postprocess(text: str, categories: list[str], stats: RenderStats) -> str:
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
    assert "## Source fallback" in output
    assert "**Readable** text" in output
    assert "<div>" not in output
    assert "<strong>" not in output
    assert expansion_errors == 0
    assert conversion_errors == 0


def test_export_strips_html_when_all_pandoc_fallbacks_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_pandoc(
        _pandoc: str, _text: str, _source_format: str, _target_format: str
    ) -> str:
        raise RuntimeError("simulated Pandoc failure")

    def fail_postprocess(text: str, categories: list[str], stats: RenderStats) -> str:
        _ = (text, categories, stats)
        raise RuntimeError("simulated postprocess failure")

    monkeypatch.setattr(markdown_export_module, "_run_pandoc", fail_pandoc)
    pages_dir = tmp_path / "pages"
    pages_dir.mkdir()

    result, expansion_errors, conversion_errors = _export_one_page(
        DumpPage(
            "Sample",
            0,
            "wikitext",
            "<p><strong>Readable</strong> source text</p>",
            None,
        ),
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
    assert "**Readable** source text" in output
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
