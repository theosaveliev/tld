# mypy: ignore-errors
# pyright: reportAny=false
# ruff: noqa

#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["wikitextprocessor", "mwparserfromhell", "lxml", "pypandoc_binary"]
# ///
"""wiki_to_html.py - spike: MediaWiki/Fandom XML dump -> static HTML mirror (no JS)

Pipeline per page
  1. mwparserfromhell  (before expansion)
       - Portable Infobox calls  -> wikitext table built from the call's parameters
         (plain template expansion would lose the values, see README notes below)
       - templates missing from the dump -> visible "[Name: a=1; b=2]" fallback,
         so parameter values are never silently dropped
  2. wikitextprocessor  expands templates, parser functions ({{#if}} ...) and Lua
       (#invoke) using the Template: / Module: pages found in the same dump
  3. small regex passes: <tabber> -> labelled sections, <gallery> -> table,
       categories collected, <references/> removed (pandoc builds the footnotes)
  4. pandoc (-f mediawiki -t html --mathml)
  5. lxml post-processing: wiki links -> relative .html links (redirects resolved),
       images -> ../images/<file>, MediaWiki-style heading ids, TOC, infobox/gallery
       classes, well-formed HTML even if the wikitext had unclosed tags

Output layout (every path is relative, the folder can be moved anywhere):
  out/index.html  out/style.css  out/wiki/<page>.html  out/categories/<cat>.html
  out/images/...  out/report.tsv

Install
  pip install wikitextprocessor mwparserfromhell lxml pypandoc_binary
  (pypandoc_binary ships a recent pandoc; some older system pandoc builds silently drop table
   cells, so the script self-tests pandoc at start-up and refuses to run with a bad build)

Usage
  python wiki_to_html.py dump.xml --out site --images path/to/wikiteam3/images --limit 50
  python wiki_to_html.py dump.xml --out site --images images            # full run
  python wiki_to_html.py dump.xml --out site --limit 50 --compare --source-url https://thelongdark.fandom.com

Dump tips: use a WikiTeam3 dump with the Template, Module and MediaWiki namespaces
included, and prefer --curonly (one revision per page; if there are several the
last one in the file wins).
"""

import warnings

# harmless noise on free-threaded Python (GIL notice for a C extension) and in wikitextprocessor itself
warnings.filterwarnings(
    "ignore", message=".*global interpreter lock.*", category=RuntimeWarning
)
warnings.filterwarnings("ignore", category=SyntaxWarning)

import argparse
import bz2
import contextlib
import gzip
import hashlib
import html as htmllib
import io
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict

import mwparserfromhell
from lxml import etree
from lxml import html as lhtml

# --------------------------------------------------------------------------- #
# wikitextprocessor 0.4.96 (PyPI) uses a regex with a mid-pattern global flag
# that Python >= 3.11 rejects; it breaks every {{#invoke:...}}. Shim it.
# --------------------------------------------------------------------------- #
# wikitextprocessor 0.4.96 also does `import pkg_resources` just to locate its bundled lua/ folder;
# recent setuptools no longer ship pkg_resources, so provide that one function ourselves if needed.
try:
    import pkg_resources  # noqa: F401
except ImportError:
    import importlib.util
    import types

    _pkg = types.ModuleType("pkg_resources")

    def _resource_filename(package, resource):
        spec = importlib.util.find_spec(package)
        return os.path.join(os.path.dirname(spec.origin), resource)

    _pkg.resource_filename = _resource_filename
    sys.modules["pkg_resources"] = _pkg

import wikitextprocessor.luaexec as _luaexec
from wikitextprocessor import Wtp

_re = re


class _ReShim:
    def __getattr__(self, name):
        return getattr(_re, name)

    def match(self, pattern, string, flags=0):
        if isinstance(pattern, str):
            pattern = pattern.replace("^(?s)", "(?s)^")
        return _re.match(pattern, string, flags)


if sys.version_info >= (3, 11):
    _luaexec.re = _ReShim()

SENTINEL = "XSENT"  # marks tables we generate, so we can class them after pandoc
FEATURE_RE = re.compile(r"\{\{|<tabber|<gallery|<math|<ref|\{\||#invoke|<infobox", re.I)


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #
def local(tag):
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def canon(title):
    """MediaWiki-style title: underscores -> spaces, first letter upper-case."""
    t = re.sub(r"\s+", " ", title.replace("_", " ")).strip()
    return t[:1].upper() + t[1:] if t else t


def make_slug(title, used_lower):
    base = re.sub(r"[^\w\-.()' ]+", "_", title).strip().replace(" ", "_") or "page"
    if len(base.encode("utf-8")) > 140:
        base = base[:100] + "_" + hashlib.md5(title.encode()).hexdigest()[:8]
    slug, n = base, 1
    while slug.lower() in used_lower:  # also avoids case-only clashes on macOS/Windows
        n += 1
        slug = f"{base}~{n}"
    used_lower.add(slug.lower())
    return slug + ".html"


