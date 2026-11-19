import json
from collections import defaultdict
from pathlib import Path  # noqa: TC003
from typing import TYPE_CHECKING, TypedDict

import mwparserfromhell as mwp

from pipeline.wikitext import is_nested_in_template_or_argument

if TYPE_CHECKING:
    from pipeline.wiki import Wiki

__all__ = ["build_category_report", "write_category_report"]

CATEGORY_NS = 14
ARTICLE_NS = 0


class CategoryEntry(TypedDict):
    article_count: int
    pages: list[str]
    sort_keys: dict[str, str]
    parents: list[str]
    subcategories: list[str]


class CategoryReportSummary(TypedDict):
    article_pages_scanned: int
    category_pages_scanned: int
    categories_found: int
    categorized_articles: int
    uncategorized_articles: int


class CategoryReport(TypedDict):
    summary: CategoryReportSummary
    categories: dict[str, CategoryEntry]
    uncategorized: list[str]


def category_links(text: str, wiki: Wiki) -> list[tuple[str, str | None]]:
    """Return direct, static category links and their optional sort keys.

    Links inside template calls or template-argument defaults are excluded:
    their presence in the source does not prove that the page emits them.
    Dynamic category titles are also excluded because their final target
    depends on template-parameter or parser-function expansion.
    """
    code = mwp.parse(text)
    result: list[tuple[str, str | None]] = []

    wikilinks = code.filter_wikilinks(  # pyright: ignore[reportUnknownMemberType]
        recursive=True
    )
    for link in wikilinks:
        if is_nested_in_template_or_argument(code, link):
            continue

        raw = str(link.title).strip()

        # A leading colon makes this an ordinary link, not categorisation.
        if raw.startswith(":"):
            continue

        # Do not count a title whose target requires wikitext expansion.
        if "{{" in raw or "}}" in raw:
            continue

        normalized = wiki.normalize(raw)
        if normalized is None:
            continue

        prefix, separator, _ = normalized.partition(":")
        if not separator or wiki.ns_lookup.get(prefix.lower()) != CATEGORY_NS:
            continue

        sort_key = str(link.text).strip() if link.text is not None else None
        result.append((normalized, sort_key or None))

    return result


def collect_article_memberships(
    wiki: Wiki,
) -> tuple[dict[str, list[str]], dict[str, dict[str, str]], list[str], int]:
    """Collect direct category memberships for article pages."""
    members: dict[str, list[str]] = defaultdict(list)
    sort_keys: dict[str, dict[str, str]] = defaultdict(dict)
    uncategorized: list[str] = []
    article_count = 0

    for page in wiki.pages.values():
        if page.redirect is not None or page.ns != ARTICLE_NS:
            continue

        article_count += 1
        links = category_links(page.text, wiki)

        if not links:
            uncategorized.append(page.title)
            continue

        for category, sort_key in links:
            members[category].append(page.title)
            if sort_key is not None:
                sort_keys[category][page.title] = sort_key

    return members, sort_keys, uncategorized, article_count


def collect_category_hierarchy(wiki: Wiki) -> tuple[dict[str, set[str]], set[str], int]:
    """Collect parent categories and category-page titles."""
    parents: dict[str, set[str]] = defaultdict(set)
    category_titles: set[str] = set()
    category_page_count = 0

    for page in wiki.pages.values():
        if page.redirect is not None or page.ns != CATEGORY_NS:
            continue

        category_page_count += 1
        category_titles.add(page.title)

        for parent, _ in category_links(page.text, wiki):
            parents[page.title].add(parent)

    return parents, category_titles, category_page_count


def build_category_entries(
    members: dict[str, list[str]],
    sort_keys: dict[str, dict[str, str]],
    parents: dict[str, set[str]],
    category_titles: set[str],
) -> dict[str, CategoryEntry]:
    """Combine membership and hierarchy data into category entries."""
    all_categories = set(members) | set(parents) | category_titles

    for parent_categories in parents.values():
        all_categories.update(parent_categories)

    children: dict[str, set[str]] = defaultdict(set)
    for child, parent_categories in parents.items():
        for parent in parent_categories:
            children[parent].add(child)

    return {
        title: {
            "article_count": len(set(members[title])),
            "pages": sorted(set(members[title])),
            "sort_keys": sort_keys[title],
            "parents": sorted(parents[title]),
            "subcategories": sorted(children[title]),
        }
        for title in sorted(all_categories)
    }


def build_category_report(wiki: Wiki) -> CategoryReport:
    """Collect direct article memberships and category-page hierarchy."""
    members, sort_keys, uncategorized, article_count = collect_article_memberships(wiki)
    parents, category_titles, category_page_count = collect_category_hierarchy(wiki)
    categories = build_category_entries(members, sort_keys, parents, category_titles)

    return {
        "summary": {
            "article_pages_scanned": article_count,
            "category_pages_scanned": category_page_count,
            "categories_found": len(categories),
            "categorized_articles": article_count - len(uncategorized),
            "uncategorized_articles": len(uncategorized),
        },
        "categories": categories,
        "uncategorized": sorted(uncategorized),
    }


def render_category_tree(report: CategoryReport) -> str:
    """Render category hierarchy as a readable Unix-style tree."""
    categories = report["categories"]
    roots = sorted(title for title, data in categories.items() if not data["parents"])

    lines = ["Categories"]
    visited: set[str] = set()

    def visit(title: str, prefix: str, is_last: bool) -> None:
        branch = "└── " if is_last else "├── "
        data = categories[title]
        lines.append(f"{prefix}{branch}{title} ({data['article_count']} articles)")
        visited.add(title)

        children = data["subcategories"]
        visible = [child for child in children if child in categories]

        for index, child in enumerate(visible):
            last = index == len(visible) - 1
            child_prefix = prefix + ("    " if is_last else "│   ")

            if child in visited:
                marker = "└── " if last else "├── "
                lines.append(f"{child_prefix}{marker}{child} [already shown]")
            else:
                visit(child, child_prefix, last)

    for index, root in enumerate(roots):
        visit(root, "", index == len(roots) - 1)

    remaining = sorted(set(categories) - visited)
    if remaining:
        lines.append("")
        lines.append("Unrooted categories (possible cycles or missing parents):")
        lines.extend(f"- {title}" for title in remaining)

    return "\n".join(lines)


def write_category_report(report: CategoryReport, output_dir: Path) -> None:
    """Write JSON, category tree and uncategorized-page reports."""
    output_dir.mkdir(parents=True, exist_ok=True)

    (output_dir / "category-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    (output_dir / "category-tree.txt").write_text(
        render_category_tree(report) + "\n", encoding="utf-8"
    )

    (output_dir / "uncategorized.txt").write_text(
        "\n".join(report["uncategorized"]) + "\n", encoding="utf-8"
    )
