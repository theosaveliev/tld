"""Analyze categories that may be introduced through template transclusion.

This is a static wikitext analysis, not a full MediaWiki expansion engine.
Results are potential template-generated memberships: parser functions,
conditional branches, and parameter substitution are not evaluated.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path  # noqa: TC003
from typing import TYPE_CHECKING, Literal, TypedDict

import mwparserfromhell as mwp

if TYPE_CHECKING:
    from pipeline.wiki import Page, Wiki

__all__ = ["build_template_category_report", "write_template_category_report"]

ARTICLE_NS = 0
TEMPLATE_NS = 10
CATEGORY_NS = 14

# Common MediaWiki magic words are written with template braces but are not
# transclusions of pages in the Template namespace.
_MAGIC_WORDS = {
    "ARTICLEPAGENAME",
    "ARTICLEPAGENAMEE",
    "BASEPAGENAME",
    "BASEPAGENAMEE",
    "CONTENTLANG",
    "CONTENTLANGUAGE",
    "CURRENTDAY",
    "CURRENTDAY2",
    "CURRENTDAYNAME",
    "CURRENTDOW",
    "CURRENTHOUR",
    "CURRENTMONTH",
    "CURRENTMONTH1",
    "CURRENTMONTHABBREV",
    "CURRENTMONTHNAME",
    "CURRENTMONTHNAMEGEN",
    "CURRENTTIME",
    "CURRENTTIMESTAMP",
    "CURRENTWEEK",
    "CURRENTYEAR",
    "DIRECTIONMARK",
    "DIRMARK",
    "FULLPAGENAME",
    "FULLPAGENAMEE",
    "NAMESPACE",
    "NAMESPACEE",
    "NAMESPACENUMBER",
    "NUMBEROFADMINS",
    "NUMBEROFARTICLES",
    "NUMBEROFACTIVEUSERS",
    "NUMBEROFEDITS",
    "NUMBEROFFILES",
    "NUMBEROFPAGES",
    "NUMBEROFUSERS",
    "PAGENAME",
    "PAGENAMEE",
    "REVISIONDAY",
    "REVISIONDAY2",
    "REVISIONID",
    "REVISIONMONTH",
    "REVISIONMONTH1",
    "REVISIONTIMESTAMP",
    "REVISIONUSER",
    "REVISIONYEAR",
    "ROOTPAGENAME",
    "ROOTPAGENAMEE",
    "SCRIPTPATH",
    "SERVER",
    "SERVERNAME",
    "SUBJECTPAGENAME",
    "SUBJECTPAGENAMEE",
    "SUBJECTSPACE",
    "SUBJECTSPACEE",
    "SUBPAGENAME",
    "SUBPAGENAMEE",
    "TALKPAGENAME",
    "TALKPAGENAMEE",
    "TALKSPACE",
    "TALKSPACEE",
    "CURRENTVERSION",
    "DISPLAYTITLE",
    "DEFAULTSORT",
}

_NO_INCLUDE_RE = re.compile(
    r"<noinclude\b[^>]*>.*?</noinclude\s*>", flags=re.IGNORECASE | re.DOTALL
)
_INCLUDE_ONLY_RE = re.compile(
    r"<includeonly\b[^>]*>(.*?)</includeonly\s*>", flags=re.IGNORECASE | re.DOTALL
)
_ONLY_INCLUDE_RE = re.compile(
    r"<onlyinclude\b[^>]*>(.*?)</onlyinclude\s*>", flags=re.IGNORECASE | re.DOTALL
)


class CategoryLink(TypedDict):
    category: str
    sort_key: str | None


class TemplateInfo(TypedDict):
    page_status: Literal["found", "missing", "redirect"]
    referenced_by_articles: list[str]
    used_by_articles: list[str]
    nested_templates: list[str]
    category_links: list[CategoryLink]


class TemplateCategoryInfo(TypedDict):
    pages: list[str]
    templates: list[str]


class ArticleInfo(TypedDict):
    explicit_categories: list[str]
    template_categories: list[str]
    all_detected_categories: list[str]
    templates: list[str]


class TemplateReportSummary(TypedDict):
    article_pages_scanned: int
    template_pages_found_in_dump: int
    articles_with_template_invocations: int
    unique_templates_encountered: int
    templates_found: int
    missing_templates: int
    redirect_templates_not_expanded: int
    templates_with_category_links: int
    articles_with_potential_template_categories: int
    potential_template_category_memberships: int
    articles_without_categories_after_scan: int
    articles_using_missing_templates: int


class TemplateCategoryReport(TypedDict):
    summary: TemplateReportSummary
    templates: dict[str, TemplateInfo]
    template_categories: dict[str, TemplateCategoryInfo]
    articles: dict[str, ArticleInfo]
    missing_templates: dict[str, list[str]]
    dynamic_category_titles: dict[str, list[str]]
    uncategorized_after_scan: list[str]


@dataclass
class _TemplateState:
    referenced_by_articles: dict[str, set[str]] = field(
        default_factory=lambda: defaultdict(set)
    )
    used_by_articles: dict[str, set[str]] = field(
        default_factory=lambda: defaultdict(set)
    )
    nested_templates: dict[str, set[str]] = field(
        default_factory=lambda: defaultdict(set)
    )
    category_links: dict[str, dict[tuple[str, str | None], None]] = field(
        default_factory=lambda: defaultdict(dict)
    )
    page_status: dict[str, Literal["found", "missing", "redirect"]] = field(
        default_factory=dict
    )
    dynamic_category_titles: dict[str, set[str]] = field(
        default_factory=lambda: defaultdict(set)
    )


def _normalise_namespace_title(
    raw_title: str, namespace_name: str, namespace_id: int, wiki: Wiki
) -> str | None:
    """Normalize a title and verify that it belongs to the requested namespace."""
    title = raw_title.strip()
    if not title or title.startswith(":"):
        return None

    if ":" not in title:
        title = f"{namespace_name}:{title}"

    normalized = wiki.normalize(title)
    if normalized is None:
        return None

    prefix, separator, _ = normalized.partition(":")
    if not separator or wiki.ns_lookup.get(prefix.lower()) != namespace_id:
        return None

    return normalized


def _transcluded_wikitext(text: str) -> str:
    """Apply common include/noinclude rules before inspecting a template body.

    This handles ordinary, non-nested include tags. It deliberately does not
    attempt to emulate every edge case of MediaWiki's transclusion parser.
    """
    only_include_sections = _ONLY_INCLUDE_RE.findall(text)
    if only_include_sections:
        text = "\n".join(only_include_sections)

    text = _NO_INCLUDE_RE.sub("", text)
    return _INCLUDE_ONLY_RE.sub(r"\1", text)


def _template_titles(text: str, wiki: Wiki, *, transcluded: bool = False) -> set[str]:
    """Find syntactic invocations of pages in the Template namespace."""
    source = _transcluded_wikitext(text) if transcluded else text
    code = mwp.parse(source)
    # mwparserfromhell's third-party stubs leave part of this method signature
    # unknown; the returned Wikilink/Template nodes are otherwise typed.
    templates = code.filter_templates(  # pyright: ignore[reportUnknownMemberType]
        recursive=True
    )

    result: set[str] = set()
    for template in templates:
        raw_name = str(template.name).strip()
        magic_word_name = raw_name.split(":", maxsplit=1)[0].upper()
        if "{{" in raw_name or "}}" in raw_name:
            continue
        if magic_word_name in _MAGIC_WORDS:
            continue
        if raw_name.startswith("#"):
            # Parser functions such as {{#if:...}} are not template pages.
            continue

        normalized = _normalise_namespace_title(raw_name, "Template", TEMPLATE_NS, wiki)
        if normalized is not None:
            result.add(normalized)

    return result


def _template_category_links(
    text: str, wiki: Wiki, *, transcluded: bool = False
) -> tuple[set[tuple[str, str | None]], set[str]]:
    """Find static category links and separately retain dynamic category titles."""
    source = _transcluded_wikitext(text) if transcluded else text
    code = mwp.parse(source)
    wikilinks = code.filter_wikilinks(  # pyright: ignore[reportUnknownMemberType]
        recursive=True
    )

    static_links: set[tuple[str, str | None]] = set()
    dynamic_titles: set[str] = set()

    for link in wikilinks:
        raw_title = str(link.title).strip()
        if raw_title.startswith(":"):
            continue

        # A template/parser function in the category title needs parameter or
        # parser-function expansion, which this static pass does not perform.
        if "{{" in raw_title or "}}" in raw_title:
            dynamic_titles.add(raw_title)
            continue

        normalized = _normalise_namespace_title(
            raw_title, "Category", CATEGORY_NS, wiki
        )
        if normalized is None:
            continue

        sort_key = str(link.text).strip() if link.text is not None else None
        static_links.add((normalized, sort_key or None))

    return static_links, dynamic_titles


def _build_template_index(wiki: Wiki) -> dict[str, Page]:
    """Index template-namespace pages by normalized title."""
    result: dict[str, Page] = {}
    for page in wiki.pages.values():
        if page.ns != TEMPLATE_NS:
            continue

        normalized = _normalise_namespace_title(
            page.title, "Template", TEMPLATE_NS, wiki
        )
        if normalized is not None:
            result[normalized] = page

    return result


def _scan_article_templates(
    article_title: str,
    article_text: str,
    wiki: Wiki,
    template_pages: dict[str, Page],
    state: _TemplateState,
) -> tuple[set[str], set[str], dict[str, set[str]]]:
    """Walk an article's template dependency graph and collect candidate categories."""
    referenced_templates = _template_titles(article_text, wiki)
    pending = list(referenced_templates)
    visited: set[str] = set()
    category_sources: dict[str, set[str]] = defaultdict(set)

    for template_title in referenced_templates:
        state.referenced_by_articles[template_title].add(article_title)

    while pending:
        template_title = pending.pop()
        if template_title in visited:
            continue

        visited.add(template_title)
        state.used_by_articles[template_title].add(article_title)
        # Template source is static, so parse each template at most once even
        # when hundreds of articles use it. Reuse its dependencies/categories.
        if template_title in state.page_status:
            for category, _ in state.category_links[template_title]:
                category_sources[category].add(template_title)
            pending.extend(state.nested_templates[template_title] - visited)
            continue

        template_page = template_pages.get(template_title)

        if template_page is None:
            state.page_status[template_title] = "missing"
            continue

        if template_page.redirect is not None:
            state.page_status[template_title] = "redirect"
            continue

        state.page_status[template_title] = "found"
        links, dynamic_titles = _template_category_links(
            template_page.text, wiki, transcluded=True
        )
        for category, sort_key in links:
            state.category_links[template_title][(category, sort_key)] = None
            category_sources[category].add(template_title)

        state.dynamic_category_titles[template_title].update(dynamic_titles)

        nested_templates = _template_titles(template_page.text, wiki, transcluded=True)
        state.nested_templates[template_title].update(nested_templates)
        pending.extend(nested_templates - visited)

    return referenced_templates, visited, category_sources