def href_quote(name):
    return urllib.parse.quote(name, safe="()'!~-._")


def esc(s):
    return htmllib.escape(s, quote=True)


def open_dump(path):
    if path.endswith(".bz2"):
        return bz2.open(path, "rb")
    if path.endswith(".gz"):
        return gzip.open(path, "rb")
    if path.endswith(".7z"):
        sys.exit(
            "Extract the .7z first (e.g. `7z x dump.xml.7z`) and pass the .xml file."
        )
    return open(path, "rb")


def read_dump(path):
    """Yield dicts: title, ns, model, text, redirect (target title or None)."""
    with open_dump(path) as f:
        for _, el in ET.iterparse(f, events=("end",)):
            if local(el.tag) != "page":
                continue
            title, ns, redirect, model, text = "", 0, None, "wikitext", ""
            for ch in el:
                t = local(ch.tag)
                if t == "title":
                    title = ch.text or ""
                elif t == "ns":
                    ns = int(ch.text or 0)
                elif t == "redirect":
                    redirect = ch.get("title")
                elif t == "revision":  # the last revision in the file wins
                    for r in ch:
                        rt = local(r.tag)
                        if rt == "text":
                            text = r.text or ""
                        elif rt == "model":
                            model = r.text or model
            el.clear()
            if not redirect:
                m = re.match(r"\s*#redirect\s*:?\s*\[\[([^\]|]+)", text, re.I)
                if m:
                    redirect = m.group(1).strip()
            yield {
                "title": title,
                "ns": ns,
                "model": model,
                "text": text,
                "redirect": redirect,
            }


# --------------------------------------------------------------------------- #
# Portable Infobox support (pre-expansion)
# --------------------------------------------------------------------------- #
PARAM_RE = re.compile(r"\{\{\{\s*([^{}|]+?)\s*(?:\|([^{}]*))?\}\}\}")


def subst(text, params):
    return PARAM_RE.sub(lambda m: params.get(m.group(1), m.group(2) or ""), text)


def inner(el):
    return "".join(el.itertext()).strip()


def load_infobox_defs(ctx):
    defs = {}
    for name, body in ctx.templates.items():
        if "<infobox" not in body.lower():
            continue
        m = re.search(r"(?is)<infobox\b.*?</infobox\s*>", body)
        if not m:
            continue
        root = etree.fromstring(
            m.group(0).encode("utf-8"), etree.XMLParser(recover=True)
        )
        if root is not None:
            defs[canon(name)] = root
    return defs


def one_line(s):
    return re.sub(r"\s*\n\s*", " ", s).strip()


def render_infobox(root, params, page_title):
    def value(el):
        src = el.get("source")
        v = params.get(src, "").strip() if src else ""
        if not v:
            d = el.find("default")
            if d is not None:
                v = subst(inner(d), params).replace("{{PAGENAME}}", page_title).strip()
        return v

    def walk(node):
        rows = []
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
                if v and fmt is not None:
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
    body = []
    for r in walk(root):
        if r[0] == "title":
            title = r[1]
        elif r[0] == "image":
            fn = re.sub(r"(?i)^(file|image)\s*:", "", r[1]).strip()
            v = (
                r[1]
                if r[1].startswith("[[")
                else "[[File:%s|250px%s]]" % (fn, "|" + one_line(r[2]) if r[2] else "")
            )
            body += ["|-", '| colspan="2" | ' + v]
        elif r[0] == "header":
            body += ["|-", "| colspan=\"2\" | '''%s'''" % one_line(r[1])]
        elif r[0] == "wide":
            body += ["|-", '| colspan="2" | ' + r[1]]
        elif r[0] == "data":
            label, v = r[1], r[2]
            cell = ("|\n" + v) if "\n" in v else ("| " + v)
            if label:
                body += ["|-", "! " + one_line(label), cell]
            else:
                body += ["|-", '| colspan="2" | ' + v]
    lines = ["{|", "|+ %s:infobox:%s" % (SENTINEL, one_line(title))] + body + ["|}"]
    return "\n" + "\n".join(lines) + "\n"


def render_missing(name, tpl):
    parts = []
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


def prepass(text, ctx, infobox_defs, page_title, stats):
    code = mwparserfromhell.parse(text)
    for tpl in code.filter_templates(recursive=False):
        name = re.sub(r"<!--.*?-->", "", str(tpl.name), flags=re.S).strip()
        if not name or "{" in name or name[0] in "#:" or ":" in name or name.isupper():
            continue  # parser functions, magic words, transclusions, {{{params}}}
        cn = canon(name)
        if cn in infobox_defs:
            params = {str(p.name).strip(): str(p.value).strip() for p in tpl.params}
            code.replace(tpl, render_infobox(infobox_defs[cn], params, page_title))
            stats["infoboxes"] += 1
        elif cn not in ctx.templates:
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


