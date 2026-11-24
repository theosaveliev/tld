"""Small helpers for inspecting the mwparserfromhell syntax tree."""

from __future__ import annotations

from typing import re
from typing import TYPE_CHECKING

from mwparserfromhell.nodes import Argument, Template, Wikilink

if TYPE_CHECKING:
    from mwparserfromhell.wikicode import Wikicode

    from pipeline.wiki import Wiki


def is_nested_in_template_or_argument(
    code: Wikicode, node: Template | Wikilink
) -> bool:
    """Return whether *node* occurs inside a template or template argument.

    Recursive mwparserfromhell filters also return nodes inside template
    parameters. Those nodes are not necessarily emitted by the surrounding
    template, so callers that are identifying direct page/template content
    should not attribute them to that page/template.
    """
    return any(
        isinstance(ancestor, (Template, Argument))
        for ancestor in code.get_ancestors(node)
    )



CATEGORY_NS = 14
_NO_INCLUDE_RE = re.compile(
    r"<noinclude\\b[^>]*>.*?</noinclude\\s*>", re.IGNORECASE | re.DOTALL
)
_INCLUDE_ONLY_RE = re.compile(
    r"<includeonly\\b[^>]*>(.*?)</includeonly\\s*>", re.IGNORECASE | re.DOTALL
)
_ONLY_INCLUDE_RE = re.compile(
    r"<onlyinclude\\b[^>]*>(.*?)</onlyinclude\\s*>", re.IGNORECASE | re.DOTALL
)


def transcluded_wikitext(text: str) -> str:
    """Apply common include/noinclude rules before inspecting a template body.

    This handles ordinary, non-nested include tags. It deliberately does not
    attempt to emulate every edge case of MediaWiki's transclusion parser.
    """
    only_include_sections = _ONLY_INCLUDE_RE.findall(text)
    if only_include_sections:
        text = "\\n".join(only_include_sections)
    text = _NO_INCLUDE_RE.sub("", text)
    return _INCLUDE_ONLY_RE.sub(r"\\1", text)


def extract_category_links(
    text: str, wiki: Wiki, *, transcluded: bool = False
) -> tuple[list[tuple[str, str | None]], set[str]]:
    """Extract direct static category links and dynamic category-title candidates."""
    source = transcluded_wikitext(text) if transcluded else text
    code = mwp.parse(source)
    static_links: list[tuple[str, str | None]] = []
    dynamic_titles: set[str] = set()

    wikilinks = code.filter_wikilinks(  # pyright: ignore[reportUnknownMemberType]
        recursive=True
    )
    for link in wikilinks:
        if is_nested_in_template_or_argument(code, link):
            continue

        raw_title = str(link.title).strip()
        if raw_title.startswith(":"):
            continue

        normalized = wiki.normalize(raw_title)
        if normalized is None:
            continue

        prefix, separator, _ = normalized.partition(":")
        if not separator or wiki.ns_lookup.get(prefix.lower()) != CATEGORY_NS:
            continue

        if "{{" in raw_title or "}}" in raw_title:
            dynamic_titles.add(raw_title)
            continue

        sort_key = str(link.text).strip() if link.text is not None else None
        static_links.append((normalized, sort_key or None))

    return static_links, dynamic_titles
