"""Fandom-specific wikitext preprocessing used by the Markdown exporter."""

from __future__ import annotations

import html as htmllib
import importlib.util
import re
import subprocess
import sys
import types
import warnings
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, TypedDict, cast

import mwparserfromhell
import wikitextprocessor.luaexec as _luaexec
from lxml import etree
from wikitextprocessor import Wtp

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

    from mwparserfromhell.nodes import Template

__all__ = [
    "Wtp",
    "canon",
    "load_infobox_defs",
    "pandoc_selftest",
    "postexpand",
    "prepass",
    "render_infobox",
]

warnings.filterwarnings(
    "ignore", message=".*global interpreter lock.*", category=RuntimeWarning
)
warnings.filterwarnings("ignore", category=SyntaxWarning)


# wikitextprocessor 0.4.96 needs two small compatibility shims on newer Python.
if importlib.util.find_spec("pkg_resources") is None:
    _pkg = types.ModuleType("pkg_resources")

    def _resource_filename(package: str, resource: str) -> str:
        spec = importlib.util.find_spec(package)
        if spec is None or spec.origin is None:
            raise ModuleNotFoundError(package)
        return str(Path(spec.origin).parent / resource)

    setattr(_pkg, "resource_filename", _resource_filename)  # noqa: B010
    sys.modules["pkg_resources"] = _pkg


class _ReShim:
    def __getattr__(self, name: str) -> object:
        """Delegate regex operations to the standard-library module."""
        return cast("object", getattr(re, name))

    def match(
        self, pattern: str | re.Pattern[str], string: str, flags: int = 0
    ) -> re.Match[str] | None:
        if isinstance(pattern, str):
            pattern = pattern.replace("^(?s)", "(?s)^")
        return re.match(pattern, string, flags)


setattr(_luaexec, "re", _ReShim())  # noqa: B010


class TemplateContext(Protocol):
    templates: Mapping[str, str]


class InfoboxStats(TypedDict):
    infoboxes: int
    missing_templates: list[str]
    infobox_left: int


_Element = etree._Element  # pyright: ignore[reportPrivateUsage]
SENTINEL = "XSENT"


def local(tag: object) -> str:
    """Return an XML element tag without its namespace."""
    value = tag.decode() if isinstance(tag, (bytes, bytearray)) else str(tag)
    return value.rsplit("}", 1)[-1]


def canon(title: str) -> str:
    """MediaWiki-style title: underscores -> spaces, first letter upper-case."""
    t = re.sub(r"\s+", " ", title.replace("_", " ")).strip()
    return t[:1].upper() + t[1:] if t else t


def esc(s: str) -> str:
    """Escape text for insertion into an HTML attribute or text node."""
    return htmllib.escape(s, quote=True)


# --------------------------------------------------------------------------- #
PARAM_RE = re.compile(r"\{\{\{\s*([^{}|]+?)\s*(?:\|([^{}]*))?\}\}\}")


def subst(text: str, params: Mapping[str, str]) -> str:
    """Substitute the supplied values into a simple infobox parameter default."""
    return PARAM_RE.sub(lambda m: params.get(m.group(1), m.group(2) or ""), text)


def inner(el: _Element) -> str:
    """Return the visible text nested inside an infobox definition element."""
    return "".join(el.itertext()).strip()


def load_infobox_defs(ctx: object) -> dict[str, _Element]:
    """Extract Portable Infobox definitions from the loaded template set."""
    template_context = cast("TemplateContext", ctx)
    defs: dict[str, _Element] = {}
    for name, body in template_context.templates.items():
        if "<infobox" not in body.lower():
            continue
        m = re.search(r"(?is)<infobox\b.*?</infobox\s*>", body)
        if not m:
            continue
        root = etree.fromstring(
            m.group(0).encode("utf-8"), etree.XMLParser(recover=True)
        )
        raw_name = re.sub(r"(?i)^template\s*:\s*", "", name)
        normalized = canon(raw_name)
        defs[normalized] = root
        defs.setdefault(normalized.casefold(), root)
    return defs


def one_line(s: str) -> str:
    """Collapse an infobox value to one readable line."""
    return re.sub(r"\s*\n\s*", " ", s).strip()


def _infobox_value(
    element: _Element, params: Mapping[str, str], page_title: str
) -> str:
    """Resolve a Portable Infobox value from its source or default."""
    source = element.get("source")
    result = params.get(source, "").strip() if source else ""
    default = element.find("default")
    if not result and default is not None:
        result = (
            subst(inner(default), params).replace("{{PAGENAME}}", page_title).strip()
        )
    return result