def tabber_to_wikitext(text):
    def repl(m):
        out = []
        for part in re.split(r"\n?\|-\|", m.group(1)):
            mm = re.match(r"(?s)\s*([^=\n]+?)\s*=\s*(.*)$", part)
            name, body = (
                (mm.group(1).strip(), mm.group(2).strip()) if mm else ("", part.strip())
            )
            if name:
                out.append("'''%s'''\n\n%s" % (name, body))
            elif body:
                out.append(body)
        return "\n\n" + "\n\n".join(out) + "\n\n"

    return TABBER_RE.sub(repl, text)


def gallery_to_wikitext(text):
    def repl(m):
        items = []
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
        rows = ["{|", "|+ %s:gallery:" % SENTINEL]
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


def split_top(s):
    """Split on | at bracket depth 0 (so [[a|b]] inside a caption stays in one piece)."""
    parts, cur, depth, i = [], [], 0, 0
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


def thumbs_to_marked(text):
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


def postexpand(text, cats, stats):
    def grab(m):
        cats.append(canon(m.group(1)))
        return ""

    text = CAT_RE.sub(grab, text)
    text = REFS_RE.sub("", text)
    text = MAGIC_RE.sub("", text)
    text = tabber_to_wikitext(text)
    text = gallery_to_wikitext(text)
    text = thumbs_to_marked(text)
    left = INFOBOX_LEFT_RE.findall(text)
    if left:
        stats["infobox_left"] += len(left)
        text = INFOBOX_LEFT_RE.sub("", text)
    return text


# --------------------------------------------------------------------------- #
# pandoc
# --------------------------------------------------------------------------- #
def find_pandoc(explicit=None):
    """--pandoc PATH, else the binary bundled with pypandoc_binary (recent), else `pandoc` on PATH."""
    if explicit:
        return explicit
    try:
        import pypandoc

        return pypandoc.get_pandoc_path()
    except Exception:
        pass
    p = shutil.which("pandoc")
    if not p:
        sys.exit("pandoc not found: `pip install pypandoc_binary` or install pandoc")
    return p


def pandoc_selftest(pandoc):
    """Older pandoc versions (e.g. 3.1.3) silently DROP table cells when the first row is a single
    colspan cell - a very common wikitable layout. Refuse to run with such a build.
    """
    ver = subprocess.run(
        [pandoc, "--version"], capture_output=True, text=True
    ).stdout.splitlines()[0]
    probe = '{|\n|-\n| colspan="2" | Title\n|-\n! Label\n| VALUE-OK\n|}'
    html_out = run_pandoc(pandoc, probe)
    if "VALUE-OK" not in html_out:
        sys.exit(
            "%s drops table cells (colspan bug). Use a newer pandoc: `pip install pypandoc_binary` "
            "or pass --pandoc /path/to/newer/pandoc" % ver
        )
    return ver


def run_pandoc(pandoc, text):
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
class Site:
    def __init__(self, args):
        self.args = args
        self.slugs = {}  # canonical title -> wiki/<file>.html  (pages we render)
        self.redirects = {}  # canonical title -> target text
        self.exists = set()  # every title present in the dump
        self.cats = defaultdict(set)  # category -> member titles
        self.cat_slugs = {}
        self.cat_used = set()
        self.images = {}  # normalised name -> real file name
        self.copied = set()
        self.index_images()

    def index_images(self):
        d = self.args.images
        if not d:
            return
        for root, _, files in os.walk(d):
            for fn in files:
                if fn.endswith(".desc"):
                    continue
                self.images.setdefault(self.norm_img(fn), os.path.join(root, fn))

    @staticmethod
    def norm_img(name):
        return urllib.parse.unquote(name).replace(" ", "_").lower()

    def cat_slug(self, name):
        if name not in self.cat_slugs:
            self.cat_slugs[name] = make_slug(name, self.cat_used)
        return self.cat_slugs[name]

    def resolve(self, raw):
        raw = urllib.parse.unquote(raw)
        title, _, frag = raw.partition("#")
        title = canon(title)
        for _ in range(6):
            tgt = self.redirects.get(title)
            if tgt is None:
                break
            t, _, f = tgt.partition("#")
            title, frag = canon(t), frag or f
        return title, frag

    def copy_image(self, name):
        """Return the file name to use under images/, or None if missing."""
        key = self.norm_img(name)
        src = self.images.get(key)
        if not src:
            return None
        dest_name = os.path.basename(src)
        if dest_name not in self.copied:
            os.makedirs(os.path.join(self.args.out, "images"), exist_ok=True)
            shutil.copy2(src, os.path.join(self.args.out, "images", dest_name))
            self.copied.add(dest_name)
        return dest_name


