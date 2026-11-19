import re
from typing import TYPE_CHECKING

import mwparserfromhell as mwp

if TYPE_CHECKING:
    from collections.abc import Iterator

    from mwparserfromhell.nodes import Template, Wikilink

    from pipeline.wiki import Wiki

__all__ = ["LinkExtractor"]

NOINCLUDE = re.compile(r"<noinclude>.*?</noinclude>", re.DOTALL | re.IGNORECASE)
INCLUDEONLY_BLOCK = re.compile(
    r"<includeonly>.*?</includeonly>", re.DOTALL | re.IGNORECASE
)
INCLUDEONLY_TAG = re.compile(r"</?includeonly>", re.IGNORECASE)
NOINCLUDE_TAG = re.compile(r"</?noinclude>", re.IGNORECASE)
# Interlanguage and interwiki prefixes such as "Ru:" or "Wikipedia:".
INTERWIKI = re.compile(r"^(?:[a-z]{2,3}|wikipedia|wikia|w|c):", re.IGNORECASE)
LINK_PARAM = re.compile(r"\blink\s*=\s*([^|\n\]}<>]+)", re.IGNORECASE)

# Namespaces whose links are not navigation between articles.
FILE_NS = 6
CATEGORY_NS = 14


def page_view(text: str) -> str:
    """Wikitext as seen when the page itself is rendered."""
    text = INCLUDEONLY_BLOCK.sub("", text)
    return NOINCLUDE_TAG.sub("", text)


def transcluded_view(text: str) -> str:
    """Wikitext as seen when the page is transcluded into another page."""
    text = NOINCLUDE.sub("", text)
    return INCLUDEONLY_TAG.sub("", text)


class LinkExtractor:
    """Resolve the outgoing links of pages, expanding template transclusions."""

    def __init__(self, wiki: Wiki) -> None:
        self.wiki = wiki
        self.dangling: set[str] = set()
        self._templates: dict[str, tuple[str, ...]] = {}

    def links(self, title: str) -> tuple[str, ...]:
        """Ordered, de-duplicated link targets of a page (templates expanded)."""
        page = self.wiki.pages[title]
        found = self._scan(page_view(page.text), {title})
        return tuple(dict.fromkeys(t for t in found if t != title))

    def _scan(self, text: str, stack: set[str]) -> Iterator[str]:
        code = mwp.parse(text)
        wikilinks: list[Wikilink] = code.filter_wikilinks()  # pyright: ignore[reportUnknownMemberType]
        for link in wikilinks:
            yield from self._wikilink(str(link.title))
        for match in LINK_PARAM.finditer(text):
            target = match.group(1).strip()
            if not target.lower().startswith(("http:", "https:")):
                yield from self._target(target)
        templates: list[Template] = code.filter_templates()  # pyright: ignore[reportUnknownMemberType]
        for template in templates:
            name = str(template.name).strip()
            if name.startswith("#") or not name:
                continue
            yield from self._template(name, stack)

    def _wikilink(self, raw: str) -> Iterator[str]:
        title = self.wiki.normalize(raw)
        prefix = title.split(":", 1)[0].lower() if title and ":" in title else ""
        key = self.wiki.ns_lookup.get(prefix)
        if key in {FILE_NS, CATEGORY_NS} and not raw.strip().startswith(":"):
            # File and category tags are not links; any link= parameter of a
            # file tag is picked up separately by LINK_PARAM.
            return
        yield from self._target(raw)

    def _target(self, raw: str) -> Iterator[str]:
        target = self.wiki.lookup(raw)
        if target is None:
            normalized = self.wiki.normalize(raw)
            if normalized is not None and not INTERWIKI.match(normalized):
                self.dangling.add(normalized)
            return
        yield target

    def _template(self, name: str, stack: set[str]) -> Iterator[str]:
        explicit = name.startswith(":")
        raw = name if explicit else f"Template:{name}"
        title = self.wiki.lookup(raw)
        if title is None or title in stack:
            return
        cached = self._templates.get(title)
        if cached is None:
            text = transcluded_view(self.wiki.pages[title].text)
            cached = tuple(self._scan(text, {*stack, title}))
            self._templates[title] = cached
        yield from cached
