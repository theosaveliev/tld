import mwparserfromhell as mwp

from pipeline.categories import category_links
from pipeline.templates import (
    _template_category_links,  # pyright: ignore[reportPrivateUsage]
    _template_titles,  # pyright: ignore[reportPrivateUsage]
    build_template_category_report,
)
from pipeline.wiki import Page, Wiki
from pipeline.wikitext import is_nested_in_template_or_argument


def make_wiki() -> Wiki:
    """Create a small real Wiki object with the namespaces used in these tests."""
    return Wiki(
        ns_names={10: "Template", 14: "Category"},
        ns_lookup={"template": 10, "category": 14},
    )


def test_top_level_template_is_not_nested() -> None:
    code = mwp.parse("{{Outer}}")
    template = code.filter_templates()[0]  # pyright: ignore[reportUnknownMemberType]

    assert not is_nested_in_template_or_argument(code, template)


def test_template_inside_another_templates_parameter_is_nested() -> None:
    code = mwp.parse("{{Outer|value={{Inner}}}}")
    templates = code.filter_templates()  # pyright: ignore[reportUnknownMemberType]
    inner = next(template for template in templates if str(template.name) == "Inner")

    assert is_nested_in_template_or_argument(code, inner)


def test_direct_category_link_is_not_nested() -> None:
    code = mwp.parse("Text [[Category:Direct]]")
    link = code.filter_wikilinks()[0]  # pyright: ignore[reportUnknownMemberType]

    assert not is_nested_in_template_or_argument(code, link)


def test_category_link_inside_template_parameter_is_nested() -> None:
    code = mwp.parse("{{Infobox|category=[[Category:Passed in]]}}")
    link = code.filter_wikilinks()[0]  # pyright: ignore[reportUnknownMemberType]

    assert is_nested_in_template_or_argument(code, link)


def test_category_link_in_argument_default_is_nested() -> None:
    code = mwp.parse("{{{category|[[Category:Fallback]]}}}")
    link = code.filter_wikilinks()[0]  # pyright: ignore[reportUnknownMemberType]

    assert is_nested_in_template_or_argument(code, link)


def test_category_link_inside_html_tag_is_not_excluded() -> None:
    code = mwp.parse("<div>[[Category:Inside tag]]</div>")
    link = code.filter_wikilinks()[0]  # pyright: ignore[reportUnknownMemberType]

    assert not is_nested_in_template_or_argument(code, link)


def test_category_extractor_ignores_links_inside_template_parameters() -> None:
    text = "[[Category:Direct]] {{Infobox|category=[[Category:Parameter]]}}"

    assert category_links(text, make_wiki()) == [("Category:Direct", None)]


def test_template_scanner_ignores_calls_inside_template_parameters() -> None:
    text = "{{Outer|value={{Inner}}}}"

    assert _template_titles(text, make_wiki()) == {"Template:Outer"}


def test_template_category_scanner_ignores_parameter_categories() -> None:
    text = "[[Category:Direct]] {{Wrapper|category=[[Category:Parameter]]}}"

    links, dynamic_titles = _template_category_links(text, make_wiki())

    assert links == {("Category:Direct", None)}
    assert dynamic_titles == set()


def test_template_category_scanner_does_not_promote_article_links() -> None:
    text = "[[Scurvy|Vitamin C]]"

    links, dynamic_titles = _template_category_links(text, make_wiki())

    assert links == set()
    assert dynamic_titles == set()


def test_template_category_scanner_preserves_category_sort_key() -> None:
    text = "[[Category:Scurvy|Vitamin C]]"

    links, dynamic_titles = _template_category_links(text, make_wiki())

    assert links == {("Category:Scurvy", "Vitamin C")}
    assert dynamic_titles == set()


def test_template_category_scanner_tracks_only_dynamic_category_links() -> None:
    text = "[[Category:{{{category}}}]] [[{{{target}}}]]"

    links, dynamic_titles = _template_category_links(text, make_wiki())

    assert links == set()
    assert dynamic_titles == {"Category:{{{category}}}"}


def test_direct_category_extractor_ignores_dynamic_category_titles() -> None:
    text = "[[Category:{{{category}}}]] [[Category:Static]]"

    assert category_links(text, make_wiki()) == [("Category:Static", None)]


def test_both_extractors_agree_on_static_category_links() -> None:
    text = "[[Related article]] [[Category:Direct]] [[Category:Another|Sort key]]"
    wiki = make_wiki()

    direct_links = set(category_links(text, wiki))
    template_links, dynamic_titles = _template_category_links(text, wiki)

    assert direct_links == template_links
    assert dynamic_titles == set()


def test_template_summary_counts_explicitly_categorized_articles() -> None:
    wiki = make_wiki()
    wiki.pages = {
        "Static article": Page(
            title="Static article", ns=0, text="[[Category:Direct]]"
        ),
        "Dynamic article": Page(
            title="Dynamic article", ns=0, text="[[Category:{{{category}}}]]"
        ),
        "Uncategorized article": Page(
            title="Uncategorized article", ns=0, text="No category link here."
        ),
    }

    report = build_template_category_report(wiki)

    assert report["summary"]["explicitly_categorized_articles"] == 1
    assert report["articles"]["Static article"]["explicit_categories"] == [
        "Category:Direct"
    ]
    assert report["articles"]["Dynamic article"]["explicit_categories"] == []