def _infobox_data_row(
    element: _Element, params: Mapping[str, str], expanded: bool
) -> tuple[str, ...] | None:
    """Build a data row using its source, format, label, and fallback value."""
    source = element.get("source")
    result = params.get(source, "").strip() if source else ""
    format_element = element.find("format")
    if format_element is not None and (result or expanded):
        result = subst(inner(format_element), params).strip()
    if not result:
        default = element.find("default")
        if default is not None:
            result = subst(inner(default), params).strip()
    if not result:
        return None
    label = element.find("label")
    return ("data", inner(label) if label is not None else "", result)


def _infobox_element_row(
    element: _Element, params: Mapping[str, str], page_title: str, expanded: bool
) -> tuple[str, ...] | None:
    """Convert one Portable Infobox definition element into a render row."""
    tag = local(element.tag)
    if tag in {"title", "header"}:
        value = (
            _infobox_value(element, params, page_title)
            if tag == "title"
            else inner(element)
        )
        return (tag, value)
    if tag == "image":
        image = _infobox_value(element, params, page_title)
        if not image:
            return None
        caption_element = element.find("caption")
        caption = ""
        if caption_element is not None:
            caption = params.get(caption_element.get("source", ""), "").strip()
            caption = caption or inner(caption_element)
        return ("image", image, caption)
    if tag == "data":
        return _infobox_data_row(element, params, expanded)
    if tag == "navigation":
        navigation = inner(element)
        if navigation:
            return ("wide", subst(navigation, params))
    return None


def _iter_infobox_rows(
    node: _Element, params: Mapping[str, str], page_title: str, expanded: bool
) -> Iterator[tuple[str, ...]]:
    """Yield rendered rows, descending into content-bearing infobox groups."""
    for element in node:
        if local(element.tag) in {"group", "section", "panel"}:
            nested_rows = list(
                _iter_infobox_rows(element, params, page_title, expanded)
            )
            if any(row[0] in {"data", "image"} for row in nested_rows):
                yield from nested_rows
            continue
        row = _infobox_element_row(element, params, page_title, expanded)
        if row is not None:
            yield row


def _infobox_row_markup(row: tuple[str, ...]) -> list[str]:
    """Serialize one normalized infobox row as table markup."""
    match row:
        case ("image", source, caption):
            filename = re.sub(r"(?i)^(file|image)\s*:", "", source).strip()
            value = (
                source
                if source.startswith("[[")
                else (
                    f"[[File:{filename}|250px"
                    f"{'|' + one_line(caption) if caption else ''}]]"
                )
            )
            return ["<tr>", '<td colspan="2">', value, "</td>", "</tr>"]
        case ("header", heading):
            return [
                "<tr>",
                '<th colspan="2" class="section">',
                f"<strong>{one_line(heading)}</strong>",
                "</th>",
                "</tr>",
            ]
        case ("wide", value):
            return ["<tr>", '<td colspan="2">', value, "</td>", "</tr>"]
        case ("data", label, value) if label:
            return ["<tr>", "<th>", label, "</th>", "<td>", value, "</td>", "</tr>"]
        case ("data", _, value):
            return ["<tr>", '<td colspan="2">', value, "</td>", "</tr>"]
        case _:
            return []


def render_infobox(
    root: _Element, params: Mapping[str, str], page_title: str, expanded: bool = False
) -> str:
    """Render a Portable Infobox definition as an HTML table fragment."""
    rows = list(_iter_infobox_rows(root, params, page_title, expanded))
    title = next((row[1] for row in reversed(rows) if row[0] == "title"), "")
    table = ['<table class="infobox">']
    if title:
        table.append(f"<caption>{esc(one_line(title))}</caption>")
    for row in rows:
        table.extend(_infobox_row_markup(row))
    table.append("</table>")
    return "\n" + "\n".join(table) + "\n"


def render_missing(name: str, tpl: Template) -> str:
    """Keep an unavailable template call and its parameters visible."""
    parts: list[str] = []
    for p in tpl.params:
        v = str(p.value).strip()
        if not v:
            continue
        parts.append(f"{str(p.name).strip()}={v}" if p.showkey else v)
    detail = ": " + "; ".join(parts) if parts else ""
    return (
        f'<span class="missing-template" '
        f'title="Template:{esc(name)} is not in the dump">'
        f"[{name}{detail}]</span>"
    )