def _build_public_template_data(
    state: _TemplateState,
) -> tuple[dict[str, TemplateInfo], dict[str, list[str]]]:
    """Convert internal sets into stable, JSON-serializable structures."""
    template_names = sorted(state.used_by_articles)
    templates: dict[str, TemplateInfo] = {}
    missing: dict[str, list[str]] = {}

    for title in template_names:
        status = state.page_status.get(title, "missing")
        link_pairs = sorted(
            state.category_links[title], key=lambda pair: (pair[0], pair[1] or "")
        )
        templates[title] = {
            "page_status": status,
            "referenced_by_articles": sorted(state.referenced_by_articles[title]),
            "used_by_articles": sorted(state.used_by_articles[title]),
            "nested_templates": sorted(state.nested_templates[title]),
            "category_links": [
                {"category": category, "sort_key": sort_key}
                for category, sort_key in link_pairs
            ],
        }

        if status == "missing":
            missing[title] = sorted(state.used_by_articles[title])

    return templates, missing


def build_template_category_report(wiki: Wiki) -> TemplateCategoryReport:
    """Find candidate article categories introduced by direct or nested templates."""
    template_pages = _build_template_index(wiki)
    state = _TemplateState()

    articles: dict[str, ArticleInfo] = {}
    category_pages: dict[str, set[str]] = defaultdict(set)
    category_templates: dict[str, set[str]] = defaultdict(set)
    uncategorized: list[str] = []
    article_count = 0
    articles_with_templates: set[str] = set()
    articles_with_template_categories: set[str] = set()
    articles_with_missing_templates: set[str] = set()

    for page in wiki.pages.values():
        if page.redirect is not None or page.ns != ARTICLE_NS:
            continue

        article_count += 1
        explicit_links, _ = _template_category_links(page.text, wiki)
        explicit_categories = {category for category, _ in explicit_links}

        referenced, used, template_sources = _scan_article_templates(
            page.title, page.text, wiki, template_pages, state
        )

        if referenced:
            articles_with_templates.add(page.title)

        template_categories = set(template_sources)
        if template_categories:
            articles_with_template_categories.add(page.title)

        for category, sources in template_sources.items():
            category_pages[category].add(page.title)
            category_templates[category].update(sources)

        all_categories = explicit_categories | template_categories
        if not all_categories:
            uncategorized.append(page.title)

        articles[page.title] = {
            "explicit_categories": sorted(explicit_categories),
            "template_categories": sorted(template_categories),
            "all_detected_categories": sorted(all_categories),
            "templates": sorted(used),
        }

        if any(
            state.page_status.get(template_title) == "missing"
            for template_title in used
        ):
            articles_with_missing_templates.add(page.title)

    templates, missing_templates = _build_public_template_data(state)

    template_categories_report: dict[str, TemplateCategoryInfo] = {
        category: {
            "pages": sorted(category_pages[category]),
            "templates": sorted(category_templates[category]),
        }
        for category in sorted(category_pages)
    }

    potential_memberships = sum(
        len(data["pages"]) for data in template_categories_report.values()
    )
    templates_with_category_links = sum(
        bool(info["category_links"]) for info in templates.values()
    )
    redirect_templates = sum(
        info["page_status"] == "redirect" for info in templates.values()
    )

    dynamic_category_titles = {
        title: sorted(values)
        for title, values in sorted(state.dynamic_category_titles.items())
        if values
    }

    summary: TemplateReportSummary = {
        "article_pages_scanned": article_count,
        "template_pages_found_in_dump": len(template_pages),
        "articles_with_template_invocations": len(articles_with_templates),
        "unique_templates_encountered": len(templates),
        "templates_found": sum(
            info["page_status"] == "found" for info in templates.values()
        ),
        "missing_templates": len(missing_templates),
        "redirect_templates_not_expanded": redirect_templates,
        "templates_with_category_links": templates_with_category_links,
        "articles_with_potential_template_categories": len(
            articles_with_template_categories
        ),
        "potential_template_category_memberships": potential_memberships,
        "articles_without_categories_after_scan": len(uncategorized),
        "articles_using_missing_templates": len(articles_with_missing_templates),
    }

    return {
        "summary": summary,
        "templates": templates,
        "template_categories": template_categories_report,
        "articles": articles,
        "missing_templates": missing_templates,
        "dynamic_category_titles": dynamic_category_titles,
        "uncategorized_after_scan": sorted(uncategorized),
    }


def write_template_category_report(
    report: TemplateCategoryReport, output_dir: Path
) -> None:
    """Write the template-category analysis as JSON plus a compact summary."""
    output_dir.mkdir(parents=True, exist_ok=True)

    (output_dir / "template-category-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    summary_lines = ["Template category analysis", ""]
    for name, value in report["summary"].items():
        summary_lines.append(f"{name}: {value}")

    summary_lines.extend(["", "Potential categories introduced by templates:"])
    for category, data in sorted(
        report["template_categories"].items(),
        key=lambda item: len(item[1]["pages"]),
        reverse=True,
    ):
        summary_lines.append(f"{len(data['pages']):>5}  {category}")

    (output_dir / "template-category-summary.txt").write_text(
        "\n".join(summary_lines) + "\n", encoding="utf-8"
    )
