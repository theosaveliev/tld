"""Fandom-specific wikitext preprocessing used by the Markdown exporter."""

from __future__ import annotations

import html as htmllib
import importlib.util
import os
import re
import subprocess
import sys
import types
import warnings
from collections.abc import Mapping
from typing import Protocol, TypedDict, cast

import mwparserfromhell
import wikitextprocessor.luaexec as _luaexec
from lxml import etree
from mwparserfromhell.nodes import Template
from wikitextprocessor import Wtp

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
        return os.path.join(os.path.dirname(spec.origin), resource)

    setattr(_pkg, "resource_filename", _resource_filename)
    sys.modules["pkg_resources"] = _pkg


class _ReShim:
    def __getattr__(self, name: str) -> object:
        """Delegate regex operations to the standard-library module."""
        return getattr(re, name)

    def match(
        self, pattern: str | re.Pattern[str], string: str, flags: int = 0
    ) -> re.Match[str] | None:
        if isinstance(pattern, str):
            pattern = pattern.replace("^(?s)", "(?s)^")
        return re.match(pattern, string, flags)


if sys.version_info >= (3, 11):
    setattr(_luaexec, "re", _ReShim())


class TemplateContext(Protocol):
    templates: Mapping[str, str]


class InfoboxStats(TypedDict):
    infoboxes: int
    missing_templates: list[str]
    infobox_left: int


_Element = etree._Element  # pyright: ignore[reportPrivateUsage]
SENTINEL = "XSENT"


def local(tag: str) -> str:
    """Return an XML element tag without its namespace."""
    return tag.rsplit("}", 1)[-1]


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
    template_context = cast(TemplateContext, ctx)
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
        if root is not None:
            raw_name = re.sub(r"(?i)^template\s*:\s*", "", name)
            normalized = canon(raw_name)
            defs[normalized] = root
            defs.setdefault(normalized.casefold(), root)
    return defs


def one_line(s: str) -> str:
    """Collapse an infobox value to one readable line."""
    return re.sub(r"\s*\n\s*", " ", s).strip()


def render_infobox(
    root: _Element,
    params: Mapping[str, str],
    page_title: str,
    expanded: bool = False,
) -> str:
    """Render a Portable Infobox definition as an HTML table fragment."""
    def value(el: _Element) -> str:
        src = el.get("source")
        v = params.get(src, "").strip() if src else ""
        if not v:
            d = el.find("default")
            if d is not None:
                v = subst(inner(d), params).replace("{{PAGENAME}}", page_title).strip()
        return v

    def walk(node: _Element) -> list[tuple[str, ...]]:
        rows: list[tuple[str, ...]] = []
        for el in node:
            tag = local(el.tag)
            if tag == "title":
                rows.append(("title", value(el)))
            elif tag == "image":
                v = value(el)
                if v:
                    cap_el = el.find("caption")
                    cap = ""
                    if cap_el is not None:
                        cap = params.get(cap_el.get("source", ""), "").strip() or inner(
                            cap_el
                        )
                    rows.append(("image", v, cap))
            elif tag == "header":
                rows.append(("header", inner(el)))
            elif tag == "data":
                src = el.get("source")
                v = params.get(src, "").strip() if src else ""
                fmt = el.find("format")
                if fmt is not None and (v or expanded):
                    v = subst(inner(fmt), params).strip()
                if not v:
                    d = el.find("default")
                    if d is not None:
                        v = subst(inner(d), params).strip()
                if v:
                    lab = el.find("label")
                    rows.append(("data", inner(lab) if lab is not None else "", v))
            elif tag == "navigation":
                v = inner(el)
                if v:
                    rows.append(("wide", subst(v, params)))
            elif tag in ("group", "section", "panel"):
                sub = walk(el)
                if any(r[0] in ("data", "image") for r in sub):
                    rows.extend(sub)
        return rows

    title = ""
    table = ['<table class="infobox">']
    for row in walk(root):
        if row[0] == "title":
            title = row[1]
        elif row[0] == "image":
            fn = re.sub(r"(?i)^(file|image)\s*:", "", row[1]).strip()
            value_markup = (
                row[1]
                if row[1].startswith("[[")
                else "[[File:%s|250px%s]]"
                % (fn, "|" + one_line(row[2]) if row[2] else "")
            )
            table.extend(["<tr>", '<td colspan="2">', value_markup, "</td>", "</tr>"])
        elif row[0] == "header":
            table.extend(
                [
                    "<tr>",
                    '<th colspan="2" class="section">',
                    "<strong>%s</strong>" % one_line(row[1]),
                    "</th>",
                    "</tr>",
                ]
            )
        elif row[0] == "wide":
            table.extend(["<tr>", '<td colspan="2">', row[1], "</td>", "</tr>"])
        elif row[0] == "data":
            label, cell_value = row[1], row[2]
            if label:
                table.extend(
                    [
                        "<tr>",
                        "<th>",
                        label,
                        "</th>",
                        "<td>",
                        cell_value,
                        "</td>",
                        "</tr>",
                    ]
                )
            else:
                table.extend(["<tr>", '<td colspan="2">', cell_value, "</td>", "</tr>"])

    if title:
        table.insert(1, "<caption>%s</caption>" % esc(one_line(title)))
    table.append("</table>")
    return "\n" + "\n".join(table) + "\n"