# --------------------------------------------------------------------------- #
# HTML post-processing
# --------------------------------------------------------------------------- #
def finalize(html_in, site, page_title, stats):
    root = lhtml.fragment_fromstring(html_in or "<p></p>", create_parent="div")

    # tables we generated -> class; infobox rows -> th
    for table in root.iter("table"):
        cap = table.find("caption")
        if cap is None or not (cap.text or "").startswith(SENTINEL + ":"):
            continue
        m = re.match(SENTINEL + r":(\w+):(.*)", cap.text, re.S)
        table.set("class", m.group(1))
        cap.text = m.group(2)
        if not cap.text.strip() and len(cap) == 0:
            table.remove(cap)
        if m.group(1) == "infobox":
            for tr in table.iter("tr"):
                tds = [c for c in tr if c.tag == "td"]
                if len(tds) == 2:
                    tds[0].tag = "th"
                elif len(tds) == 1 and tds[0].get("colspan") == "2":
                    kids = [k for k in tds[0] if k.tag == "p"]
                    if (
                        len(kids) == 1
                        and len(kids[0]) == 1
                        and kids[0][0].tag == "strong"
                        and not (kids[0].text or "").strip()
                    ):
                        tds[0].tag = "th"
                        tds[0].set("class", "section")

    # images
    for img in root.iter("img"):
        src = urllib.parse.unquote(img.get("src", ""))
        name = re.sub(r"(?i)^(file|image):", "", src).strip()
        real = site.copy_image(name) if site.images else name.replace(" ", "_")
        if real is None:
            stats["missing_images"].append(name)
            img.set("class", "missing-image")
            real = name.replace(" ", "_")
        img.set("src", "../images/" + href_quote(real))
        img.set("loading", "lazy")
        if (
            img.get("title") == name
        ):  # pandoc fills title/caption with the file name when none was given
            img.attrib.pop("title")
    for img in list(root.iter("img")):
        label = img.get("alt") or img.get("title") or ""
        if not label.startswith("XTHUMB"):
            continue
        cap = label[len("XTHUMB") :].strip()
        for k in ("alt", "title"):
            img.attrib.pop(k, None)
        if cap:
            img.set("alt", cap)
        parent = img.getparent()
        if parent.tag == "figure":
            parent.set("class", "thumb")
            fc = parent.find("figcaption")
            if fc is not None:
                parent.remove(fc)
            if cap:
                fc = lhtml.fromstring("<figcaption></figcaption>")
                fc.text = cap
                parent.append(fc)
        else:
            box = lhtml.fromstring('<span class="thumb"></span>')
            img.addprevious(box)
            box.append(img)
            if cap:
                c = lhtml.fromstring('<span class="thumbcaption"></span>')
                c.text = cap
                box.append(c)
            img.tail, box.tail = None, img.tail
    for fig in list(root.iter("figcaption")):
        img = fig.getparent().find("img")
        txt = (fig.text or "").strip()
        if (
            img is not None
            and not len(fig)
            and txt
            and site.norm_img(txt)
            == site.norm_img(os.path.basename(urllib.parse.unquote(img.get("src", ""))))
        ):
            fig.getparent().remove(fig)

    # headings: MediaWiki-style ids
    used, heads = set(), []
    for h in root.iter("h1", "h2", "h3", "h4", "h5", "h6"):
        base = h.text_content().strip().replace(" ", "_") or "section"
        hid, n = base, 1
        while hid in used:
            n += 1
            hid = "%s_%d" % (base, n)
        used.add(hid)
        h.set("id", hid)
        heads.append((int(h.tag[1]), h.text_content().strip(), hid))

    # links
    for a in list(root.iter("a")):
        href = a.get("href")
        if href is None or href.startswith("#"):
            continue
        has_scheme = re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", href) is not None
        if has_scheme and not href.lower().startswith(
            ("file:", "image:", "media:", "category:")
        ):
            continue  # external link (https:, mailto: ...) is left alone
        a.attrib.pop("title", None)
        a.attrib.pop("class", None)
        raw = urllib.parse.unquote(href).lstrip(":")
        m = re.match(r"(?i)^(file|image|media)\s*:\s*(.*)$", raw)
        if m:
            real = site.copy_image(m.group(2))
            if real:
                a.set("href", "../images/" + href_quote(real))
            else:
                _redlink(a, stats, existing=False)
            continue
        m = re.match(r"(?i)^category\s*:\s*([^#]*)", raw)
        if m:
            name = canon(m.group(1))
            site.cats[name]  # make sure the category page will exist
            a.set("href", "../categories/" + href_quote(site.cat_slug(name)))
            continue
        title, frag = site.resolve(raw)
        slug = site.slugs.get(title)
        if slug:
            a.set(
                "href",
                href_quote(slug)
                + ("#" + href_quote(frag.replace(" ", "_")) if frag else ""),
            )
            a.set("title", title)
        else:
            _redlink(a, stats, existing=title in site.exists)

    # TOC
    toc = ""
    if len(heads) >= 4:
        items = "".join(
            '<li class="lvl%d"><a href="#%s">%s</a></li>' % (lvl, esc(hid), esc(txt))
            for lvl, txt, hid in heads
        )
        toc = '<nav class="toc"><strong>Contents</strong><ul>%s</ul></nav>' % items

    # footnotes block without a heading above it
    for sec in root.iter("section", "aside"):
        if "footnotes" in (sec.get("class") or ""):
            prev = sec.getprevious()
            if prev is None or prev.tag not in ("h1", "h2", "h3", "h4"):
                sec.insert(0, lhtml.fromstring("<h2>Notes</h2>"))

    out = (root.text or "") + "".join(
        etree.tostring(c, encoding="unicode", method="html") for c in root
    )
    return out, toc, root


