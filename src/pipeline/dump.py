"""Streaming reader for MediaWiki XML page records."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

import defusedxml.ElementTree as SafeET

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path
    from xml.etree.ElementTree import Element

REDIRECT_RE = re.compile(r"\s*#redirect\s*:?\s*\[\[([^\]|]+)", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class DumpPage:
    """A page extracted from one XML dump record."""

    title: str
    namespace: int
    model: str
    text: str
    redirect: str | None


def _xml_local_name(element: Element) -> str:
    """Return an XML element's local name without its namespace."""
    return element.tag.rsplit("}", 1)[-1]


def _read_slot(slot: Element, model: str, text: str) -> tuple[str, str]:
    """Read the content model and text from one revision slot."""
    for child in slot:
        tag = _xml_local_name(child)
        if tag == "text":
            text = child.text or ""
        elif tag == "model":
            model = child.text or model
    return model, text


def _read_revision(revision: Element, model: str, text: str) -> tuple[str, str]:
    """Read legacy revision fields or slot-based revision content."""
    for child in revision:
        tag = _xml_local_name(child)
        if tag == "slots":
            slots = [slot for slot in child if _xml_local_name(slot) == "slot"]
            main_slot = next(
                (slot for slot in slots if slot.get("role") == "main"),
                slots[0] if slots else None,
            )
            if main_slot is not None:
                model, text = _read_slot(main_slot, model, text)
        elif tag == "text":
            text = child.text or ""
        elif tag == "model":
            model = child.text or model
    return model, text


def page_from_element(element: Element) -> DumpPage | None:
    """Extract a typed page record from one XML page element."""
    title = ""
    namespace = 0
    model = "wikitext"
    text = ""
    redirect: str | None = None

    for child in element:
        tag = _xml_local_name(child)
        if tag == "title":
            title = child.text or ""
        elif tag == "ns":
            namespace = int(child.text or "0")
        elif tag == "redirect":
            redirect = child.get("title")
        elif tag == "revision":
            model, text = _read_revision(child, model, text)

    if not redirect:
        match = REDIRECT_RE.match(text)
        if match:
            redirect = match.group(1).strip()

    if not title:
        return None
    return DumpPage(title, namespace, model, text, redirect)


def iter_dump_pages(path: Path) -> Iterator[DumpPage]:
    """Stream page records from a MediaWiki XML dump."""
    events = cast(
        "Iterator[tuple[str, Element]]",
        SafeET.iterparse(str(path), events=("start", "end")),
    )
    try:
        _, root = next(events)
    except StopIteration as exc:
        raise ValueError(f"Wiki dump is empty: {path}") from exc

    for event, element in events:
        if event != "end" or _xml_local_name(element) != "page":
            continue

        page = page_from_element(element)
        root.clear()
        if page is not None:
            yield page