def render_missing(name: str, tpl: Template) -> str:
    """Keep an unavailable template call and its parameters visible."""
    parts: list[str] = []
    for p in tpl.params:
        v = str(p.value).strip()
        if not v:
            continue
        parts.append("%s=%s" % (str(p.name).strip(), v) if p.showkey else v)
    detail = ": " + "; ".join(parts) if parts else ""
    return (
        '<span class="missing-template" title="Template:%s is not in the dump">[%s%s]</span>'
        % (esc(name), name, detail)
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
    template_context = cast(TemplateContext, ctx)
    for tpl in code.filter_templates(recursive=False):
        name = re.sub(r"<!--.*?-->", "", str(tpl.name), flags=re.S).strip()
        if not name or "{" in name or name[0] in "#:" or ":" in name or name.isupper():
            continue  # parser functions, magic words, transclusions, {{{params}}}
        cn = canon(name)
        infobox_def = infobox_defs.get(cn)
        if infobox_def is None:
            infobox_def = infobox_defs.get(cn.casefold())
        if infobox_def is not None:
            params = {str(p.name).strip(): str(p.value).strip() for p in tpl.params}
            code.replace(tpl, render_infobox(cast(_Element, infobox_def), params, page_title))
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
                out.append("=== %s ===\n\n%s" % (name, body))
            elif body:
                out.append(body)
        return "\n\n" + "\n\n".join(out) + "\n\n"

    return TABBER_RE.sub(repl, text)


def gallery_to_wikitext(text: str) -> str:
    """Convert Fandom gallery markup into a four-column wikitext table."""
    def repl(m: re.Match[str]) -> str:
        items: list[str] = []
        for line in m.group(1).splitlines():
            line = line.strip()
            if not line:
                continue
            fn, _, cap = line.partition("|")
            fn = re.sub(r"(?i)^(file|image)\s*:\s*", "", fn).strip()
            items.append(
                "[[File:%s|140px%s]]" % (fn, "|" + cap.strip() if cap.strip() else "")
            )
        if not items:
            return ""
        rows: list[str] = ["{|", "|+ %s:gallery:" % SENTINEL]
        for i in range(0, len(items), 4):
            rows.append("|-")
            rows += ["| " + x for x in items[i : i + 4]]
        rows.append("|}")
        return "\n\n" + "\n".join(rows) + "\n\n"

    return GALLERY_RE.sub(repl, text)


THUMB_OPTS = {"thumb", "thumbnail", "frame", "framed"}
IMG_PLAIN_OPT_RE = re.compile(
    r"^(thumb|thumbnail|frame|framed|frameless|border|left|right|center|centre|none|upright.*|baseline|sub|super|top|"
    r"text-top|middle|bottom|text-bottom|\d*x?\d*px|link=.*|alt=.*|class=.*|lang=.*|page=.*|thumb=.*|thumbnail=.*)$",
    re.I,
)


def split_top(s: str) -> list[str]:
    """Split on | at bracket depth 0 (so [[a|b]] inside a caption stays in one piece)."""
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
    """[[File:x|thumb|caption]] -> [[File:x|220px|XTHUMB caption]] so the caption can be turned into
    visible text after pandoc (pandoc only keeps it as an alt/tooltip for inline images).
    """
    code = mwparserfromhell.parse(text)
    for link in code.filter_wikilinks():
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
            link,
            "[[File:%s|%s|%s %s]]"
            % (m.group(1), px, "XTHUMB", caps[-1] if caps else ""),
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
        except (etree.XMLSyntaxError, TypeError, ValueError):
            stats["infobox_left"] += 1
            return ""
        stats["infoboxes"] += 1
        return rendered

    text = INFOBOX_LEFT_RE.sub(render_leftover_infobox, text)
    return text


# --------------------------------------------------------------------------- #
# pandoc
# --------------------------------------------------------------------------- #

def pandoc_selftest(pandoc: str) -> str:
    """Older pandoc versions (e.g. 3.1.3) silently DROP table cells when the first row is a single
    colspan cell - a very common wikitable layout. Refuse to run with such a build.
    """
    version_lines = subprocess.run(
        [pandoc, "--version"], capture_output=True, text=True, check=False
    ).stdout.splitlines()
    ver = version_lines[0] if version_lines else "unknown"
    probe = '{|\n|-\n| colspan="2" | Title\n|-\n! Label\n| VALUE-OK\n|}'
    html_out = run_pandoc(pandoc, probe)
    if "VALUE-OK" not in html_out:
        sys.exit(
            "%s drops table cells (colspan bug). Use a newer pandoc: `pip install pypandoc_binary` "
            "or pass --pandoc /path/to/newer/pandoc" % ver
        )
    return ver


def run_pandoc(pandoc: str, text: str) -> str:
    """Convert a MediaWiki fragment to HTML with Pandoc."""
    r = subprocess.run(
        [pandoc, "-f", "mediawiki", "-t", "html", "--mathml"],
        input=text,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if r.returncode != 0:
        raise RuntimeError("pandoc: " + r.stderr[:200])
    return r.stdout


# --------------------------------------------------------------------------- #
# site model: slugs, redirects, images
# --------------------------------------------------------------------------- #