def _redlink(a, stats, existing):
    a.tag = "span"
    a.attrib.pop("href", None)
    a.set("class", "unrendered" if existing else "redlink")
    stats["unrendered" if existing else "redlinks"] += 1


# --------------------------------------------------------------------------- #
# templates for output pages
# --------------------------------------------------------------------------- #
CSS = """\
:root{--text:#202122;--link:#0645ad;--visited:#0b0080;--red:#ba0000;--border:#a2a9b1;--panel:#f8f9fa;--head:#eaecf0}
*{box-sizing:border-box}
body{margin:0;font:16px/1.6 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;color:var(--text);background:#fff}
header.site{background:var(--panel);border-bottom:1px solid var(--border);padding:.6em 1.2em;font-weight:600}
header.site a{text-decoration:none}
main{max-width:64rem;margin:0 auto;padding:1em 1.5em 3em}
h1.page-title{font:normal 1.9em Georgia,"Linux Libertine",Times,serif;border-bottom:1px solid var(--border);margin:.4em 0 .6em}
h2,h3,h4{font-family:Georgia,"Linux Libertine",Times,serif;font-weight:normal}
h2{font-size:1.5em;border-bottom:1px solid var(--border);margin-top:1.2em}
a{color:var(--link)} a:visited{color:var(--visited)}
.redlink{color:var(--red)} .missing-template{background:#fff3cd;padding:0 .25em;border-radius:3px;font-size:.9em}
.missing-image{outline:2px dashed var(--red);min-width:40px;min-height:20px}
img{max-width:100%;height:auto} figure{margin:.5em 0} figcaption{font-size:.85em;color:#54595d}
table{border-collapse:collapse} td p,th p{margin:.2em 0}
table:not(.infobox):not(.gallery) td,table:not(.infobox):not(.gallery) th{border:1px solid var(--border);padding:.3em .6em}
table:not(.infobox):not(.gallery) thead th{background:var(--head)}
table.infobox{float:right;clear:right;width:22em;max-width:100%;margin:0 0 1em 1.2em;background:var(--panel);border:1px solid var(--border);font-size:.9em}
table.infobox caption{font-weight:700;background:var(--head);border:1px solid var(--border);border-bottom:0;padding:.4em;text-align:center}
table.infobox th,table.infobox td{padding:.3em .5em;vertical-align:top;border-top:1px solid var(--head)}
table.infobox th{text-align:left;width:38%} table.infobox th.section{text-align:center;background:var(--head);width:auto}
table.infobox td[colspan]{text-align:center}
table.gallery td{text-align:center;vertical-align:top;padding:.4em;border:0}
nav.toc{display:inline-block;background:var(--panel);border:1px solid var(--border);padding:.5em 1em;margin:.5em 0 1em;font-size:.9em}
nav.toc ul{list-style:none;margin:.3em 0 0;padding:0} nav.toc li.lvl3{margin-left:1em} nav.toc li.lvl4{margin-left:2em}
footer.cats{clear:both;margin-top:2em;padding:.5em .8em;background:var(--panel);border:1px solid var(--border);font-size:.9em}
p.attrib{font-size:.8em;color:#54595d;clear:both}
section.footnotes{font-size:.9em}
blockquote{margin:1em 2em;color:#444}
span.thumb,figure.thumb{display:block;float:right;clear:right;max-width:240px;margin:.3em 0 .8em 1.2em;padding:4px;
border:1px solid var(--border);background:var(--panel);font-size:.85em;text-align:center}
span.thumb img,figure.thumb img{display:block;margin:0 auto 4px}
.letters a{margin-right:.5em}
"""


def layout(title, body, depth, site_name, extra_head=""):
    root = "../" if depth else ""
    return (
        '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<title>%s - %s</title><link rel="stylesheet" href="%sstyle.css">%s</head><body>'
        '<header class="site"><a href="%sindex.html">%s</a></header><main>%s</main></body></html>\n'
        % (esc(title), esc(site_name), root, extra_head, root, esc(site_name), body)
    )


# --------------------------------------------------------------------------- #
# offline content-loss check: words in the wikitext we handed to pandoc vs words in the HTML
# --------------------------------------------------------------------------- #
def tokens(text):
    return re.findall(r"\w+", text.lower())


IMG_OPT_RE = re.compile(
    r"^(thumb|thumbnail|frame|frameless|border|left|right|center|centre|none|upright.*|baseline|sub|super|top|"
    r"text-top|middle|bottom|text-bottom|\d*x?\d*px|link=.*|alt=.*|class=.*|lang=.*|page=.*)$",
    re.I,
)


