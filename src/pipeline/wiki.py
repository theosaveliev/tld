# pyright: reportAny=false
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import defusedxml.ElementTree as ET

if TYPE_CHECKING:
    from pathlib import Path
    from xml.etree.ElementTree import Element

__all__ = ["Page", "Wiki", "load_wiki"]

XML_NS = "{http://www.mediawiki.org/xml/export-0.11/}"
WHITESPACE = re.compile(r"[\s_]+")


@dataclass(slots=True)
class Page:
    title: str
    ns: int
    text: str
    redirect: str | None = None


@dataclass(slots=True)
class Wiki:
    pages: dict[str, Page] = field(default_factory=dict)
    # Namespace key -> canonical name ("" for the main namespace).
    ns_names: dict[int, str] = field(default_factory=dict)
    # Lower-cased namespace name or alias -> namespace key.
    ns_lookup: dict[str, int] = field(default_factory=dict)

    def normalize(self, raw: str) -> str | None:
        """Turn a link target into a canonical page title, or None if empty."""
        title = raw.split("#", 1)[0]
        title = WHITESPACE.sub(" ", title).strip()
        title = title.removeprefix(":").strip()
        if not title:
            return None

        prefix, sep, rest = title.partition(":")
        key = self.ns_lookup.get(prefix.strip().lower()) if sep else None
        if key is not None and rest.strip():
            name = self.ns_names[key]
            rest = rest.strip()
            return f"{name}:{rest[0].upper()}{rest[1:]}" if name else rest
        return f"{title[0].upper()}{title[1:]}"

    def resolve(self, title: str) -> str | None:
        """Follow redirects; return None for missing pages and redirect loops."""
        seen: set[str] = set()
        current: str | None = title
        while current is not None:
            page = self.pages.get(current)
            if page is None or current in seen:
                return None
            if page.redirect is None:
                return current
            seen.add(current)
            current = self.normalize(page.redirect)
        return None

    def lookup(self, raw: str) -> str | None:
        """Normalize a raw link target and resolve it to an existing page."""
        title = self.normalize(raw)
        return self.resolve(title) if title is not None else None

    def aliases(self) -> dict[str, list[str]]:
        """Map each real page to the redirect titles that point to it."""
        result: dict[str, list[str]] = {}
        for page in self.pages.values():
            if page.redirect is None:
                continue
            target = self.resolve(page.title)
            if target is not None:
                result.setdefault(target, []).append(page.title)
        return result


def _read_namespaces(siteinfo: Element, wiki: Wiki) -> None:
    for element in siteinfo.iter(f"{XML_NS}namespace"):
        key = int(element.get("key", "0"))
        name = (element.text or "").strip()
        wiki.ns_names[key] = name
        if name:
            wiki.ns_lookup[name.lower()] = key
    # Standard MediaWiki aliases.
    wiki.ns_lookup.setdefault("image", 6)
    wiki.ns_lookup.setdefault("image talk", 7)
    wiki.ns_lookup.setdefault("project", 4)
    wiki.ns_lookup.setdefault("project talk", 5)


def _read_page(element: Element) -> Page:
    redirect = element.find(f"{XML_NS}redirect")
    return Page(
        title=element.findtext(f"{XML_NS}title") or "",
        ns=int(element.findtext(f"{XML_NS}ns") or "0"),
        text=element.findtext(f"{XML_NS}revision/{XML_NS}text") or "",
        redirect=redirect.get("title") if redirect is not None else None,
    )


def load_wiki(dump: Path) -> Wiki:
    """Stream a MediaWiki XML export into memory."""
    wiki = Wiki()
    # The first start event is the root element; it is emptied as pages
    # complete so that memory stays flat.
    events = ET.iterparse(str(dump), events=("start", "end"))
    _, root = next(events)
    root_element: Element = root
    for event, element in events:
        if event != "end":
            continue
        tag: str = element.tag
        if tag == f"{XML_NS}siteinfo":
            _read_namespaces(element, wiki)
            root_element.clear()
        elif tag == f"{XML_NS}page":
            page = _read_page(element)
            wiki.pages[page.title] = page
            root_element.clear()
    return wiki