NON_CONTENT_NAVBOX_TEMPLATES = frozenset(
    {
        "paradise lost (predux)",
        "railway",
        "renewed hope (predux)",
        "survival school (predux)",
        "the grey mother (predux)",
    }
)


def prepass(
    text: str,
    ctx: object,
    infobox_defs: Mapping[str, object],
    page_title: str,
    stats: InfoboxStats,
) -> str:
    """Expand Portable Infobox calls and retain otherwise-missing templates."""
    code = mwparserfromhell.parse(text)
    template_context = cast("TemplateContext", ctx)
    for tpl in code.filter_templates(recursive=False):  # pyright: ignore[reportUnknownMemberType]
        name = re.sub(r"<!--.*?-->", "", str(tpl.name), flags=re.S).strip()
        if not name or "{" in name or name[0] in "#:" or ":" in name or name.isupper():
            continue  # parser functions, magic words, transclusions, {{{params}}}
        cn = canon(name)
        infobox_def = infobox_defs.get(cn)
        if infobox_def is None:
            infobox_def = infobox_defs.get(cn.casefold())
        if infobox_def is not None:
            params = {str(p.name).strip(): str(p.value).strip() for p in tpl.params}
            code.replace(
                tpl, render_infobox(cast("_Element", infobox_def), params, page_title)
            )
            stats["infoboxes"] += 1
        elif cn.casefold() in NON_CONTENT_NAVBOX_TEMPLATES:
            code.replace(tpl, "")
        elif cn not in template_context.templates:
            code.replace(tpl, render_missing(cn, tpl))
            stats["missing_templates"].append(cn)
    return str(code)


# --------------------------------------------------------------------------- #
# post-expansion rewrites (still wikitext)
# --------------------------------------------------------------------------- #
CAT_RE = re.compile(r"\[\[\s*Category\s*:\s*([^\]|]+?)\s*(?:\|[^\]]*)?\]\]\s*", re.I)
TABBER_RE = re.compile(r"(?is)<tabber\b[^>]*>(.*?)</tabber\s*>")
GALLERY_RE = re.compile(r"(?is)<gallery\b[^>]*>(.*?)</gallery\s*>")
REFS_RE = re.compile(
    r"(?is)<references\b[^>]*?/>|<references\b[^>]*>.*?</references\s*>"
)
MAGIC_RE = re.compile(r"__(?:NO)?(?:TOC|EDITSECTION|INDEX|GALLERY)__|__FORCETOC__")
INFOBOX_LEFT_RE = re.compile(r"(?is)<infobox\b.*?</infobox\s*>")


def tabber_to_wikitext(text: str) -> str:
    """Convert Fandom tabber panels into ordinary wikitext sections."""

    def repl(m: re.Match[str]) -> str:
        out: list[str] = []
        for part in re.split(r"\n?\|-\|", m.group(1)):
            mm = re.match(r"(?s)\s*([^=\n]+?)\s*=\s*(.*)$", part)
            name, body = (
                (mm.group(1).strip(), mm.group(2).strip()) if mm else ("", part.strip())
            )
            if name:
                out.append(f"=== {name} ===\n\n{body}")
            elif body:
                out.append(body)
        return "\n\n" + "\n\n".join(out) + "\n\n"

    return TABBER_RE.sub(repl, text)


def gallery_to_wikitext(text: str) -> str:
    """Convert Fandom gallery markup into a four-column wikitext table."""

    def repl(m: re.Match[str]) -> str:
        items: list[str] = []
        for raw_line in m.group(1).splitlines():
            line = raw_line.strip()
            if not line:
                continue
            fn, _, cap = line.partition("|")
            fn = re.sub(r"(?i)^(file|image)\s*:\s*", "", fn).strip()
            caption = f"|{cap.strip()}" if cap.strip() else ""
            items.append(f"[[File:{fn}|140px{caption}]]")
        if not items:
            return ""
        rows: list[str] = ["{|", f"|+ {SENTINEL}:gallery:"]
        for i in range(0, len(items), 4):
            rows.append("|-")
            rows += ["| " + x for x in items[i : i + 4]]
        rows.append("|}")
        return "\n\n" + "\n".join(rows) + "\n\n"

    return GALLERY_RE.sub(repl, text)