def source_tokens(wikitext):
    """Approximate the *visible* words of a wikitext snippet (markup, file names, attributes removed)."""
    t = htmllib.unescape(wikitext)
    file_words = set()
    for m in re.finditer(r"(?i)\[\[\s*(?:file|image)\s*:([^|\]]*)", t):
        file_words.update(tokens(m.group(1)))

    def img(m):
        return " ".join(
            x for x in m.group(1).split("|")[1:] if not IMG_OPT_RE.match(x.strip())
        )

    t = re.sub(r"(?is)<!--.*?-->", " ", t)
    t = re.sub(r"(?is)\[\[\s*(?:file|image)\s*:(.*?)\]\](?!\])", img, t)
    t = re.sub(r"\[\[([^\]\[|]*)\|([^\]\[]*)\]\]", r"\2", t)
    t = re.sub(r"\[\[([^\]\[]*)\]\]", r"\1", t)
    t = re.sub(r"\[https?://\S+\s+([^\]]*)\]", r"\1", t)
    t = re.sub(r"https?://\S+", " ", t)
    t = re.sub(r"<[^>]+>", " ", t)
    t = re.sub(
        r"\b(?:colspan|rowspan|class|style|width|height|align|valign|bgcolor|border|cellpadding|"
        r"cellspacing|id|title|src|href|alt)\s*=\s*(\"[^\"]*\"|'[^']*'|\S+)",
        " ",
        t,
    )
    t = re.sub(SENTINEL + r":\w+:", " ", t).replace("XTHUMB", " ")
    return set(tokens(t)) - file_words


def lost_tokens(wikitext, out_root):
    """Words present in the source wikitext but absent from the rendered page (sorted)."""
    if out_root is None:
        return []
    return sorted(source_tokens(wikitext) - set(tokens(out_root.text_content())))


# --------------------------------------------------------------------------- #
# live comparison (optional)
# --------------------------------------------------------------------------- #