THUMB_OPTS = {"thumb", "thumbnail", "frame", "framed"}
IMG_PLAIN_OPT_RE = re.compile(
    r"^(thumb|thumbnail|frame|framed|frameless|border|left|right|center|centre|"
    r"none|upright.*|baseline|sub|super|top|text-top|middle|bottom|text-bottom|"
    r"\d*x?\d*px|link=.*|alt=.*|class=.*|lang=.*|page=.*|thumb=.*|thumbnail=.*)$",
    re.I,
)


def split_top(s: str) -> list[str]:
    """Split on top-level pipes.

    Pipes inside nested links and templates remain part of their current segment.
    """
    parts: list[str] = []
    cur: list[str] = []
    depth, i = 0, 0
    while i < len(s):
        two = s[i : i + 2]
        if two in ("[[", "{{"):
            depth += 1
            cur.append(two)
            i += 2
        elif two in ("]]", "}}"):
            depth -= 1
            cur.append(two)
            i += 2
        elif s[i] == "|" and depth == 0:
            parts.append("".join(cur))
            cur = []
            i += 1
        else:
            cur.append(s[i])
            i += 1
    parts.append("".join(cur))
    return parts


def thumbs_to_marked(text: str) -> str:
    """Mark thumbnail captions so Pandoc's alt text can become visible text.

    Convert thumb options to a fixed-size image with an explicit caption marker.
    """
    code = mwparserfromhell.parse(text)
    for link in code.filter_wikilinks():  # pyright: ignore[reportUnknownMemberType]
        m = re.match(r"(?is)\s*(?:file|image)\s*:\s*(.*?)\s*$", str(link.title))
        if not m or link.text is None:
            continue
        opts = [o.strip() for o in split_top(str(link.text))]
        if not any(
            o.lower() in THUMB_OPTS or o.lower().startswith(("thumb=", "thumbnail="))
            for o in opts
        ):
            continue
        caps = [o for o in opts if o and not IMG_PLAIN_OPT_RE.match(o)]
        px = next((o for o in opts if re.fullmatch(r"\d*x?\d+px", o)), "220px")
        code.replace(
            link, f"[[File:{m.group(1)}|{px}|XTHUMB {caps[-1] if caps else ''}]]"
        )
    return str(code)


def postexpand(text: str, cats: list[str], stats: InfoboxStats) -> str:
    """Collect categories and normalize Fandom constructs after expansion."""

    def grab(m: re.Match[str]) -> str:
        cats.append(canon(m.group(1)))
        return ""

    text = CAT_RE.sub(grab, text)
    text = REFS_RE.sub("", text)
    text = MAGIC_RE.sub("", text)
    text = tabber_to_wikitext(text)
    text = gallery_to_wikitext(text)
    text = thumbs_to_marked(text)

    def render_leftover_infobox(match: re.Match[str]) -> str:
        try:
            root = etree.fromstring(
                match.group(0).encode("utf-8"), etree.XMLParser(recover=True)
            )
            rendered = render_infobox(root, {}, "", expanded=True)
        except etree.XMLSyntaxError, TypeError, ValueError:
            stats["infobox_left"] += 1
            return ""
        stats["infoboxes"] += 1
        return rendered

    return INFOBOX_LEFT_RE.sub(render_leftover_infobox, text)


# --------------------------------------------------------------------------- #
# pandoc
# --------------------------------------------------------------------------- #


def pandoc_selftest(pandoc: str) -> str:
    """Reject Pandoc builds that silently drop cells after a colspan-only row.

    This catches a long-standing conversion bug before processing the dump.
    """
    version_lines = subprocess.run(  # noqa: S603
        [pandoc, "--version"], capture_output=True, text=True, check=False
    ).stdout.splitlines()
    ver = version_lines[0] if version_lines else "unknown"
    probe = '{|\n|-\n| colspan="2" | Title\n|-\n! Label\n| VALUE-OK\n|}'
    html_out = run_pandoc(pandoc, probe)
    if "VALUE-OK" not in html_out:
        sys.exit(
            f"{ver} drops table cells (colspan bug). Install a newer Pandoc build "
            "or pass --pandoc /path/to/newer/pandoc."
        )
    return ver


def run_pandoc(pandoc: str, text: str) -> str:
    """Convert a MediaWiki fragment to HTML with Pandoc."""
    r = subprocess.run(  # noqa: S603
        [pandoc, "-f", "mediawiki", "-t", "html", "--mathml"],
        input=text,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if r.returncode != 0:
        raise RuntimeError("pandoc: " + r.stderr[:200])
    return r.stdout


# --------------------------------------------------------------------------- #