def live_coverage(base, title, mine_root, ua):
    url = (
        "%s/api.php?action=parse&page=%s&prop=text&format=json&disableeditsection=1&redirects=1"
        % (base.rstrip("/"), urllib.parse.quote(title))
    )
    req = urllib.request.Request(url, headers={"User-Agent": ua})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.load(r)
    live = lhtml.fragment_fromstring(data["parse"]["text"]["*"], create_parent="div")
    for bad in live.xpath(
        "//script|//style|//*[contains(@class,'mw-editsection')]|//*[contains(@class,'toc')]"
    ):
        bad.getparent().remove(bad)
    live_t = Counter(tokens(live.text_content()))
    mine = set(tokens(mine_root.text_content()))
    if not live_t:
        return None, ""
    hit = sum(1 for t in live_t if t in mine)
    missing = [w for w, _ in live_t.most_common() if w not in mine][:12]
    return hit / len(live_t), " ".join(missing)


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("dump")
    ap.add_argument("--out", default="site")
    ap.add_argument(
        "--images", help="WikiTeam3 images directory (files are copied when referenced)"
    )
    ap.add_argument(
        "--ns", default="0", help="comma separated namespace ids to render (default: 0)"
    )
    ap.add_argument(
        "--limit",
        type=int,
        help="render only N pages (half of them chosen among pages using templates/tabs/galleries/refs/...)",
    )
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--titles", help="file with one page title per line to render")
    ap.add_argument("--site-name", default="Wiki mirror")
    ap.add_argument(
        "--source-url",
        help="original wiki base URL, e.g. https://thelongdark.fandom.com (adds attribution links; used by --compare)",
    )
    ap.add_argument(
        "--compare",
        action="store_true",
        help="compare word coverage with the live wiki (needs --source-url)",
    )
    ap.add_argument(
        "--delay", type=float, default=1.0, help="seconds between live requests"
    )
    ap.add_argument(
        "--pandoc",
        help="path to a pandoc binary (default: pypandoc's bundled one, else PATH)",
    )
    args = ap.parse_args()
    if args.compare and not args.source_url:
        sys.exit("--compare needs --source-url")

    out = args.out
    os.makedirs(os.path.join(out, "wiki"), exist_ok=True)
    os.makedirs(os.path.join(out, "categories"), exist_ok=True)
    wanted_ns = {int(x) for x in args.ns.split(",") if x.strip()}
    pandoc = find_pandoc(args.pandoc)
    print("Using " + pandoc_selftest(pandoc) + " (" + pandoc + ")", flush=True)
    site = Site(args)

    # ---- 1. load the dump ------------------------------------------------- #
    print("Reading dump ...", flush=True)
    ctx = Wtp(quiet=True, num_threads=1)
    cands = []  # titles we may render
    n = 0
    for p in read_dump(args.dump):
        n += 1
        title = canon(p["title"])
        site.exists.add(title)
        if p["redirect"]:
            ctx.add_page("redirect", p["title"], p["redirect"])
            site.redirects[title] = p["redirect"]
        else:
            ctx.add_page(p["model"], p["title"], p["text"])
            if p["ns"] in wanted_ns and p["model"] == "wikitext":
                cands.append(title)
    ctx.analyze_templates()
    infobox_defs = load_infobox_defs(ctx)
    print(
        "  %d pages, %d renderable, %d templates, %d portable-infobox templates"
        % (n, len(cands), len(ctx.templates), len(infobox_defs)),
        flush=True,
    )

    used = set()
    for t in sorted(cands):
        site.slugs[t] = make_slug(t, used)

    # ---- 2. choose pages -------------------------------------------------- #
    todo = sorted(cands)
    if args.titles:
        want = {
            canon(l.strip()) for l in open(args.titles, encoding="utf-8") if l.strip()
        }
        todo = [t for t in todo if t in want]
    elif args.limit and args.limit < len(todo):
        rnd = random.Random(args.seed)
        featured = [t for t in todo if FEATURE_RE.search(ctx.read_by_title(t) or "")]
        fset = set(featured)
        plain = [t for t in todo if t not in fset]
        k = args.limit // 2
        pick = rnd.sample(featured, min(k, len(featured)))
        pick += rnd.sample(plain, min(args.limit - len(pick), len(plain)))
        todo = sorted(pick)
    print("Rendering %d pages ..." % len(todo), flush=True)

    # ---- 3. render -------------------------------------------------------- #
    report = []
    rendered = set()
    t0 = time.time()
    for i, title in enumerate(todo, 1):
        stats = {
            "infoboxes": 0,
            "missing_templates": [],
            "infobox_left": 0,
            "missing_images": [],
            "redlinks": 0,
            "unrendered": 0,
        }
        status, err, cats = "ok", "", []
        try:
            raw = ctx.read_by_title(title) or ""
            ctx.start_page(title)
            text = prepass(raw, ctx, infobox_defs, title, stats)
            try:
                with contextlib.redirect_stdout(
                    io.StringIO()
                ):  # wikitextprocessor prints Lua errors; we report them instead
                    text = ctx.expand(text, timeout=30)
            except Exception as e:  # keep going with the unexpanded text
                status, err = "expand_failed", repr(e)[:200]
            wt_errors = [str(e)[:120] for e in ctx.errors[:3]]
            expand_markers = len(re.findall(r'<strong class="error">', text))
            text = postexpand(text, cats, stats)
            body_html = run_pandoc(pandoc, text)
            body, toc, root = finalize(body_html, site, title, stats)
            lost = lost_tokens(text, root)
        except Exception as e:
            status, err = "failed", repr(e)[:200]
            body, toc, root, wt_errors, expand_markers = (
                "<p><em>Rendering failed.</em></p>",
                "",
                None,
                [],
                0,
            )
            lost = []

        cats = sorted(set(cats))
        for c in cats:
            site.cats[c].add(title)
        plain_text = root.text_content() if root is not None else ""
        flags = []
        if "{{" in plain_text:
            flags.append("braces")
        if stats["infobox_left"]:
            flags.append("infobox_left")
        if "Script error" in plain_text:
            flags.append("script_error")
        if len(lost) >= 3:
            flags.append("lost_text")
        cov, missing_words = "", ""
        if args.compare and root is not None:
            try:
                time.sleep(args.delay)
                c, mw = live_coverage(
                    args.source_url,
                    title,
                    root,
                    "wiki-to-html-spike/0.1 (personal archive)",
                )
                cov, missing_words = ("%.3f" % c if c is not None else ""), mw
            except Exception as e:
                missing_words = "live fetch failed: %s" % str(e)[:60]

        foot = ""
        if cats:
            foot = '<footer class="cats">Categories: %s</footer>' % " | ".join(
                '<a href="../categories/%s">%s</a>'
                % (href_quote(site.cat_slug(c)), esc(c))
                for c in cats
            )
        attrib = ""
        if args.source_url:
            attrib = (
                '<p class="attrib">Archived copy of <a href="%s/wiki/%s">%s</a>. '
                "Text is available under CC BY-SA unless the source wiki says otherwise.</p>"
                % (
                    esc(args.source_url.rstrip("/")),
                    esc(urllib.parse.quote(title.replace(" ", "_"))),
                    esc(title),
                )
            )
        page = layout(
            title,
            '<h1 class="page-title">%s</h1>%s<div class="content">%s</div>%s%s'
            % (esc(title), toc, body, foot, attrib),
            1,
            args.site_name,
        )
        with open(
            os.path.join(out, "wiki", site.slugs[title]), "w", encoding="utf-8"
        ) as f:
            f.write(page)
        rendered.add(title)
        report.append(
            [
                title,
                status,
                len(tokens(plain_text)),
                stats["infoboxes"],
                ";".join(sorted(set(stats["missing_templates"]))),
                expand_markers + len(wt_errors),
                len(stats["missing_images"]),
                stats["redlinks"],
                stats["unrendered"],
                ",".join(flags),
                len(lost),
                " ".join(lost[:8]),
                cov,
                missing_words,
                err or " | ".join(wt_errors),
            ]
        )
        if i % 100 == 0 or i == len(todo):
            print(
                "  %d/%d  (%.1f pages/s)" % (i, len(todo), i / (time.time() - t0)),
                flush=True,
            )

    # ---- 4. redirects, categories, index, css, report --------------------- #
    for src in site.redirects:
        dest, frag = site.resolve(src)
        if dest in rendered and src not in site.slugs:
            fn = make_slug(src, used)
            link = href_quote(site.slugs[dest]) + (
                "#" + href_quote(frag.replace(" ", "_")) if frag else ""
            )
            body = '<p>Redirect to <a href="%s">%s</a>.</p>' % (link, esc(dest))
            with open(os.path.join(out, "wiki", fn), "w", encoding="utf-8") as f:
                f.write(
                    layout(
                        src,
                        '<h1 class="page-title">%s</h1>%s' % (esc(src), body),
                        1,
                        args.site_name,
                        '<meta http-equiv="refresh" content="0; url=%s">' % esc(link),
                    )
                )

    for cat, members in site.cats.items():
        items = (
            "".join(
                '<li><a href="../wiki/%s">%s</a></li>'
                % (href_quote(site.slugs[m]), esc(m))
                for m in sorted(members)
                if m in site.slugs
            )
            or "<li><em>No rendered pages in this run.</em></li>"
        )
        with open(
            os.path.join(out, "categories", site.cat_slug(cat)), "w", encoding="utf-8"
        ) as f:
            f.write(
                layout(
                    "Category: " + cat,
                    '<h1 class="page-title">Category: %s</h1><ul>%s</ul>'
                    % (esc(cat), items),
                    1,
                    args.site_name,
                )
            )

    groups = defaultdict(list)
    for t in sorted(rendered, key=lambda x: (x.lower(), x)):
        groups[t[:1].upper() if t[:1].isalpha() else "#"].append(t)
    letters = " ".join(
        '<a href="#L%s">%s</a>' % (esc(g), esc(g)) for g in sorted(groups)
    )
    sections = "".join(
        '<h2 id="L%s">%s</h2><ul>%s</ul>'
        % (
            esc(g),
            esc(g),
            "".join(
                '<li><a href="wiki/%s">%s</a></li>'
                % (href_quote(site.slugs[t]), esc(t))
                for t in groups[g]
            ),
        )
        for g in sorted(groups)
    )
    cats_idx = "".join(
        '<li><a href="categories/%s">%s</a> (%d)</li>'
        % (href_quote(site.cat_slug(c)), esc(c), len(m))
        for c, m in sorted(site.cats.items())
    )
    main_link = (
        '<p><a href="wiki/%s">Main Page</a></p>' % href_quote(site.slugs["Main Page"])
        if "Main Page" in rendered
        else ""
    )
    body = (
        '<h1 class="page-title">%s</h1>%s<p class="letters">%s</p>%s<h2>Categories</h2><ul>%s</ul>'
        % (esc(args.site_name), main_link, letters, sections, cats_idx)
    )
    with open(os.path.join(out, "index.html"), "w", encoding="utf-8") as f:
        f.write(layout(args.site_name, body, 0, args.site_name))
    with open(os.path.join(out, "style.css"), "w", encoding="utf-8") as f:
        f.write(CSS)

    cols = [
        "title",
        "status",
        "words",
        "infoboxes",
        "missing_templates",
        "expand_errors",
        "missing_images",
        "redlinks",
        "links_to_unrendered",
        "flags",
        "lost_words",
        "lost_words_sample",
        "live_coverage",
        "live_words_missing_here",
        "error",
    ]
    with open(os.path.join(out, "report.tsv"), "w", encoding="utf-8") as f:
        f.write("\t".join(cols) + "\n")
        for row in report:
            f.write(
                "\t".join(str(x).replace("\t", " ").replace("\n", " ") for x in row)
                + "\n"
            )

    bad = [r for r in report if r[1] != "ok" or r[9] or r[4] or r[5]]
    print(
        "\nDone: %d pages written to %s/ (open %s/index.html)"
        % (len(rendered), out, out)
    )
    print(
        "Pages with issues (status/flags/missing templates): %d  -> see %s/report.tsv"
        % (len(bad), out)
    )
    if args.compare:
        covs = [float(r[12]) for r in report if r[12]]
        if covs:
            print(
                "Live word coverage: mean %.1f%%, min %.1f%%"
                % (100 * sum(covs) / len(covs), 100 * min(covs))
            )
    if args.limit or args.titles:
        print(
            "Note: sample run - links to pages outside the sample point at files that exist only in a full run."
        )


if __name__ == "__main__":
    main()
