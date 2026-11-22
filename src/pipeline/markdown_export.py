# pyright: reportAny=false
"""Convert a MediaWiki XML dump into a linked Markdown workspace."""

from __future__ import annotations

import contextlib
import hashlib
import html
import importlib
import io
import json
import re
import shutil
import subprocess
import unicodedata
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, TypedDict, cast

import defusedxml.ElementTree as SafeET
from lxml import etree
from lxml import html as lhtml

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Mapping
    from xml.etree.ElementTree import Element


REDIRECT_RE = re.compile(r"\s*#redirect\s*:?\s*\[\[([^\]|]+)", re.IGNORECASE)
MAX_FILENAME_BYTES = 255
INFOBOX_VALUE_CELL_COUNT = 2
WIDE_TABLE_COLUMN_THRESHOLD = 8
QUOTE_TABLE_CELL_COUNT = 3
MINIMUM_HEADINGS_FOR_TOC = 4
NAVBOX_ERROR_LINE_RE = re.compile(
    r"(?im)^.*Lua execution error in Module:Navbox function navbox.*$"
)
ITALIC_ASTERISK_RE = re.compile(
    r"(?<![\\*])\*(?!\*)(?P<content>\S(?:[^*\n]*?\S)?)\*(?!\*)"
)
STRONG_UNDERSCORE_RE = re.compile(r"(?<!_)__(?P<content>\S(?:[^\n]*?\S)?)__(?!_)")
LANGUAGE_INTERWIKI_SCHEME_RE = re.compile(r"^[a-z]{2,3}$")
RECIPE_TEMPLATE_RE = re.compile(r"\{\{\s*Infobox recipe\b([\s\S]*?)\}\}", re.IGNORECASE)
USES_MATERIAL_LIST_RE = re.compile(
    r"\{\{\s*Uses material list(?:\s*\|\s*([^{}]*?))?\s*\}\}", re.IGNORECASE
)
BACKTICK = chr(96)
TABLE_CELL_LIST_BREAK_THRESHOLD = 8
REDUNDANT_IMAGE_FILENAME_CAPTION_RE = re.compile(
    r"(?P<image>!\[(?:\\.|[^\]])*\]\((?P<destination>[^)]+)\))"
    r"\s*/\s*_(?P<caption>(?:\\.|[^_])*)_"
)
INLINE_CODE_SPAN_RE = re.compile(
    "("
    + re.escape(BACKTICK)
    + r"+[^"
    + BACKTICK
    + r"\n]*"
    + re.escape(BACKTICK)
    + r"+)"
)
FENCED_CODE_RE = re.compile(r"^[ \t]{0,3}(" + re.escape(BACKTICK) + r"{3,}|~{3,})")
EXTERNAL_SCHEMES = frozenset({"ftp", "http", "https", "mailto", "tel"})
LOCAL_WIKI_SCHEMES = frozenset(
    {"category", "file", "image", "media", "module", "special", "template"}
)
NAMED_INTERWIKI_SCHEMES = frozenset({"c", "w", "wikia", "wikipedia"})


class ExportSummary(TypedDict):
    article_pages: int
    exported_pages: int
    redirects: int
    expansion_errors: int
    conversion_errors: int
    repaired_pages: int
    raw_fallback_pages: int
    html_fallback_pages: int
    unresolved_links: int
    missing_images: int
    categories: int
    infoboxes: int
    unconverted_infoboxes: int
    missing_templates: int


class PageExportResult(TypedDict):
    title: str
    filename: str
    status: str
    categories: int
    infoboxes: int
    infoboxes_unconverted: int
    missing_templates: list[str]
    error: str


class ExportReport(TypedDict):
    summary: ExportSummary
    pages: list[PageExportResult]
    unresolved_links: list[str]
    missing_images: list[str]


@dataclass(frozen=True, slots=True)
class DumpPage:
    """A page extracted from one XML dump record."""

    title: str
    namespace: int
    model: str
    text: str
    redirect: str | None


class RenderStats(TypedDict):
    infoboxes: int
    missing_templates: list[str]
    infobox_left: int
    missing_images: list[str]
    redlinks: int
    unrendered: int


@dataclass(slots=True)
class PageExportResources:
    """Resources shared by the rendering and HTML-rewrite stages."""

    title_to_filename: dict[str, str]
    redirects: dict[str, str]
    image_index: dict[str, Path]
    image_dir: Path | None
    asset_dir: Path
    copied_images: dict[Path, str]
    used_asset_names: dict[str, Path]
    missing_links: set[str]
    missing_images: set[str]


@dataclass(slots=True)
class PageConversion:
    """Markdown body and diagnostics produced while exporting one page."""

    markdown_body: str
    status: str
    note: str = ""
    conversion_errors: int = 0


class WikiContext(Protocol):
    """Methods used from wikitextprocessor's Wtp context."""

    def add_page(self, model: str, title: str, text: str) -> object: ...

    def analyze_templates(self) -> None: ...

    def start_page(self, title: str) -> None: ...

    def expand(self, text: str, timeout: int) -> str: ...


class WtpFactory(Protocol):
    """Callable constructor exposed by wikitextprocessor."""

    def __call__(self, *, quiet: bool, num_threads: int) -> WikiContext: ...


class PreprocessFunction(Protocol):
    """Fandom preprocessing function signature."""

    def __call__(
        self,
        text: str,
        context: WikiContext,
        infobox_defs: Mapping[str, object],
        page_title: str,
        stats: RenderStats,
    ) -> str: ...


class PostprocessFunction(Protocol):
    """Fandom post-expansion function signature."""

    def __call__(self, text: str, categories: list[str], stats: RenderStats) -> str: ...


class ExportState(TypedDict):
    title_to_filename: dict[str, str]
    context: WikiContext
    articles: list[DumpPage]
    material_usage_index: dict[str, list[tuple[str, str, list[tuple[str, str]]]]]
    redirects: dict[str, str]
    redirect_count: int
    preprocess: PreprocessFunction
    postprocess: PostprocessFunction
    infobox_defs: Mapping[str, object]
    pandoc: str
    image_index: dict[str, Path]
    image_dir: Path | None
    pages_dir: Path
    asset_dir: Path
    copied_images: dict[Path, str]
    used_asset_names: dict[str, Path]
    missing_links: set[str]
    missing_images: set[str]
    page_results: list[PageExportResult]
    categories_seen: set[str]
    expansion_errors: int
    conversion_errors: int


def normalize_title(title: str) -> str:
    """Normalize a page title for MediaWiki-style lookup."""
    value = urllib.parse.unquote(title)
    value = html.unescape(value).replace("_", " ")
    value = re.sub(r"\s+", " ", value).strip()
    return value[:1].upper() + value[1:] if value else value


def normalize_image_name(name: str) -> str:
    """Normalize a MediaWiki file target for matching dumped image files."""
    value = urllib.parse.unquote(name).strip().replace("\\", "/")
    value = value.rsplit("/", 1)[-1]
    value = re.sub(r"^(?:file|image)\s*:\s*", "", value, flags=re.IGNORECASE)
    return value.replace(" ", "_").casefold()


def _safe_destination(filename: str) -> str:
    """Percent-encode a link destination while retaining filename punctuation."""
    return urllib.parse.quote(filename, safe="!~-._")


def _portable_asset_name(name: str) -> str:
    """Make a dumped image basename portable to macOS and within byte limits."""
    normalized = unicodedata.normalize("NFC", Path(name).name).rstrip(" .")
    normalized = " - ".join(part.strip() for part in normalized.split(":"))
    suffix = Path(normalized).suffix
    stem = normalized[: -len(suffix)] if suffix else normalized
    stem = "".join(
        "_" if unicodedata.category(char) == "Cc" else char for char in stem
    ).rstrip(" .")
    if not stem:
        stem = "asset"

    suffix = "".join(
        "_" if unicodedata.category(char) == "Cc" else char for char in suffix
    )
    available_bytes = MAX_FILENAME_BYTES - len(suffix.encode("utf-8"))
    if available_bytes < 1:
        raise ValueError(f"Image extension is too long to store portably: {name!r}")

    if len(stem.encode("utf-8")) > available_bytes:
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:8]
        marker = f"-{digest}"
        budget = available_bytes - len(marker.encode("utf-8"))
        if budget < 1:
            raise ValueError(f"Image filename is too long to store portably: {name!r}")
        kept: list[str] = []
        used_bytes = 0
        for char in stem:
            char_bytes = len(char.encode("utf-8"))
            if used_bytes + char_bytes > budget:
                break
            kept.append(char)
            used_bytes += char_bytes
        stem = unicodedata.normalize("NFC", "".join(kept).rstrip(" .")) + marker

    return f"{stem}{suffix}"


def _is_external_scheme(scheme: str) -> bool:
    """Return whether a URI scheme should remain an external link."""
    is_interwiki = len(scheme) in {2, 3} or scheme in NAMED_INTERWIKI_SCHEMES
    return scheme in EXTERNAL_SCHEMES or (
        bool(scheme) and scheme not in LOCAL_WIKI_SCHEMES and is_interwiki
    )


def _follow_redirects(
    title: str, fragment: str, redirects: dict[str, str]
) -> tuple[str, str]:
    """Resolve a redirect chain and inherit its fragment when needed."""
    visited: set[str] = set()
    while title in redirects and title not in visited:
        visited.add(title)
        target, separator, redirect_fragment = redirects[title].partition("#")
        title = normalize_title(target)
        if not fragment and separator:
            fragment = redirect_fragment.replace(" ", "_")
    return title, fragment


def _resolve_page_destination(
    raw_target: str,
    fragment: str,
    current_title: str,
    title_to_filename: dict[str, str],
    redirects: dict[str, str],
) -> str | None:
    """Resolve a local page target and preserve its section fragment."""
    key = normalize_title(raw_target.lstrip(":"))
    key, fragment = _follow_redirects(key, fragment, redirects)
    filename = title_to_filename.get(key)
    if filename is None:
        return None

    destination = "./" + _safe_destination(filename)
    if key == normalize_title(current_title) and fragment:
        destination = "#" + _safe_destination(fragment)
    elif fragment:
        destination += "#" + _safe_destination(fragment)
    return destination


def resolve_internal_href(
    href: str,
    current_title: str,
    title_to_filename: dict[str, str],
    redirects: dict[str, str],
) -> str | None:
    """Resolve a Pandoc HTML link to a local Markdown file.

    External URLs and same-page anchors are returned unchanged. Unresolved
    wiki links return None so the caller can retain their label.
    """
    destination: str | None
    if not href or href.startswith("//"):
        destination = href
    elif href.startswith("#"):
        fragment = urllib.parse.unquote(href[1:]).replace(" ", "_")
        destination = "#" + _safe_destination(fragment)
    else:
        parsed = urllib.parse.urlsplit(href)
        scheme = parsed.scheme.casefold()
        if _is_external_scheme(scheme):
            destination = href
        else:
            raw_path = parsed.path if not scheme else href
            raw_target = urllib.parse.unquote(raw_path).strip()
            raw_target = raw_target.removeprefix("./").removeprefix("/")
            fragment = urllib.parse.unquote(parsed.fragment).replace(" ", "_")
            if not raw_target:
                destination = "#" + _safe_destination(fragment) if fragment else None
            else:
                destination = _resolve_page_destination(
                    raw_target, fragment, current_title, title_to_filename, redirects
                )
    return destination


def _read_mapping_entry(entry: object) -> tuple[str, str]:
    """Validate a raw mapping object and return its title and filename."""
    if not isinstance(entry, dict):
        raise ValueError("Every filename mapping entry must be an object.")
    mapping = cast("dict[str, object]", entry)
    title = mapping.get("title")
    filename = mapping.get("filename")
    if not isinstance(title, str) or not isinstance(filename, str):
        raise ValueError("Each mapping needs string 'title' and 'filename' fields.")
    return title, filename


def _validate_mapping_filename(
    title: str, filename: str, used_filenames: dict[str, str]
) -> str:
    """Validate one output filename and return its normalized collision key."""
    invalid_name = (
        not filename.endswith(".md")
        or "/" in filename
        or "\\" in filename
        or ":" in filename
        or filename in {".md", "..md"}
    )
    if invalid_name:
        raise ValueError(f"Unsafe Markdown filename for {title!r}: {filename!r}")
    if len(filename.encode("utf-8")) > MAX_FILENAME_BYTES:
        raise ValueError(f"Filename exceeds {MAX_FILENAME_BYTES} bytes: {filename!r}")
    if any(unicodedata.category(char) == "Cc" for char in filename):
        raise ValueError(f"Filename contains control characters: {filename!r}")

    collision_key = unicodedata.normalize("NFC", filename).casefold()
    if collision_key in used_filenames:
        other = used_filenames[collision_key]
        raise ValueError(f"Filename collision in mapping: {other!r}, {filename!r}")
    return collision_key


def load_filename_mapping(path: Path) -> dict[str, str]:
    """Load and validate the committed title-to-filename audit mapping."""
    raw_data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw_data, dict):
        raise ValueError("Filename mapping JSON must contain a top-level object.")
    data = cast("dict[str, object]", raw_data)
    entries = data.get("mappings")
    if not isinstance(entries, list):
        raise ValueError("Filename mapping JSON must contain a 'mappings' list.")

    title_to_filename: dict[str, str] = {}
    used_filenames: dict[str, str] = {}
    for entry in cast("list[object]", entries):
        title, filename = _read_mapping_entry(entry)
        normalized_title = normalize_title(title)
        if normalized_title in title_to_filename:
            raise ValueError(f"Duplicate normalized page title: {title!r}")

        collision_key = _validate_mapping_filename(title, filename, used_filenames)
        title_to_filename[normalized_title] = filename
        used_filenames[collision_key] = filename

    return title_to_filename


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


def _page_from_element(element: Element) -> DumpPage | None:
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


def _iter_dump_pages(path: Path) -> Iterator[DumpPage]:
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

        page = _page_from_element(element)
        root.clear()
        if page is not None:
            yield page


def _load_wiki_context(
    dump_path: Path, wtp_factory: WtpFactory
) -> tuple[WikiContext, list[DumpPage], dict[str, str], int]:
    """Load templates/modules and retain main-namespace article records."""
    context = wtp_factory(quiet=True, num_threads=1)
    articles: list[DumpPage] = []
    redirects: dict[str, str] = {}
    article_redirects = 0

    for page in _iter_dump_pages(dump_path):
        if page.redirect:
            context.add_page("redirect", page.title, page.redirect)
            redirects[normalize_title(page.title)] = page.redirect
            article_redirects += int(page.namespace == 0)
        else:
            context.add_page(page.model, page.title, page.text)
            if page.namespace == 0:
                articles.append(page)

    context.analyze_templates()
    return context, articles, redirects, article_redirects


def _pandoc_path() -> str:
    """Find Pandoc bundled by pypandoc-binary or installed on PATH."""
    try:
        pypandoc = importlib.import_module("pypandoc")
        get_pandoc_path = pypandoc.get_pandoc_path
        return str(get_pandoc_path())
    except ImportError, OSError, RuntimeError:
        binary = shutil.which("pandoc")
        if binary is None:
            raise RuntimeError(
                "Pandoc was not found. Install the project dependencies first."
            ) from None
        return binary


def _run_pandoc(binary: str, text: str, source_format: str, target_format: str) -> str:
    """Convert one document fragment with Pandoc."""
    command = [binary, "--from", source_format, "--to", target_format, "--wrap=none"]
    if target_format == "html":
        command.append("--mathml")
    result = subprocess.run(  # noqa: S603
        command, input=text, capture_output=True, text=True, timeout=120, check=False
    )
    if result.returncode:
        raise RuntimeError("Pandoc conversion failed: " + result.stderr.strip()[:1000])
    return result.stdout.strip()


def _strip_wikitext_comments(line: str, in_comment: bool) -> tuple[str, bool]:
    """Remove comments from one line while tracking multiline comment state."""
    visible = line
    if in_comment:
        _, separator, visible = visible.partition("-->")
        if not separator:
            return "", True
        in_comment = False

    while "<!--" in visible:
        before, _, remainder = visible.partition("<!--")
        _, separator, after = remainder.partition("-->")
        visible = before + after if separator else before
        if not separator:
            in_comment = True
            break
    return visible, in_comment


def _strip_wikitext_literal_content(
    line: str, literal_tag: str | None
) -> tuple[str, str | None]:
    """Remove nowiki-like content and track a tag spanning multiple lines."""
    visible = line
    if literal_tag is not None:
        closing = re.search(rf"</{re.escape(literal_tag)}\s*>", visible, re.I)
        if closing is None:
            return "", literal_tag
        visible = visible[closing.end() :]

    opening = re.search(r"<(nowiki|pre|source|syntaxhighlight)\b[^>]*>", visible, re.I)
    if opening is None:
        return visible, None

    tag = opening.group(1)
    closing = re.search(rf"</{re.escape(tag)}\s*>", visible[opening.end() :], re.I)
    if closing is None:
        return visible[: opening.start()], tag

    closing_end = opening.end() + closing.end()
    return visible[: opening.start()] + visible[closing_end:], None


def _is_wikitext_section_heading(value: str) -> bool:
    """Return whether a visible line is a MediaWiki section heading."""
    return re.fullmatch(r"={2,6}\s*.+?\s*={2,6}", value.strip()) is not None


def _append_mediawiki_table_closers(output: list[str], depth: int) -> int:
    """Append the requested number of table closers and return their count."""
    if not depth:
        return 0
    if output and not output[-1].endswith(("\n", "\r")):
        output.append("\n")
    output.extend("|}\n" for _ in range(depth))
    return depth


def _wikitext_table_depth_delta(value: str) -> int:
    """Return the nesting-depth change indicated by one visible table line."""
    marker = value.lstrip()
    if marker.startswith("{|"):
        return 1
    if marker.startswith("|}"):
        return -1
    return 0


def _repair_unclosed_mediawiki_tables(text: str) -> tuple[str, int]:
    """Close unterminated tables before the next section, or at end of page.

    Pandoc reports an unexpected end of input when an expanded template leaves
    a table open. Count only table markers at the beginning of a line, and
    ignore markers inside comments and literal wikitext tags. A missing table
    closer must not swallow later page sections.
    """
    depth = 0
    repaired_count = 0
    in_comment = False
    literal_tag: str | None = None
    output: list[str] = []

    for line in text.splitlines(keepends=True):
        visible, in_comment = _strip_wikitext_comments(line, in_comment)
        if not in_comment or visible:
            visible, literal_tag = _strip_wikitext_literal_content(visible, literal_tag)

        if depth and literal_tag is None and _is_wikitext_section_heading(visible):
            repaired_count += _append_mediawiki_table_closers(output, depth)
            depth = 0

        if not in_comment and literal_tag is None:
            depth = max(0, depth + _wikitext_table_depth_delta(visible))

        output.append(line)

    repaired_count += _append_mediawiki_table_closers(output, depth)
    if not repaired_count:
        return text, 0
    return "".join(output), repaired_count
def _build_image_index(image_dir: Path | None) -> dict[str, Path]:
    """Index primary images and their mismatch-folder fallbacks by basename."""
    if image_dir is None:
        return {}

    index: dict[str, Path] = {}
    image_dirs = (image_dir, image_dir.with_name(f"{image_dir.name}_mismatch"))
    for directory in image_dirs:
        if not directory.is_dir():
            continue
        for path in sorted(directory.rglob("*")):
            if path.is_file() and not path.name.endswith(".desc"):
                index.setdefault(normalize_image_name(path.name), path)
    return index


def _detect_image_suffix(source: Path) -> str:
    """Detect a useful image extension when the dumped filename has none."""
    header = b""
    try:
        with source.open("rb") as image_file:
            header = image_file.read(512)
    except OSError:
        header = b""

    suffix = ""
    if header.startswith(b"\xff\xd8\xff"):
        suffix = ".jpg"
    elif header.startswith(b"\x89PNG\r\n\x1a\n"):
        suffix = ".png"
    elif header.startswith((b"GIF87a", b"GIF89a")):
        suffix = ".gif"
    elif header.startswith(b"RIFF") and header[8:12] == b"WEBP":
        suffix = ".webp"
    elif header.startswith(b"BM"):
        suffix = ".bmp"
    elif header.startswith((b"II*\x00", b"MM\x00*")):
        suffix = ".tif"
    elif header.startswith(b"\x00\x00\x01\x00"):
        suffix = ".ico"
    elif b"<svg" in header.lower():
        suffix = ".svg"
    return suffix


def _copy_image(
    name: str,
    image_index: dict[str, Path],
    image_dir: Path | None,
    asset_dir: Path,
    copied: dict[Path, str],
    used_names: dict[str, Path],
) -> str | None:
    """Copy one referenced image and return its output basename."""
    source = image_index.get(normalize_image_name(name))
    if source is None or image_dir is None:
        return None
    if source in copied:
        return copied[source]

    portable_source_name = source.name
    if not Path(portable_source_name).suffix:
        portable_source_name += _detect_image_suffix(source)
    portable_source = Path(portable_source_name)
    output_name = _portable_asset_name(portable_source_name)
    key = unicodedata.normalize("NFC", output_name).casefold()
    previous = used_names.get(key)
    if previous is not None and previous != source:
        try:
            relative_source = str(source.relative_to(image_dir))
        except ValueError:
            relative_source = str(Path(source.parent.name, source.name))
        digest = hashlib.sha256(relative_source.encode()).hexdigest()[:8]
        output_name = _portable_asset_name(
            f"{portable_source.stem}-{digest}{portable_source.suffix}"
        )
        key = unicodedata.normalize("NFC", output_name).casefold()
        suffix = 2
        while key in used_names and used_names[key] != source:
            candidate = (
                f"{portable_source.stem}-{digest}-{suffix}{portable_source.suffix}"
            )
            output_name = _portable_asset_name(candidate)
            key = unicodedata.normalize("NFC", output_name).casefold()
            suffix += 1

    asset_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, asset_dir / output_name)
    copied[source] = output_name
    used_names[key] = source
    return output_name


def _restore_infobox_headers(table: lhtml.HtmlElement) -> None:
    """Turn infobox data cells into semantic table headers."""
    for row in table.iter("tr"):
        cells = [child for child in row if child.tag == "td"]
        if len(cells) == INFOBOX_VALUE_CELL_COUNT:
            cells[0].tag = "th"
        elif len(cells) == 1 and cells[0].get("colspan") == "2":
            paragraphs = [child for child in cells[0] if child.tag == "p"]
            if (
                len(paragraphs) == 1
                and len(paragraphs[0]) == 1
                and paragraphs[0][0].tag == "strong"
                and not (paragraphs[0].text or "").strip()
            ):
                cells[0].tag = "th"
                cells[0].set("class", "section")


def _restore_generated_tables(root: lhtml.HtmlElement) -> None:
    """Restore infobox and gallery semantics tagged by the Fandom preprocessor."""
    for table in root.iter("table"):
        caption = table.find("caption")
        if caption is None or not (caption.text or "").startswith("XSENT:"):
            continue

        table_class, separator, caption_text = (caption.text or "")[6:].partition(":")
        if not separator:
            continue
        table.set("class", table_class)
        caption.text = caption_text
        if not (caption.text or "").strip() and len(caption) == 0:
            table.remove(caption)
        if table_class == "infobox":
            _restore_infobox_headers(table)


def _restore_thumbnail_captions(root: lhtml.HtmlElement) -> None:
    """Turn XTHUMB image alt markers into visible figure or span captions."""
    for image in list(root.iter("img")):
        label = image.get("alt") or image.get("title") or ""
        if not label.startswith("XTHUMB"):
            continue

        caption = label[len("XTHUMB") :].strip()
        image.attrib.pop("alt", None)
        image.attrib.pop("title", None)
        if caption:
            image.set("alt", caption)

        parent = image.getparent()
        if parent is None:
            continue
        if parent.tag == "figure":
            parent.set("class", "thumb")
            old_caption = parent.find("figcaption")
            if old_caption is not None:
                parent.remove(old_caption)
            if caption:
                figcaption = cast("lhtml.HtmlElement", etree.Element("figcaption"))
                figcaption.text = caption
                parent.append(figcaption)
            continue

        wrapper = cast("lhtml.HtmlElement", etree.Element("span"))
        wrapper.set("class", "thumb")
        old_tail = image.tail
        image.addprevious(wrapper)
        wrapper.append(image)
        if caption:
            caption_node = cast("lhtml.HtmlElement", etree.Element("span"))
            caption_node.set("class", "thumbcaption")
            caption_node.text = caption
            wrapper.append(caption_node)
        image.tail = None
        wrapper.tail = old_tail


def _add_mediawiki_heading_ids(root: lhtml.HtmlElement) -> None:
    """Assign stable MediaWiki-style heading IDs and insert a compact TOC."""
    used: set[str] = set()
    headings: list[tuple[int, str, str]] = []
    for heading in root.iter("h1", "h2", "h3", "h4", "h5", "h6"):
        label = heading.text_content().strip()
        base = label.replace(" ", "_") or "section"
        identifier = base
        suffix = 1
        while identifier in used:
            suffix += 1
            identifier = f"{base}_{suffix}"
        used.add(identifier)
        heading.set("id", identifier)
        tag = heading.tag
        if not isinstance(tag, str):
            continue
        headings.append((int(tag[1]), label, identifier))

    if len(headings) < MINIMUM_HEADINGS_FOR_TOC:
        return

    nav = cast("lhtml.HtmlElement", etree.Element("nav"))
    nav.set("class", "toc")
    toc_heading = etree.SubElement(nav, "strong")
    toc_heading.text = "Contents"
    items = etree.SubElement(nav, "ul")
    for level, label, identifier in headings:
        item = etree.SubElement(items, "li")
        item.set("class", f"lvl{level}")
        link = etree.SubElement(item, "a")
        link.set("href", "#" + _safe_destination(identifier))
        link.text = label
    root.insert(0, nav)


def _add_footnote_heading(root: lhtml.HtmlElement) -> None:
    """Add a Notes heading when Pandoc emits a footnotes section without one."""
    for section in root.iter("section", "aside"):
        if "footnotes" not in (section.get("class") or ""):
            continue
        previous = section.getprevious()
        if previous is None or previous.tag not in {"h1", "h2", "h3", "h4"}:
            heading = cast("lhtml.HtmlElement", etree.Element("h2"))
            heading.text = "Notes"
            section.insert(0, heading)


def _rewrite_image_nodes(
    root: lhtml.HtmlElement,
    image_index: dict[str, Path],
    image_dir: Path | None,
    asset_dir: Path,
    copied_images: dict[Path, str],
    used_asset_names: dict[str, Path],
    missing_images: set[str],
) -> None:
    """Rewrite local image tags and copy their source assets."""
    for image in list(root.iter("img")):
        source_name = image.get("src", "")
        image.set("loading", "lazy")
        image_title = image.get("title")
        if image_title and normalize_image_name(image_title) == normalize_image_name(
            source_name
        ):
            image.attrib.pop("title")

        scheme = urllib.parse.urlsplit(source_name).scheme
        if scheme in {"http", "https", "data"}:
            continue

        output_name = _copy_image(
            source_name,
            image_index,
            image_dir,
            asset_dir,
            copied_images,
            used_asset_names,
        )
        if output_name is not None:
            image.set("src", "../assets/" + _safe_destination(output_name))
            continue

        missing_images.add(normalize_image_name(source_name))
        label = image.get("alt") or ""
        caption = label[len("XTHUMB") :].strip() if label.startswith("XTHUMB") else ""
        placeholder = cast("lhtml.HtmlElement", etree.Element("span"))
        placeholder.text = f"[Missing image: {source_name}]"
        parent = image.getparent()
        if parent is not None:
            placeholder.tail = (" " + caption if caption else "") + (image.tail or "")
            image.tail = None
            parent.replace(image, placeholder)

    _restore_thumbnail_captions(root)


def _rewrite_anchor_nodes(
    root: lhtml.HtmlElement,
    current_title: str,
    title_to_filename: dict[str, str],
    redirects: dict[str, str],
    image_index: dict[str, Path],
    image_dir: Path | None,
    asset_dir: Path,
    copied_images: dict[Path, str],
    used_asset_names: dict[str, Path],
    missing_links: set[str],
    missing_images: set[str],
) -> None:
    """Rewrite article and file anchors to local Markdown destinations."""
    for link in list(root.iter("a")):
        href = link.get("href")
        if not href:
            continue

        parsed_href = urllib.parse.urlsplit(href)
        scheme = parsed_href.scheme.casefold()
        label = link.text_content().strip()
        if LANGUAGE_INTERWIKI_SCHEME_RE.fullmatch(
            scheme
        ) and label.casefold().startswith(f"{scheme}:"):
            link.drop_tree()
            continue

        raw = urllib.parse.unquote(href).lstrip(":")
        prefix, separator, _ = raw.partition(":")
        if separator and prefix.strip().casefold() in {"file", "image", "media"}:
            output_name = _copy_image(
                raw, image_index, image_dir, asset_dir, copied_images, used_asset_names
            )
            if output_name is None:
                missing_images.add(normalize_image_name(raw))
                link.drop_tag()
            else:
                link.set("href", "../assets/" + _safe_destination(output_name))
            continue

        destination = resolve_internal_href(
            href, current_title, title_to_filename, redirects
        )
        if destination is None:
            if not href.startswith("#"):
                missing_links.add(href)
            link.drop_tag()
        elif destination != href:
            link.set("href", destination)


def _rewrite_html_links(
    fragment: str,
    current_title: str,
    title_to_filename: dict[str, str],
    redirects: dict[str, str],
    image_index: dict[str, Path],
    image_dir: Path | None,
    asset_dir: Path,
    copied_images: dict[Path, str],
    used_asset_names: dict[str, Path],
    missing_links: set[str],
    missing_images: set[str],
) -> str:
    """Rewrite HTML links/images to local Markdown workspace paths."""
    root = lhtml.fragment_fromstring(fragment or "", create_parent="div")
    _restore_generated_tables(root)
    _rewrite_image_nodes(
        root,
        image_index,
        image_dir,
        asset_dir,
        copied_images,
        used_asset_names,
        missing_images,
    )
    _rewrite_anchor_nodes(
        root,
        current_title,
        title_to_filename,
        redirects,
        image_index,
        image_dir,
        asset_dir,
        copied_images,
        used_asset_names,
        missing_links,
        missing_images,
    )
    _add_mediawiki_heading_ids(root)
    _add_footnote_heading(root)
    return etree.tostring(root, encoding="unicode", method="html")


def _page_markdown(
    title: str, categories: list[str], body: str, source_url: str | None
) -> str:
    """Compose plain Markdown with a visible title and readable metadata."""
    lines = [f"# {_markdown_label(title)}", ""]
    metadata: list[str] = []
    if categories:
        category_text = ", ".join(_markdown_text(category) for category in categories)
        metadata.append(f"**Categories:** {category_text}")
    if source_url:
        article_url = (
            source_url.rstrip("/")
            + "/wiki/"
            + urllib.parse.quote(title.replace(" ", "_"), safe="()'!~-._")
        )
        metadata.append(f"**Source:** [Fandom]({_markdown_destination(article_url)})")
    if metadata:
        lines.extend([" · ".join(metadata), ""])
    cleaned_body = _strip_navbox_lua_errors(body)
    cleaned_body = _strip_wikitext_table_row_artifacts(cleaned_body)
    cleaned_body = _strip_empty_gallery_sections(cleaned_body)
    if cleaned_body:
        lines.extend([cleaned_body, ""])
    document = "\n".join(lines).rstrip() + "\n"
    document = _normalize_markdown_emphasis(document)
    return _normalize_markdown_headings(document)


MARKDOWN_SPECIAL_CHARS = frozenset("\\`*_{}[]()#+-.!|>")


def _markdown_label(title: str) -> str:
    """Escape Markdown punctuation in a wiki title used as text or a label."""
    return "".join(
        "\\" + character if character in MARKDOWN_SPECIAL_CHARS else character
        for character in title
    )


def _strip_navbox_lua_errors(value: str) -> str:
    """Remove the visible Lua failure line and its dangling interwiki link."""
    cleaned = NAVBOX_ERROR_LINE_RE.sub("", value)
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()


def _is_leaked_wikitext_table_artifact(line: str) -> bool:
    """Return whether a line is a leaked MediaWiki table or file marker."""
    stripped = line.strip()
    return bool(
        re.fullmatch(
            r"""\\?\|}\s*\{\\?\|\s*style\s*=\s*["']margin:auto["']""",
            stripped,
            re.IGNORECASE,
        )
        or re.fullmatch(
            r"\s*(?:\\?\[){2}\s*file\s*:.*?(?:\\?\]){2}\s*", line, re.IGNORECASE
        )
        or re.fullmatch(r"\s*\|\s*\|\s*[-\u2013\u2014]\s*\|\s*", stripped)
    )


def _strip_missing_template_labels(line: str) -> str:
    """Remove leaked template labels while retaining a following Markdown link."""
    link_templates = r"(?:pic link(?: plain)?|iconlink(?: plain)?)"
    template_link = (
        rf"\*\*Template:{link_templates}\*\*"
        r"(\[[^\]\n]+\]\([^)]+\))\*\*"
    )
    cleaned = re.sub(template_link, r"\1", line, flags=re.IGNORECASE)
    return re.sub(
        r"\*{0,2}Template:(?:pic link(?: plain)?|iconlink(?: plain)?|"
        r"quote2|stub|update history|reflist)\*{0,2}",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )


def _strip_empty_markup_rows(line: str) -> str:
    """Remove empty Markdown table rows and list items after cleanup."""
    stripped = line.strip()
    if re.fullmatch(r"\s*\|\s*(?:\|\s*)+\s*", stripped):
        return ""
    if re.fullmatch(r"\s*(?:[-*+]|[0-9]+[.)])\s*", stripped):
        return ""
    return line


def _quote_table_opening(line: str) -> re.Match[str] | None:
    """Find the opening wrapper of a leaked Fandom quote2 table."""
    return re.search(
        r"\{\|\s*style\s*=\s*['\"]margin:auto['\"]\s*"
        r"\|\s*style\s*=\s*['\"]vertical-align:top;[^'\"]*['\"]\s*"
        r"\|\s*[\u201c\u2018\"]\s*\|\s*(.*)$",
        line,
        re.IGNORECASE,
    )


def _quote_table_closing(line: str) -> re.Match[str] | None:
    """Find the closing wrapper of a leaked Fandom quote2 table."""
    return re.search(
        r"\|\s*style\s*=\s*['\"]vertical-align:bottom;[^'\"]*['\"]\s*"
        r"\|\s*[\u201d\u2019\"]\s*\|\}(.*)$",
        line,
        re.IGNORECASE,
    )


def _append_wikitext_quote(
    output: list[str], quote_lines: list[str], trailing_text: str
) -> None:
    """Append a leaked quote2 table as a Markdown blockquote."""
    content = [line.strip() for line in quote_lines]
    while content and not content[-1]:
        content.pop()

    attribution = content.pop() if len(content) > 1 else ""
    output.extend("> " + line if line else ">" for line in content)
    if attribution:
        output.extend([">", "> — " + attribution])
    if trailing_text.strip():
        if output and output[-1].strip():
            output.append("")
        output.append(trailing_text.strip())


def _advance_quote_code_fence(
    line: str, fence: tuple[str, int] | None
) -> tuple[bool, tuple[str, int] | None]:
    """Track code fences while quote-table normalization scans Markdown."""
    match = FENCED_CODE_RE.match(line)
    if match is None:
        return fence is not None, fence

    marker = match.group(1)
    if fence is None:
        return True, (marker[0], len(marker))
    if marker[0] == fence[0] and len(marker) >= fence[1]:
        return True, None
    return True, fence


def _begin_wikitext_quote(line: str, output: list[str]) -> list[str] | None:
    """Start converting a quote2 wrapper, returning pending quote lines."""
    opening = _quote_table_opening(line)
    if opening is None:
        return None

    prefix = re.sub(
        r"\*{1,2}\s*Template:quote2\s*\*{1,2}",
        "",
        line[: opening.start()],
        flags=re.IGNORECASE,
    )
    if prefix.strip():
        output.append(prefix.strip())

    closing = _quote_table_closing(opening.group(1))
    if closing is None:
        return [opening.group(1)]

    _append_wikitext_quote(
        output, [opening.group(1)[: closing.start()]], closing.group(1)
    )
    return []


def _continue_wikitext_quote(
    line: str, quote_lines: list[str], output: list[str]
) -> bool:
    """Append a quote line or finish the wrapper when its closing row appears."""
    closing = _quote_table_closing(line)
    if closing is None:
        quote_lines.append(line)
        return False

    before_closing = line[: closing.start()].strip()
    if before_closing:
        quote_lines.append(before_closing)
    _append_wikitext_quote(output, quote_lines, closing.group(1))
    return True


def _normalize_leaked_wikitext_quotes(value: str) -> str:
    """Convert leaked quote2 table wrappers to ordinary Markdown blockquotes."""
    output: list[str] = []
    quote_lines: list[str] = []
    fence: tuple[str, int] | None = None

    for line in value.splitlines():
        in_fence, fence = _advance_quote_code_fence(line, fence)
        if in_fence:
            output.append(line)
            continue

        if quote_lines:
            if _continue_wikitext_quote(line, quote_lines, output):
                quote_lines = []
            continue

        started_quote = _begin_wikitext_quote(line, output)
        if started_quote is None:
            output.append(line)
        else:
            quote_lines = started_quote

    if quote_lines:
        return value
    normalized = "\n".join(output)
    return normalized + ("\n" if value.endswith("\n") else "")


def _clean_wikitext_artifact_line(line: str) -> str:
    """Remove unsupported MediaWiki syntax from one ordinary text line."""
    cleaned = re.sub(r"\\+\|-[ \t]*(?=\r?\n?$)", "", line)
    cleaned = re.sub(r"\\?\|\}", "", cleaned)
    cleaned = re.sub(
        r"\{\\?\|\s*(?:class\s*=\s*['\"][^'\"]*['\"]\s*)?"
        r"style\s*=\s*['\"](?:margin:auto|background-color:transparent)[^'\"]*['\"]",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = _strip_missing_template_labels(cleaned)
    cleaned = re.sub(r"(?m)^(\s*[-*+]\s*),[ \t]*", r"\1", cleaned)
    heading = re.fullmatch(r"\s*(={2,6})\s*(.*?)\s*\1\s*", cleaned.strip())
    if heading is not None:
        line_ending = cleaned[len(cleaned.rstrip("\r\n")) :]
        return f"{'#' * len(heading.group(1))} {heading.group(2).strip()}{line_ending}"
    return _strip_empty_markup_rows(cleaned)


def _strip_wikitext_table_row_artifacts(value: str) -> str:
    """Remove leaked MediaWiki table, file, template, quote, and list artifacts."""
    value = _normalize_leaked_wikitext_quotes(value)
    output: list[str] = []
    fence: tuple[str, int] | None = None
    for line in value.splitlines(keepends=True):
        match = FENCED_CODE_RE.match(line)
        if match is not None:
            marker = match.group(1)
            if fence is None:
                fence = (marker[0], len(marker))
            elif marker[0] == fence[0] and len(marker) >= fence[1]:
                fence = None
            output.append(line)
            continue
        if fence is None and _is_leaked_wikitext_table_artifact(line):
            continue
        cleaned_line = _clean_wikitext_artifact_line(line) if fence is None else line
        if cleaned_line:
            output.append(cleaned_line)
    return "".join(output)


def _strip_empty_gallery_sections(value: str) -> str:
    """Remove Gallery headings only when their complete section has no content."""
    lines = value.splitlines()
    empty_gallery_lines: set[int] = set()
    for index, line in enumerate(lines):
        heading = re.match(r"^(#{1,6})\s+Gallery\s*#*\s*$", line, re.IGNORECASE)
        if heading is None:
            continue

        gallery_level = len(heading.group(1))
        section_has_content = False
        for following in lines[index + 1 :]:
            next_heading = re.match(r"^(#{1,6})\s+", following)
            if next_heading is not None and len(next_heading.group(1)) <= gallery_level:
                break
            if following.strip() and next_heading is None:
                section_has_content = True
                break

        if not section_has_content:
            empty_gallery_lines.add(index)

    if not empty_gallery_lines:
        return value
    cleaned = [
        line
        for index, line in enumerate(lines)
        if index not in empty_gallery_lines
        and not re.match(
            r"^\s*[-*+]\s+\[Gallery\]\(#gallery\)\s*$", line, re.IGNORECASE
        )
    ]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(cleaned)).strip()


def _uppercase_heading_initial(line: str) -> str:
    """Uppercase the first visible letter of one Markdown ATX heading."""
    match = re.match(
        r"^(?P<prefix> {0,3}#{1,6}[ \t]+)(?P<content>.*?)(?P<ending>\r?\n?)$", line
    )
    if match is None:
        return line

    content = match.group("content")
    index = 0
    while index < len(content):
        if content[index] == "<":
            tag_end = content.find(">", index + 1)
            if tag_end >= 0:
                index = tag_end + 1
                continue
        if content[index].isalpha():
            content = content[:index] + content[index].upper() + content[index + 1 :]
            break
        index += 1

    return match.group("prefix") + content + match.group("ending")


def _normalize_markdown_headings(value: str) -> str:
    """Ensure every Markdown heading starts with an uppercase letter."""
    output: list[str] = []
    fence: tuple[str, int] | None = None
    for line in value.splitlines(keepends=True):
        match = FENCED_CODE_RE.match(line)
        if match is not None:
            marker = match.group(1)
            if fence is None:
                fence = (marker[0], len(marker))
            elif marker[0] == fence[0] and len(marker) >= fence[1]:
                fence = None
            output.append(line)
            continue
        output.append(line if fence is not None else _uppercase_heading_initial(line))
    return "".join(output)


def _normalize_markdown_emphasis(value: str) -> str:
    """Use underscore-delimited italics without changing code spans or fences."""
    output: list[str] = []
    fence: tuple[str, int] | None = None
    for line in value.splitlines(keepends=True):
        match = FENCED_CODE_RE.match(line)
        if match is not None:
            marker = match.group(1)
            if fence is None:
                fence = (marker[0], len(marker))
            elif marker[0] == fence[0] and len(marker) >= fence[1]:
                fence = None
            output.append(line)
            continue
        if fence is not None:
            output.append(line)
            continue

        parts = INLINE_CODE_SPAN_RE.split(line)
        for index, part in enumerate(parts):
            if index % 2:
                output.append(part)
            else:
                separated_strong = re.sub(r"(?<!\\)\*{4}(?!\*)", "** **", part)
                strong = STRONG_UNDERSCORE_RE.sub(
                    lambda item: f"**{item.group('content')}**", separated_strong
                )
                output.append(
                    ITALIC_ASTERISK_RE.sub(
                        lambda item: f"_{item.group('content')}_", strong
                    )
                )
    return "".join(output)


def _build_material_usage_index(
    articles: list[DumpPage],
) -> dict[str, list[tuple[str, str, list[tuple[str, str]]]]]:
    """Index recipe pages by each material named in their recipe infoboxes."""
    usage_index: dict[str, list[tuple[str, str, list[tuple[str, str]]]]] = {}
    for page in articles:
        for match in RECIPE_TEMPLATE_RE.finditer(page.text):
            parameters = {
                parameter.group(1).casefold(): parameter.group(2).strip()
                for parameter in re.finditer(
                    r"\|\s*([a-z][a-z0-9_]*)\s*=\s*([^|\n}]*)",
                    match.group(1),
                    re.IGNORECASE,
                )
            }
            recipe_name = parameters.get("name") or page.title
            materials: list[tuple[str, str]] = []
            for slot in range(1, 7):
                material = parameters.get(f"mat{slot}", "").strip()
                quantity = parameters.get(f"mat{slot}qty", "").strip()
                if material and quantity:
                    materials.append((quantity, material))
            if not materials:
                continue

            row = (recipe_name, "None", materials)
            for _, material in materials:
                key = normalize_title(material).casefold()
                usage_index.setdefault(key, []).append(row)

    for rows in usage_index.values():
        rows.sort(key=lambda row: row[0].casefold())
    return usage_index


def _render_material_usage_table(
    rows: list[tuple[str, str, list[tuple[str, str]]]],
) -> str:
    """Build the three-column table generated by Fandom's Uses material list."""
    rendered_rows: list[str] = []
    for recipe_name, skills, materials in rows:
        recipe_link = html.escape(recipe_name, quote=True)
        material_items = "".join(
            "<li>"
            + html.escape(quantity)
            + ' \u00d7 <a href="'
            + html.escape(material, quote=True)
            + '">'
            + html.escape(material)
            + "</a></li>"
            for quantity, material in materials
        )
        rendered_rows.append(
            "<tr><td>"
            '<a href="'
            + recipe_link
            + '">'
            + html.escape(recipe_name)
            + "</a></td><td>"
            + html.escape(skills)
            + "</td><td><ul>"
            + material_items
            + "</ul></td></tr>"
        )
    if not rendered_rows:
        return ""
    return (
        '<table class="wikitable uses-material-list">'
        "<thead><tr><th>Item</th><th>Skills</th><th>Materials</th></tr></thead>"
        "<tbody>" + "".join(rendered_rows) + "</tbody></table>"
    )


def _defer_uses_material_list_templates(
    text: str,
    page_title: str,
    usage_index: dict[str, list[tuple[str, str, list[tuple[str, str]]]]],
) -> tuple[str, dict[str, str]]:
    """Replace material-list calls with safe tokens until wikitext expansion ends."""
    deferred_tables: dict[str, str] = {}

    def defer(match: re.Match[str]) -> str:
        target = match.group(1) or page_title
        rows = usage_index.get(normalize_title(target).casefold(), [])
        table = _render_material_usage_table(rows)
        if not table:
            return match.group(0)

        token = f"TLDGENERATEDMATERIALLIST{len(deferred_tables)}TOKEN"
        while token in text or token in deferred_tables:
            token += "X"
        deferred_tables[token] = table
        return token

    return USES_MATERIAL_LIST_RE.sub(defer, text), deferred_tables


def _restore_deferred_material_tables_in_html(
    html_fragment: str, deferred_tables: dict[str, str]
) -> str:
    """Restore generated tables after Pandoc has converted their safe text tokens."""
    for token, table in deferred_tables.items():
        paragraph = re.compile(
            rf"<p(?:\s[^>]*)?>\s*{re.escape(token)}\s*</p>", re.IGNORECASE
        )

        def replace_table(_match: re.Match[str], replacement: str = table) -> str:
            return replacement

        html_fragment, replacements = paragraph.subn(replace_table, html_fragment)
        if not replacements:
            html_fragment = html_fragment.replace(token, table)
    return html_fragment


def _expand_page(
    page: DumpPage,
    context: WikiContext,
    preprocess: PreprocessFunction,
    infobox_defs: Mapping[str, object],
    stats: RenderStats,
    material_usage_index: dict[str, list[tuple[str, str, list[tuple[str, str]]]]]
    | None = None,
) -> tuple[str, str, int, dict[str, str]]:
    """Expand one page and retain generated tables for post-Pandoc insertion."""
    expanded = page.text
    if page.model != "wikitext":
        return expanded, "", 0, {}

    deferred_material_tables: dict[str, str] = {}
    try:
        context.start_page(page.title)
        expanded = preprocess(page.text, context, infobox_defs, page.title, stats)
        if material_usage_index is not None:
            expanded, deferred_material_tables = _defer_uses_material_list_templates(
                expanded, page.title, material_usage_index
            )
    except Exception as exc:
        return expanded, str(exc)[:300], 1, deferred_material_tables

    try:
        with contextlib.redirect_stdout(io.StringIO()):
            expanded = context.expand(expanded, timeout=30)
    except Exception as exc:
        return expanded, str(exc)[:300], 1, deferred_material_tables

    return expanded, "", 0, deferred_material_tables


def _render_mediawiki_html(
    page: DumpPage,
    expanded: str,
    pandoc: str,
    deferred_material_tables: dict[str, str] | None = None,
) -> tuple[str, str, str]:
    """Render expanded wikitext and restore generated tables after Pandoc."""
    conversion_stage = "Pandoc MediaWiki-to-HTML"
    try:
        html_fragment = _run_pandoc(pandoc, expanded, "mediawiki", "html")
    except Exception as initial_error:
        repaired_text, repaired_tables = _repair_unclosed_mediawiki_tables(expanded)
        if (
            "unexpected end of input" in str(initial_error).casefold()
            and repaired_tables
        ):
            try:
                html_fragment = _run_pandoc(pandoc, repaired_text, "mediawiki", "html")
            except Exception as repair_error:
                conversion_note = (
                    f"{conversion_stage} still failed after closing "
                    f"{repaired_tables} unterminated table(s): {repair_error}"
                )
            else:
                html_fragment = _restore_deferred_material_tables_in_html(
                    html_fragment, deferred_material_tables or {}
                )
                conversion_note = (
                    "Recovered from Pandoc's unexpected end of input by "
                    f"closing {repaired_tables} unterminated MediaWiki table(s)."
                )
                return html_fragment, "repaired", conversion_note
        else:
            conversion_note = (
                f"{conversion_stage} rejected expanded wikitext: {initial_error}"
            )

        html_fragment = _run_pandoc(pandoc, page.text, "mediawiki", "html")
        conversion_note = f"{conversion_note} Used the unexpanded source as a fallback."
        return html_fragment, "raw_fallback", conversion_note

    html_fragment = _restore_deferred_material_tables_in_html(
        html_fragment, deferred_material_tables or {}
    )
    return html_fragment, "ok", ""


def _rewrite_page_html(
    html_fragment: str, page: DumpPage, resources: PageExportResources, note: str
) -> tuple[str, str, bool]:
    """Rewrite page links and images, retaining HTML if rewriting fails."""
    try:
        rewritten_html = _rewrite_html_links(
            html_fragment,
            page.title,
            resources.title_to_filename,
            resources.redirects,
            resources.image_index,
            resources.image_dir,
            resources.asset_dir,
            resources.copied_images,
            resources.used_asset_names,
            resources.missing_links,
            resources.missing_images,
        )
    except Exception as rewrite_error:
        note = (
            f"{note} HTML link/image rewriting failed; kept the "
            f"Pandoc HTML fragment: {rewrite_error}"
        ).strip()
        return html_fragment, note, True
    return rewritten_html, note, False


HTML_BLOCK_TAGS = frozenset(
    {
        "address",
        "article",
        "aside",
        "blockquote",
        "body",
        "canvas",
        "center",
        "dd",
        "details",
        "dialog",
        "div",
        "dl",
        "dt",
        "fieldset",
        "figcaption",
        "figure",
        "footer",
        "form",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "hgroup",
        "hr",
        "main",
        "menu",
        "nav",
        "ol",
        "p",
        "pre",
        "search",
        "section",
        "summary",
        "table",
        "ul",
    }
)
HTML_TAG_RE = re.compile(
    r"""
    </?
    (?![A-Za-z][A-Za-z0-9+.-]*://)
    [A-Za-z][A-Za-z0-9._:-]*
    (?=\s|/?>)
    [^>]*>
    """,
    re.VERBOSE,
)
HTML_COMMENT_RE = re.compile(r"<!--.*?(?:-->|$)", re.DOTALL)
HTML_DECLARATION_RE = re.compile(r"<![A-Za-z][^>]*>")
HTML_HIDDEN_TAGS = frozenset(
    {"base", "head", "link", "meta", "script", "style", "template"}
)


def _contains_html_markup(value: str) -> bool:
    """Return whether text contains HTML elements, comments, or declarations."""
    return any(
        pattern.search(value)
        for pattern in (HTML_TAG_RE, HTML_COMMENT_RE, HTML_DECLARATION_RE)
    )


def _strip_html_markup(value: str) -> str:
    """Remove residual markup when even the HTML parser cannot recover a fragment."""
    without_comments = HTML_COMMENT_RE.sub("", value)
    without_declarations = HTML_DECLARATION_RE.sub("", without_comments)
    return HTML_TAG_RE.sub("", without_declarations)


def _html_tag(node: lhtml.HtmlElement) -> str:
    """Return an HTML element's normalized tag name."""
    return node.tag.casefold() if isinstance(node.tag, str) else ""


def _markdown_text(value: str) -> str:
    """Escape plain text while translating MediaWiki double-underscore bold."""
    pieces = STRONG_UNDERSCORE_RE.split(value)
    rendered: list[str] = []
    for index, piece in enumerate(pieces):
        escaped = html.escape(re.sub(r"\s+", " ", piece), quote=False)
        for character in ("\\", "*", "_", "[", "]"):
            escaped = escaped.replace(character, "\\" + character)
        if index % 2:
            rendered.append(f"**{escaped}**")
        else:
            rendered.append(escaped)
    return "".join(rendered)


def _markdown_destination(value: str) -> str:
    """Encode characters that would terminate a Markdown link destination."""
    return value.replace(" ", "%20").replace("(", "%28").replace(")", "%29")


def _markdown_code_span(value: str) -> str:
    """Format inline code with a delimiter longer than any run in its content."""
    content = value.strip()
    if not content:
        return ""
    backtick = chr(96)
    longest = max(
        (len(match) for match in re.findall(re.escape(backtick) + r"+", content)),
        default=0,
    )
    fence = backtick * (longest + 1)
    padding = " " if content.startswith(backtick) or content.endswith(backtick) else ""
    return f"{fence}{padding}{content}{padding}{fence}"


def _markdown_image(node: lhtml.HtmlElement) -> str:
    """Convert an HTML image to a Markdown image reference."""
    source = node.get("src", "")
    label = node.get("alt") or node.get("title") or ""
    if label and source and normalize_image_name(label) == normalize_image_name(
        urllib.parse.unquote(source)
    ):
        label = ""
    if not source:
        return _markdown_text(label)
    return f"![{_markdown_text(label)}]({_markdown_destination(source)})"

def _inline_markdown(node: lhtml.HtmlElement) -> str:
    """Render an element's mixed inline content without retaining HTML tags."""
    pieces = [_markdown_text(node.text or "")]
    previous_child: lhtml.HtmlElement | None = None
    for child in node:
        if isinstance(child.tag, str):
            current_tag = _html_tag(child)
            previous_tag = (
                _html_tag(previous_child) if previous_child is not None else ""
            )
            if (
                previous_tag in {"strong", "b"}
                and current_tag in {"strong", "b"}
                and previous_child is not None
                and not (previous_child.tail or "").endswith((" ", "\t", "\n"))
                and not (child.text or "").startswith((" ", "\t", "\n"))
                and _inline_markdown(child)
            ):
                pieces.append(" ")
            pieces.append(_inline_markdown_element(child))
            previous_child = child
        pieces.append(_markdown_text(child.tail or ""))
    return "".join(pieces)


def _markdown_emphasis_blocks(content: str, marker: str) -> str:
    """Apply Markdown emphasis separately to paragraphs separated by blank lines."""
    chunks = re.split(r"(\n[ \t]*\n)", content)
    return "".join(
        f"{marker}{chunk.strip()}{marker}" if chunk.strip() else chunk
        for chunk in chunks
    )


def _inline_span_markdown(node: lhtml.HtmlElement, content: str) -> str | None:
    """Translate visual span styling into nearby Markdown emphasis."""
    style = (node.get("style") or "").casefold().replace(" ", "")
    classes = (node.get("class") or "").split()
    if "font-style:italic" in style or "thumbcaption" in classes:
        return f"_{content}_" if content else ""
    if "font-weight:bold" in style:
        return _markdown_emphasis_blocks(content, "**") if content else ""
    if "text-decoration:line-through" in style:
        return f"~~{content}~~" if content else ""
    if "text-decoration:underline" in style:
        return f"_{content}_" if content else ""
    return None


def _styled_markdown_content(node: lhtml.HtmlElement, content: str) -> str:
    """Apply a Markdown approximation for visual inline styles."""
    styled = _inline_span_markdown(node, content)
    return content if styled is None else styled


def _inline_markdown_emphasis(tag: str, content: str) -> str | None:
    """Convert inline emphasis and strike-through tags to Markdown."""
    if tag in {"strong", "b", "mark", "big"}:
        return _markdown_emphasis_blocks(content, "**") if content else ""
    if tag in {"em", "i", "cite", "var", "u", "small", "dfn"}:
        return f"_{content}_" if content else ""
    if tag in {"del", "s", "strike"}:
        return f"~~{content}~~" if content else ""
    return None


def _inline_markdown_special_semantics(
    node: lhtml.HtmlElement, tag: str, content: str
) -> str | None:
    """Translate semantic inline elements without retaining their HTML tags."""
    if tag == "span":
        return content
    if tag == "abbr":
        title = node.get("title", "").strip()
        if title and title.casefold() != content.casefold():
            return f"{content} ({_markdown_text(title)})"
        return content
    if tag == "q":
        return f"“{content}”" if content else ""
    if tag == "wbr":
        return ""
    is_math = tag == "math" or "math" in (node.get("class") or "").split()
    return "$" + content + "$" if is_math and content else None


def _inline_markdown_semantics(
    node: lhtml.HtmlElement, tag: str, content: str
) -> str | None:
    """Convert code, math, and baseline formatting tags to Markdown."""
    if tag in {"code", "kbd", "samp"}:
        return _markdown_code_span(node.text_content())
    if tag == "sup":
        return _unicode_superscript(content)
    if tag == "sub":
        return _unicode_subscript(content)
    styled = _inline_span_markdown(node, content)
    if styled is not None:
        return styled
    return _inline_markdown_special_semantics(node, tag, content)


def _inline_markdown_formatting(
    node: lhtml.HtmlElement, tag: str, content: str
) -> str | None:
    """Convert inline emphasis, code, math, and baseline formatting."""
    emphasis = _inline_markdown_emphasis(tag, content)
    if emphasis is not None:
        return emphasis
    return _inline_markdown_semantics(node, tag, content)


def _inline_markdown_input(node: lhtml.HtmlElement) -> str:
    """Convert visible form input into Markdown text or a task-list marker."""
    input_type = (node.get("type") or "text").casefold()
    if input_type == "checkbox":
        return "[x] " if node.get("checked") is not None else "[ ] "
    if input_type in {"hidden", "password"}:
        return ""
    return _markdown_text(node.get("value") or node.get("placeholder") or "")


def _inline_markdown_svg(node: lhtml.HtmlElement) -> str:
    """Use accessible SVG labels instead of retaining inline SVG markup."""
    label = node.get("aria-label") or node.get("title")
    title_node = node.find(".//title")
    if not label and title_node is not None:
        label = title_node.text_content()
    return _markdown_text(label or "")


def _inline_markdown_embedded_media(node: lhtml.HtmlElement, content: str) -> str:
    """Represent embedded media as a Markdown link when a source is available."""
    source = node.get("src") or node.get("data")
    source_node = node.find(".//source")
    if not source and source_node is not None:
        source = source_node.get("src")
    if not source:
        return content
    label = content or _markdown_text(node.get("title") or "Embedded media")
    return f"[{label}]({_markdown_destination(source)})"


def _inline_markdown_media(
    node: lhtml.HtmlElement, tag: str, content: str
) -> str | None:
    """Convert links, images, controls, and embedded media to Markdown."""
    result: str | None = None
    if tag == "a":
        href = node.get("href")
        if not href:
            result = content
        else:
            label = content or _markdown_text(href)
            result = f"[{label}]({_markdown_destination(href)})"
    elif tag == "img":
        result = _markdown_image(node)
    elif tag == "br":
        result = "  \n"
    elif tag == "input":
        result = _inline_markdown_input(node)
    elif tag == "svg":
        result = _inline_markdown_svg(node)
    elif tag in {"iframe", "video", "audio", "object", "embed"}:
        result = _inline_markdown_embedded_media(node, content)
    return result


def _unicode_superscript(value: str) -> str:
    """Use Unicode superscript glyphs where Markdown has no superscript syntax."""
    table = str.maketrans("0123456789+-=()", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾")
    return value.translate(table)


def _unicode_subscript(value: str) -> str:
    """Use Unicode subscript glyphs where available instead of an HTML tag."""
    table = str.maketrans("0123456789+-=()", "₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎")
    return value.translate(table)


def _inline_markdown_element(node: lhtml.HtmlElement) -> str:
    """Convert one inline HTML element to its Markdown equivalent."""
    tag = _html_tag(node)
    if tag in HTML_HIDDEN_TAGS:
        return ""
    content = _inline_markdown(node)
    formatted = _inline_markdown_formatting(node, tag, content)
    if formatted is not None:
        return formatted
    media = _inline_markdown_media(node, tag, content)
    return media if media is not None else content


def _remove_redundant_image_filename_captions(value: str) -> str:
    """Drop italic captions that only repeat the adjacent image filename."""
    def replace(match: re.Match[str]) -> str:
        caption = re.sub(r"\\(.)", r"\1", match.group("caption"))
        destination = urllib.parse.unquote(
            urllib.parse.urlsplit(match.group("destination")).path
        )
        if normalize_image_name(caption) == normalize_image_name(destination):
            return match.group("image")
        return match.group(0)

    return REDUNDANT_IMAGE_FILENAME_CAPTION_RE.sub(replace, value)


def _markdown_table_cell(
    cell: lhtml.HtmlElement, *, use_html_line_breaks: bool = False
) -> str:
    """Render a table cell as a single Markdown table row value."""
    content = _render_html_children(cell).rstrip()
    content = _remove_redundant_image_filename_captions(content)
    if content.endswith(r"\|-"):
        content = content[:-3].rstrip()
    separator = "<br>" if use_html_line_breaks else " / "
    content = re.sub(r"\s*\n\s*", separator, content).strip()
    return content.replace("|", r"\|")

def _table_rows(table: lhtml.HtmlElement) -> list[list[lhtml.HtmlElement]]:
    """Extract only rows owned by this table, not rows in nested tables."""
    rows: list[list[lhtml.HtmlElement]] = []
    for group in table:
        if _html_tag(group) == "tr":
            row_groups = [group]
        elif _html_tag(group) in {"thead", "tbody", "tfoot"}:
            row_groups = [row for row in group if _html_tag(row) == "tr"]
        else:
            continue
        for row in row_groups:
            cells = [child for child in row if _html_tag(child) in {"td", "th"}]
            if cells:
                rows.append(cells)
    return rows


def _table_has_wide_row(rows: list[list[lhtml.HtmlElement]]) -> bool:
    """Return whether a table row exceeds the normal Markdown table width."""
    return any(len(row) > WIDE_TABLE_COLUMN_THRESHOLD for row in rows)


def _pipe_table(headers: list[str], rows: list[list[str]]) -> str:
    """Serialize a rectangular collection of cells as a GFM pipe table."""
    width = max(len(headers), max((len(row) for row in rows), default=0), 1)
    padded_headers = headers + [""] * (width - len(headers))
    lines = ["| " + " | ".join(padded_headers) + " |"]
    lines.append("| " + " | ".join("---" for _ in range(width)) + " |")
    for row in rows:
        padded = row + [""] * (width - len(row))
        lines.append("| " + " | ".join(padded) + " |")
    return "\n".join(lines)


def _render_quote_table(cells: list[lhtml.HtmlElement]) -> str | None:
    """Turn Fandom's three-cell quote layout into a Markdown blockquote."""
    if len(cells) != QUOTE_TABLE_CELL_COUNT:
        return None
    left = cells[0].text_content().strip()
    right = cells[2].text_content().strip()
    if left not in {"“", "\u2018", '"'} or right not in {"”", "\u2019", '"'}:
        return None

    paragraphs = [
        part.strip()
        for part in _render_html_children(cells[1]).split("\n\n")
        if part.strip()
    ]
    if not paragraphs:
        return ""
    quote = f"> {left}{paragraphs[0]}{right}"
    if len(paragraphs) == 1:
        return quote
    attribution = " ".join(paragraphs[1:])
    return f"{quote}\n>\n> — {attribution}"


def _is_table_section(cell: lhtml.HtmlElement) -> bool:
    """Return whether a one-cell row represents a table section heading."""
    has_image = any(_html_tag(child) == "img" for child in cell.iter())
    return "section" in (cell.get("class") or "").split() or (
        int(cell.get("colspan", "1")) > 1
        and not has_image
        and cell.find(".//strong") is not None
    )


def _render_image_layout_cell(value: str) -> str:
    """Separate an image and caption so both render outside a Markdown table."""
    image, separator, caption = value.partition(" / ")
    if separator and image.lstrip().startswith("!["):
        return image.strip() + ("\n\n" + caption.strip() if caption.strip() else "")
    return value.strip()


def _render_image_layout_table(rows: list[list[lhtml.HtmlElement]]) -> str:
    """Render image-bearing data tables as ordinary Markdown blocks."""
    blocks: list[str] = []
    for cells in rows:
        quote = _render_quote_table(cells)
        if quote is not None:
            if quote:
                blocks.append(quote)
            continue

        values = [
            (cell, value)
            for cell in cells
            if (value := _markdown_table_cell(cell).strip())
        ]
        if not values:
            continue
        if len(values) == 1:
            cell, value = values[0]
            if _is_table_section(cell):
                blocks.append(f"**{value.strip('* ')}**")
            else:
                blocks.append(_render_image_layout_cell(value))
            continue

        first_cell, first_value = values[0]
        first_has_image = any(_html_tag(child) == "img" for child in first_cell.iter())
        if not first_has_image and len(values) == INFOBOX_VALUE_CELL_COUNT:
            detail = _render_image_layout_cell(values[1][1])
            label = first_value.strip()
            if label.startswith("**") and label.endswith("**"):
                label = label[2:-2]
            label = label.rstrip(":").strip()
            if label and detail:
                blocks.append(f"**{label}:** {detail}")
                continue

        blocks.extend(
            rendered
            for _, value in values
            if (rendered := _render_image_layout_cell(value))
        )
    return "\n\n".join(blocks)


def _render_infobox_table(rows: list[list[lhtml.HtmlElement]]) -> str:
    """Convert a Fandom infobox into a readable two-column Markdown table."""
    body: list[list[str]] = []
    for cells in rows:
        values = [_markdown_table_cell(cell) for cell in cells]
        if len(cells) == 1:
            cell = cells[0]
            value = values[0]
            if _is_table_section(cell):
                body.append([f"**{value.strip('* ')}**", ""])
            elif value:
                body.append(["", value])
            continue
        label = values[0]
        detail = " / ".join(value for value in values[1:] if value)
        body.append([label, detail])
    return _pipe_table(["Property", "Details"], body)


def _recipe_section_heading(
    cells: list[lhtml.HtmlElement], plain_values: list[str]
) -> str | None:
    """Identify recipe section headings even when they include an icon image."""
    for cell, plain_value in zip(cells, plain_values, strict=True):
        image_labels = " ".join(
            label
            for image in cell.iter("img")
            for label in (image.get("alt", ""), image.get("title", ""))
            if label
        )
        heading_text = f"{plain_value} {image_labels}".casefold()
        for section in ("requirements", "materials"):
            if re.search(rf"\b{section}\b", heading_text):
                return section
    return None


def _is_recipe_infobox(rows: list[list[lhtml.HtmlElement]]) -> bool:
    """Recognize recipe tables by their requirement fields and crafting metadata."""
    labels = {cell.text_content().strip().casefold() for row in rows for cell in row}
    required_fields = {"requirements", "item", "quantity"}
    has_crafting_time = any(
        re.search(
            r"\b(?:seconds?|minutes?|hours?)\b", cell.text_content(), re.IGNORECASE
        )
        for row in rows
        for cell in row
    )
    has_material_section = any(
        _recipe_section_heading([cell], [cell.text_content().strip()]) == "materials"
        for row in rows
        for cell in row
    )
    return required_fields <= labels and (has_material_section or has_crafting_time)


def _is_unresolved_icon_template(value: str) -> bool:
    """Identify placeholder text left behind when an icon-link template is missing."""
    return "template:iconlink" in value.casefold()


@dataclass(slots=True)
class RecipeInfoboxData:
    """Parsed values from a recipe infobox."""

    skill: str | None
    skill_values: list[str]
    requirements: list[tuple[str, str]]
    materials: list[tuple[str, str]]
    pending_requirement: str | None
    pending_material: str | None
    section: str


def _recipe_infobox_row_values(
    cells: list[lhtml.HtmlElement],
) -> tuple[list[str], list[str]]:
    """Return Markdown and plain-text values for one recipe table row."""
    values = [
        re.sub(r"!\[[^\]]*\]\([^)]+\)\s*", "", _markdown_table_cell(cell)).strip()
        for cell in cells
    ]
    plain_values = [cell.text_content().strip() for cell in cells]
    return values, plain_values


def _consume_recipe_material_row(
    data: RecipeInfoboxData, values: list[str], plain_values: list[str]
) -> None:
    """Collect a material name and its matching quantity from an infobox row."""
    if any(_is_unresolved_icon_template(value) for value in plain_values):
        return

    if len(values) > 1:
        label = plain_values[0].rstrip(":").casefold()
        detail = " / ".join(value for value in values[1:] if value)
        if label == "item":
            data.pending_material = detail or None
        elif label == "quantity":
            if data.pending_material and detail:
                data.materials.append((data.pending_material, detail))
            data.pending_material = None
        elif data.pending_material and detail:
            data.materials.append((data.pending_material, detail))
            data.pending_material = None
        return

    value = values[0]
    if not value:
        return
    if data.pending_material is None:
        data.pending_material = value
    else:
        data.materials.append((data.pending_material, value))
        data.pending_material = None


def _consume_recipe_requirement_row(
    data: RecipeInfoboxData, values: list[str], plain_values: list[str]
) -> None:
    """Collect a labelled requirement and its value."""
    if any(_is_unresolved_icon_template(value) for value in plain_values):
        return

    if len(values) > 1:
        label = values[0].strip()
        detail = " / ".join(value for value in values[1:] if value)
        if label and detail:
            data.requirements.append((label.rstrip(":"), detail))
            data.pending_requirement = None
        elif label:
            data.pending_requirement = label.rstrip(":")
        return

    value = values[0]
    if value and data.pending_requirement:
        data.requirements.append((data.pending_requirement, value))
        data.pending_requirement = None


def _consume_recipe_skill_row(
    data: RecipeInfoboxData, values: list[str], plain_values: list[str]
) -> None:
    """Collect the skill label and any values shown before Requirements."""
    if len(values) > 1:
        label = values[0]
        detail = " / ".join(value for value in values[1:] if value)
        if label and not detail:
            data.skill = label
        elif label and detail:
            data.requirements.append((label.rstrip(":"), detail))
        return

    value = values[0]
    if value and not _is_unresolved_icon_template(plain_values[0]):
        data.skill_values.append(value)


def _parse_recipe_infobox(rows: list[list[lhtml.HtmlElement]]) -> RecipeInfoboxData:
    """Parse recipe metadata and material pairs from the infobox rows."""
    data = RecipeInfoboxData(
        skill=None,
        skill_values=[],
        requirements=[],
        materials=[],
        pending_requirement=None,
        pending_material=None,
        section="",
    )
    for cells in rows:
        values, plain_values = _recipe_infobox_row_values(cells)
        recipe_heading = _recipe_section_heading(cells, plain_values)
        if recipe_heading is not None:
            data.section = recipe_heading
            data.pending_requirement = None
            continue
        if len(cells) == 1 and _is_table_section(cells[0]):
            data.section = plain_values[0].casefold()
            data.pending_requirement = None
            continue
        if (
            data.section == "requirements"
            and len(cells) > 1
            and plain_values[0].rstrip(":").casefold() == "item"
        ):
            data.section = "materials"
        if not any(plain_values):
            continue

        if data.section == "materials":
            _consume_recipe_material_row(data, values, plain_values)
        elif data.section == "requirements":
            _consume_recipe_requirement_row(data, values, plain_values)
        else:
            _consume_recipe_skill_row(data, values, plain_values)

    if data.pending_material:
        data.materials.append((data.pending_material, ""))
    return data


def _render_recipe_skill(data: RecipeInfoboxData) -> list[str]:
    """Format the recipe skill, level, and crafting time."""
    if not data.skill:
        return data.skill_values.copy()

    level = next(
        (value for value in data.skill_values if re.fullmatch(r"\d+(?:\.\d+)?", value)),
        None,
    )
    duration = next(
        (
            value
            for value in data.skill_values
            if re.search(r"\b(?:seconds?|minutes?|hours?)\b", value, re.IGNORECASE)
        ),
        None,
    )
    blocks = [
        f"**Skill:** {data.skill} (Level {level})"
        if level
        else f"**Skill:** {data.skill}"
    ]
    if duration:
        blocks.append(f"**Crafting time:** {duration}")
    blocks.extend(
        f"**Details:** {value}"
        for value in data.skill_values
        if value not in {level, duration}
    )
    return blocks


def _render_recipe_infobox_table(rows: list[list[lhtml.HtmlElement]]) -> str:
    """Render recipe metadata and ingredient pairs without splitting them into rows."""
    data = _parse_recipe_infobox(rows)
    blocks = _render_recipe_skill(data)
    if data.requirements:
        blocks.append("**Requirements**")
        blocks.extend(f"**{label}:** {value}" for label, value in data.requirements)
    if data.materials:
        blocks.extend(
            [
                "**Materials**",
                _pipe_table(
                    ["Item", "Quantity"],
                    [[item, quantity] for item, quantity in data.materials],
                ),
            ]
        )
    return "\n\n".join(blocks) if blocks else _render_infobox_table(rows)


def _table_has_list_content(rows: list[list[lhtml.HtmlElement]]) -> bool:
    """Return whether a table contains a list that cannot fit in a pipe-table cell."""
    return any(
        _html_tag(child) in {"ul", "ol"}
        for row in rows
        for cell in row
        for child in cell.iter()
    )


def _table_has_long_list_cell(rows: list[list[lhtml.HtmlElement]]) -> bool:
    """Return whether a table cell contains enough list items to need line breaks."""
    return any(
        sum(1 for child in cell.iter() if _html_tag(child) == "li")
        > TABLE_CELL_LIST_BREAK_THRESHOLD
        for row in rows
        for cell in row
    )


def _table_is_single_column(rows: list[list[lhtml.HtmlElement]]) -> bool:
    """Return whether every row in a table contains at most one cell."""
    return all(len(row) == 1 for row in rows)


def _render_list_content_table(rows: list[list[lhtml.HtmlElement]]) -> str:
    """Render list-bearing table cells as standalone Markdown blocks."""
    blocks = [
        rendered
        for row in rows
        for cell in row
        if (rendered := _render_html_children(cell).strip())
    ]
    return "\n\n".join(blocks)


def _table_cell_has_block_layout(cell: lhtml.HtmlElement) -> bool:
    """Identify one-cell wrappers whose descendants need block-level rendering."""
    block_tags = {"div", "h1", "h2", "h3", "h4", "h5", "h6", "ol", "p", "table", "ul"}
    return any(_html_tag(child) in block_tags for child in cell.iterdescendants())


def _render_generic_table(
    rows: list[list[lhtml.HtmlElement]], *, use_html_line_breaks: bool = False
) -> str:
    """Convert ordinary HTML tables to Markdown, dropping empty spacer columns."""
    rendered_rows = [
        [
            _markdown_table_cell(cell, use_html_line_breaks=use_html_line_breaks)
            for cell in row
        ]
        for row in rows
    ]
    width = max(len(row) for row in rendered_rows)
    retained_columns = [
        index
        for index in range(width)
        if any(index < len(row) and row[index] for row in rendered_rows)
    ]
    compact_rows = [
        [row[index] if index < len(row) else "" for index in retained_columns]
        for row in rendered_rows
    ]
    has_header = any(_html_tag(cell) == "th" for cell in rows[0])
    if has_header:
        headers = compact_rows[0]
        values = compact_rows[1:]
    else:
        headers = [f"Column {index + 1}" for index in range(len(retained_columns))]
        values = compact_rows
    return _pipe_table(headers, values)

def _render_gallery_table(rows: list[list[lhtml.HtmlElement]]) -> str:
    """Render each gallery item as a standalone Markdown image and caption."""
    items: list[str] = []
    for row in rows:
        for cell in row:
            content = _markdown_table_cell(cell).strip()
            if not content:
                continue
            image, separator, caption = content.partition(" / ")
            if separator:
                content = image.strip()
                if caption.strip():
                    content += "\n\n" + caption.strip()
            items.append(_normalize_markdown_emphasis(content))
    return "\n\n".join(items)


def _render_single_cell_wrapper_table(
    rows: list[list[lhtml.HtmlElement]], caption: str
) -> str | None:
    """Render a one-cell wrapper as blocks when it contains mixed layout."""
    if len(rows) != 1 or len(rows[0]) != 1:
        return None
    cell = rows[0][0]
    if not _table_cell_has_block_layout(cell):
        return None
    content = _render_html_children(cell)
    if caption and content:
        return f"**{caption}**\n\n{content}"
    return content


def _render_other_html_table(
    rows: list[list[lhtml.HtmlElement]],
    table_classes: list[str],
    has_images: bool,
    caption: str,
) -> str:
    """Render image, quote, infobox, and generic tables."""
    if has_images:
        return _render_image_layout_table(rows)
    if len(rows) == 1:
        quote = _render_quote_table(rows[0])
        if quote is not None:
            return f"**{caption}**\n\n{quote}" if caption else quote
    if "infobox" in table_classes:
        return _render_infobox_table(rows)
    return _render_generic_table(rows)


def _render_html_table(node: lhtml.HtmlElement) -> str:
    """Convert infoboxes, galleries, and data tables to Markdown."""
    rows = _table_rows(node)
    caption_node = node.find("caption")
    caption = _inline_markdown(caption_node).strip() if caption_node is not None else ""
    if not rows:
        return f"**{caption}**" if caption else ""

    wrapper_content = _render_single_cell_wrapper_table(rows, caption)
    if wrapper_content is not None:
        return wrapper_content

    table_classes = (node.get("class") or "").split()
    has_images = any(
        _html_tag(child) == "img"
        for row in rows
        for cell in row
        for child in cell.iter()
    )
    use_html_line_breaks = has_images or _table_has_long_list_cell(rows)
    if "gallery" in table_classes:
        table = _render_gallery_table(rows)
    elif "uses-material-list" in table_classes:
        table = _render_generic_table(rows, use_html_line_breaks=use_html_line_breaks)
    elif _is_recipe_infobox(rows):
        table = _render_recipe_infobox_table(rows)
    elif _table_has_list_content(rows):
        table = (
            _render_list_content_table(rows)
            if _table_is_single_column(rows)
            else _render_generic_table(rows, use_html_line_breaks=use_html_line_breaks)
        )
    elif has_images:
        table = _render_image_layout_table(rows)
    elif _table_has_wide_row(rows):
        table = _render_generic_table(rows)
    else:
        table = _render_other_html_table(rows, table_classes, has_images, caption)
    return f"**{caption}**\n\n{table}" if caption else table


def _is_html_block_element(node: lhtml.HtmlElement) -> bool:
    """Recognize standard and custom elements that contain block layout."""
    if _html_tag(node) in HTML_HIDDEN_TAGS:
        return False
    if _html_tag(node) in HTML_BLOCK_TAGS:
        return True
    style = (node.get("style") or "").casefold().replace(" ", "")
    if any(
        f"display:{display}" in style
        for display in ("block", "flex", "grid", "flow-root", "table", "list-item")
    ):
        return True
    return any(
        isinstance(child.tag, str) and _html_tag(child) in HTML_BLOCK_TAGS
        for child in node
    )


def _render_html_list(node: lhtml.HtmlElement, depth: int = 0) -> str:
    """Convert nested HTML ordered and unordered lists to Markdown lists."""
    ordered = _html_tag(node) == "ol"
    try:
        start = max(1, int(node.get("start", "1")))
    except ValueError:
        start = 1
    lines: list[str] = []
    for item_number, item in enumerate(node, start):
        if _html_tag(item) != "li":
            continue
        content = [_markdown_text(item.text or "")]
        nested: list[str] = []
        for child in item:
            if not isinstance(child.tag, str):
                content.append(_markdown_text(child.tail or ""))
                continue
            if _html_tag(child) in {"ul", "ol"}:
                nested.append(_render_html_list(child, depth + 2))
            elif _is_html_block_element(child):
                content.append(_render_html_block(child).replace("\n", " "))
            else:
                content.append(_inline_markdown_element(child))
            content.append(_markdown_text(child.tail or ""))
        marker = f"{item_number}." if ordered else "-"
        lines.append(" " * depth + marker + " " + "".join(content).strip())
        lines.extend(part for part in nested if part)
    return "\n".join(lines)


def _render_html_pre(node: lhtml.HtmlElement) -> str:
    """Convert a preformatted HTML block to a fenced Markdown code block."""
    code = node.text_content().strip("\n")
    if not code:
        return ""
    runs = re.findall(re.escape(chr(96)) + r"+", code)
    fence = chr(96) * max(3, max((len(run) for run in runs), default=0) + 1)
    language = ""
    for class_name in (node.get("class") or "").split():
        if class_name.startswith("language-"):
            language = class_name.removeprefix("language-")
            break
    return f"{fence}{language}\n{code}\n{fence}"


def _render_html_definition_list(node: lhtml.HtmlElement) -> str:
    """Convert definition lists to bold terms and indented definitions."""
    lines: list[str] = []
    term = ""
    for child in node:
        if not isinstance(child.tag, str):
            continue
        tag = _html_tag(child)
        content = _inline_markdown(child).strip()
        if tag == "dt":
            term = content
        elif tag == "dd":
            lines.append(f"**{term}**\n: {content}" if term else content)
            term = ""
    return "\n\n".join(lines)


def _render_html_special_block(node: lhtml.HtmlElement) -> str | None:
    """Render block elements with specialized Markdown representations."""
    tag = _html_tag(node)
    rendered: str | None = None
    if tag == "table":
        rendered = _render_html_table(node)
    elif tag == "blockquote":
        content = _render_html_children(node)
        rendered = "\n".join(
            "> " + line if line else ">" for line in content.splitlines()
        )
    elif tag == "pre":
        rendered = _render_html_pre(node)
    elif tag in {"ul", "ol"}:
        rendered = _render_html_list(node)
    elif tag == "hr":
        rendered = "---"
    elif tag == "dl":
        rendered = _render_html_definition_list(node)
    elif tag == "center":
        content = _render_html_children(node)
        rendered = "\n".join(
            "> " + line if line else ">" for line in content.splitlines()
        )
    return rendered


def _render_html_block(node: lhtml.HtmlElement) -> str:
    """Convert one HTML block element into Markdown."""
    tag = _html_tag(node)
    special = _render_html_special_block(node)
    rendered = special if special is not None else ""
    if special is None:
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            level = int(tag[1])
            content = _styled_markdown_content(node, _inline_markdown(node).strip())
            rendered = f"{'#' * level} {content}"
        elif tag == "p":
            content = _inline_markdown(node).strip()
            rendered = _styled_markdown_content(node, content)
        elif tag == "figcaption":
            caption = _inline_markdown(node).strip()
            rendered = f"_{caption}_" if caption else ""
        else:
            content = _styled_markdown_content(node, _render_html_children(node))
            if "footnotes" in (
                node.get("class") or ""
            ).split() and not content.startswith("## Notes"):
                content = f"## Notes\n\n{content}" if content else "## Notes"
            if tag == "summary":
                rendered = f"**{_inline_markdown(node).strip()}**"
            elif tag in {"iframe", "video", "audio", "object", "embed"}:
                rendered = _inline_markdown_element(node)
            elif tag == "canvas":
                rendered = _markdown_text(
                    node.get("aria-label") or node.get("title") or content
                )
            else:
                rendered = content
    return rendered


def _render_html_children(node: lhtml.HtmlElement) -> str:
    """Render mixed block and inline descendants, discarding all wrapper tags."""
    blocks: list[str] = []
    inline: list[str] = [_markdown_text(node.text or "")]
    for child in node:
        if not isinstance(child.tag, str):
            inline.append(_markdown_text(child.tail or ""))
            continue
        if _is_html_block_element(child):
            current = "".join(inline).strip()
            if current:
                blocks.append(current)
            rendered = _render_html_block(child)
            if rendered.strip():
                blocks.append(rendered)
            inline = []
        else:
            inline.append(_inline_markdown_element(child))
        inline.append(_markdown_text(child.tail or ""))
    current = "".join(inline).strip()
    if current:
        blocks.append(current)
    return "\n\n".join(blocks)


def _markdown_heading_slug(label: str) -> str:
    """Approximate the heading anchor generated by common Markdown readers."""
    slug = urllib.parse.unquote(label).casefold().replace("_", " ")
    slug = re.sub(r"[^\w\- ]", "", slug)
    return re.sub(r"\s+", "-", slug).strip("-")


def _normalize_markdown_anchor_hrefs(root: lhtml.HtmlElement) -> None:
    """Rewrite MediaWiki heading IDs to Markdown-generated heading anchors."""
    headings = root.iter("h1", "h2", "h3", "h4", "h5", "h6")
    heading_slugs: dict[str, str] = {}
    used: dict[str, int] = {}
    for heading in headings:
        identifier = heading.get("id")
        if not identifier:
            continue
        base = _markdown_heading_slug(heading.text_content())
        count = used.get(base, 0)
        used[base] = count + 1
        heading_slugs[identifier] = f"{base}-{count}" if count else base

    for link in root.iter("a"):
        href = link.get("href", "")
        path, separator, fragment = href.partition("#")
        if not separator or (path and not path.endswith(".md")):
            continue
        decoded = urllib.parse.unquote(fragment)
        slug = heading_slugs.get(decoded)
        if slug is not None:
            link.set("href", path + "#" + slug)


def _contains_custom_render_table(fragment: str) -> bool:
    """Return whether HTML contains a gallery, image, or wide sparse table."""
    root = lhtml.fragment_fromstring(fragment or "", create_parent="div")
    return any(
        {"gallery", "uses-material-list"} & set((table.get("class") or "").split())
        or any(_html_tag(child) == "img" for child in table.iter())
        or _table_has_list_content(_table_rows(table))
        or any(
            len(row) == 1 and _table_cell_has_block_layout(row[0])
            for row in _table_rows(table)
        )
        or _table_has_wide_row(_table_rows(table))
        for table in root.iter("table")
    )


def _html_fragment_to_markdown(fragment: str) -> str:
    """Convert an HTML fragment to Markdown without structural HTML tags."""
    root = lhtml.fragment_fromstring(fragment or "", create_parent="div")
    _normalize_markdown_anchor_hrefs(root)
    return _render_html_children(root).strip()


def _convert_html_to_markdown(
    pandoc: str, rewritten_html: str, note: str
) -> tuple[str, str, bool]:
    """Convert rewritten HTML to Markdown, normalizing any HTML Pandoc preserves."""
    if _contains_custom_render_table(rewritten_html):
        return _html_fragment_to_markdown(rewritten_html), note, False

    try:
        markdown_body = _run_pandoc(pandoc, rewritten_html, "html", "gfm")
    except Exception as markdown_error:
        markdown_body = _html_fragment_to_markdown(rewritten_html)
        note = (
            f"{note} HTML-to-Markdown conversion failed; converted the HTML "
            f"fragment to Markdown instead: {markdown_error}"
        ).strip()
        return markdown_body, note, True

    if _contains_html_markup(markdown_body):
        markdown_body = _html_fragment_to_markdown(rewritten_html)
        note = (
            f"{note} Converted HTML elements retained by Pandoc into Markdown."
        ).strip()
        return markdown_body, note, True
    return markdown_body, note, False

def _source_html_markdown_fallback(
    page: DumpPage, pandoc: str, resources: PageExportResources
) -> str:
    """Render original source as HTML and normalize it to Markdown."""
    try:
        html_fragment = _run_pandoc(pandoc, page.text, "mediawiki", "html")
    except Exception:
        if not _contains_html_markup(page.text):
            raise
        html_fragment = page.text
    rewritten_html, _, _ = _rewrite_page_html(html_fragment, page, resources, "")
    return _html_fragment_to_markdown(rewritten_html)


def _source_markdown_fallback(
    page: DumpPage,
    pandoc: str,
    conversion_stage: str,
    error: Exception,
    resources: PageExportResources,
) -> PageConversion:
    """Use direct source conversion, ensuring the final fallback contains Markdown."""
    conversion_stage_error = f"{conversion_stage} failed: {error}"
    try:
        markdown_body = _run_pandoc(pandoc, page.text, "mediawiki", "gfm")
    except Exception as fallback_error:
        try:
            markdown_body = _source_html_markdown_fallback(page, pandoc, resources)
        except Exception as html_error:
            safe_source = _strip_html_markup(page.text)
            markdown_body = "\n".join("> " + line for line in safe_source.splitlines())
            return PageConversion(
                markdown_body=markdown_body,
                status="conversion_failed",
                note=(
                    f"{conversion_stage_error}; source fallback failed: "
                    f"{fallback_error}; HTML fallback failed: {html_error}"
                ),
                conversion_errors=1,
            )
        return PageConversion(
            markdown_body=markdown_body,
            status="raw_fallback",
            note=(
                f"{conversion_stage_error} Direct Markdown conversion failed; "
                "rendered the original source through HTML-to-Markdown fallback."
            ),
        )

    if _contains_html_markup(markdown_body):
        try:
            markdown_body = _source_html_markdown_fallback(page, pandoc, resources)
        except Exception as html_error:
            markdown_body = _strip_html_markup(markdown_body)
            return PageConversion(
                markdown_body=markdown_body,
                status="conversion_failed",
                note=(
                    f"{conversion_stage_error} Pandoc retained HTML and the "
                    f"Markdown normalization fallback failed: {html_error}"
                ),
                conversion_errors=1,
            )
        return PageConversion(
            markdown_body=markdown_body,
            status="raw_fallback",
            note=(
                f"{conversion_stage_error} Pandoc retained HTML; converted the "
                "original source into Markdown instead."
            ),
        )

    return PageConversion(
        markdown_body=markdown_body,
        status="raw_fallback",
        note=f"{conversion_stage_error} Direct source-to-Markdown fallback used.",
    )


def _convert_page_body(
    page: DumpPage,
    expanded: str,
    expansion_error: str,
    pandoc: str,
    postprocess: PostprocessFunction,
    categories: list[str],
    stats: RenderStats,
    resources: PageExportResources,
    categories_seen: set[str],
    deferred_material_tables: dict[str, str] | None = None,
) -> PageConversion:
    """Run postprocessing and conversion, falling back at each recoverable stage."""
    conversion_stage = "post-expansion cleanup"
    try:
        expanded = postprocess(expanded, categories, stats)
        categories[:] = sorted(set(categories), key=str.casefold)
        categories_seen.update(categories)

        conversion_stage = "Pandoc MediaWiki-to-HTML"
        html_fragment, status, note = _render_mediawiki_html(
            page, expanded, pandoc, deferred_material_tables
        )

        conversion_stage = "HTML link and image rewriting"
        rewritten_html, note, rewrite_failed = _rewrite_page_html(
            html_fragment, page, resources, note
        )
        if rewrite_failed:
            status = "html_fallback"

        conversion_stage = "Pandoc HTML-to-GFM"
        markdown_body, note, markdown_failed = _convert_html_to_markdown(
            pandoc, rewritten_html, note
        )
        if markdown_failed:
            status = "html_fallback"

        if expansion_error and status == "ok":
            status = "expand_failed"
        return PageConversion(markdown_body, status, note)
    except Exception as exc:
        return _source_markdown_fallback(page, pandoc, conversion_stage, exc, resources)


def _export_one_page(
    page: DumpPage,
    title_to_filename: dict[str, str],
    context: WikiContext,
    preprocess: PreprocessFunction,
    postprocess: PostprocessFunction,
    infobox_defs: Mapping[str, object],
    pandoc: str,
    redirects: dict[str, str],
    image_index: dict[str, Path],
    image_dir: Path | None,
    pages_dir: Path,
    asset_dir: Path,
    copied_images: dict[Path, str],
    used_asset_names: dict[str, Path],
    missing_links: set[str],
    missing_images: set[str],
    categories_seen: set[str],
    source_url: str | None,
    material_usage_index: dict[str, list[tuple[str, str, list[tuple[str, str]]]]]
    | None = None,
) -> tuple[PageExportResult, int, int]:
    """Expand and convert one article, recovering gracefully from parser failures."""
    filename = title_to_filename[normalize_title(page.title)]
    stats: RenderStats = {
        "infoboxes": 0,
        "missing_templates": [],
        "infobox_left": 0,
        "missing_images": [],
        "redlinks": 0,
        "unrendered": 0,
    }
    categories: list[str] = []
    resources = PageExportResources(
        title_to_filename=title_to_filename,
        redirects=redirects,
        image_index=image_index,
        image_dir=image_dir,
        asset_dir=asset_dir,
        copied_images=copied_images,
        used_asset_names=used_asset_names,
        missing_links=missing_links,
        missing_images=missing_images,
    )

    expanded, expansion_error, expansion_errors, deferred_material_tables = (
        _expand_page(
            page, context, preprocess, infobox_defs, stats, material_usage_index
        )
    )
    conversion = _convert_page_body(
        page,
        expanded,
        expansion_error,
        pandoc,
        postprocess,
        categories,
        stats,
        resources,
        categories_seen,
        deferred_material_tables,
    )
    if expansion_error:
        conversion.note = (
            f"{conversion.note} {expansion_error}".strip()
            if conversion.note
            else expansion_error
        )

    document = _page_markdown(
        page.title, categories, conversion.markdown_body, source_url
    )
    (pages_dir / filename).write_text(document, encoding="utf-8")
    result: PageExportResult = {
        "title": page.title,
        "filename": filename,
        "status": conversion.status,
        "categories": len(categories),
        "infoboxes": stats["infoboxes"],
        "infoboxes_unconverted": stats["infobox_left"],
        "missing_templates": sorted(set(stats["missing_templates"]), key=str.casefold),
        "error": conversion.note[:1000],
    }
    return result, expansion_errors, conversion.conversion_errors


def _write_workspace_index(
    page_results: list[PageExportResult], output_dir: Path
) -> None:
    """Write the Zettlr workspace root index."""
    index_lines = ["# The Long Dark Wiki", "", f"Articles: {len(page_results)}", ""]
    for result in sorted(page_results, key=lambda item: item["title"].casefold()):
        title = result["title"]
        filename = result["filename"]
        link = "pages/" + _safe_destination(filename)
        index_lines.append(f"- [{_markdown_label(title)}]({link})")
    (output_dir / "Index.md").write_text(
        "\n".join(index_lines) + "\n", encoding="utf-8"
    )


def _write_conversion_report(report: ExportReport, output_dir: Path) -> None:
    """Write diagnostics for the completed Markdown export."""
    (output_dir / "conversion-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _load_renderer_pipeline(
    dump_path: Path,
) -> tuple[
    WikiContext,
    list[DumpPage],
    dict[str, str],
    PreprocessFunction,
    PostprocessFunction,
    Mapping[str, object],
    Callable[[str], str],
    int,
]:
    """Load the existing Fandom renderer helpers and the expanded wiki context."""
    renderer = importlib.import_module("pipeline.wiki_to_html")
    preprocess = cast("PreprocessFunction", renderer.prepass)
    postprocess = cast("PostprocessFunction", renderer.postexpand)
    load_infobox_defs = cast(
        "Callable[[WikiContext], Mapping[str, object]]", renderer.load_infobox_defs
    )
    wtp_factory = cast("WtpFactory", renderer.Wtp)
    pandoc_selftest = cast("Callable[[str], str]", renderer.pandoc_selftest)
    context, articles, redirects, redirect_count = _load_wiki_context(
        dump_path, wtp_factory
    )
    infobox_defs = load_infobox_defs(context)
    return (
        context,
        articles,
        redirects,
        preprocess,
        postprocess,
        infobox_defs,
        pandoc_selftest,
        redirect_count,
    )


def _prepare_export(
    dump_path: Path, mapping_path: Path, output_dir: Path, image_dir: Path | None
) -> ExportState:
    """Load the dump context and initialize resources for the export."""
    title_to_filename = load_filename_mapping(mapping_path)
    (
        context,
        articles,
        redirects,
        preprocess,
        postprocess,
        infobox_defs,
        pandoc_selftest,
        redirect_count,
    ) = _load_renderer_pipeline(dump_path)

    article_titles = {normalize_title(page.title) for page in articles}
    missing_mappings = article_titles - title_to_filename.keys()
    extra_mappings = title_to_filename.keys() - article_titles
    if missing_mappings or extra_mappings:
        detail = f"missing {len(missing_mappings)}; stale {len(extra_mappings)}"
        raise ValueError(f"Filename mapping mismatch: {detail}.")

    pages_dir = output_dir / "pages"
    asset_dir = output_dir / "assets"
    pages_dir.mkdir(parents=True, exist_ok=True)
    pandoc = _pandoc_path()
    pandoc_selftest(pandoc)

    return {
        "title_to_filename": title_to_filename,
        "context": context,
        "articles": articles,
        "material_usage_index": _build_material_usage_index(articles),
        "redirects": redirects,
        "redirect_count": redirect_count,
        "preprocess": preprocess,
        "postprocess": postprocess,
        "infobox_defs": infobox_defs,
        "pandoc": pandoc,
        "image_index": _build_image_index(image_dir),
        "image_dir": image_dir,
        "pages_dir": pages_dir,
        "asset_dir": asset_dir,
        "copied_images": {},
        "used_asset_names": {},
        "missing_links": set(),
        "missing_images": set(),
        "page_results": [],
        "categories_seen": set(),
        "expansion_errors": 0,
        "conversion_errors": 0,
    }


def export_dump_to_markdown(
    dump_path: Path,
    mapping_path: Path,
    output_dir: Path,
    *,
    image_dir: Path | None = None,
    source_url: str | None = "https://thelongdark.fandom.com",
) -> ExportReport:
    """Convert a dump into linked Markdown files using the audited filename map."""
    state = _prepare_export(dump_path, mapping_path, output_dir, image_dir)

    for page in sorted(state["articles"], key=lambda item: item.title.casefold()):
        result, page_expansion_errors, page_conversion_errors = _export_one_page(
            page,
            state["title_to_filename"],
            state["context"],
            state["preprocess"],
            state["postprocess"],
            state["infobox_defs"],
            state["pandoc"],
            state["redirects"],
            state["image_index"],
            state["image_dir"],
            state["pages_dir"],
            state["asset_dir"],
            state["copied_images"],
            state["used_asset_names"],
            state["missing_links"],
            state["missing_images"],
            state["categories_seen"],
            source_url,
            state["material_usage_index"],
        )
        state["page_results"].append(result)
        state["expansion_errors"] += page_expansion_errors
        state["conversion_errors"] += page_conversion_errors

    _write_workspace_index(state["page_results"], output_dir)
    summary: ExportSummary = {
        "article_pages": len(state["articles"]),
        "exported_pages": len(state["page_results"]),
        "redirects": state["redirect_count"],
        "expansion_errors": state["expansion_errors"],
        "conversion_errors": state["conversion_errors"],
        "repaired_pages": sum(
            page["status"] == "repaired" for page in state["page_results"]
        ),
        "raw_fallback_pages": sum(
            page["status"] == "raw_fallback" for page in state["page_results"]
        ),
        "html_fallback_pages": sum(
            page["status"] == "html_fallback" for page in state["page_results"]
        ),
        "unresolved_links": len(state["missing_links"]),
        "missing_images": len(state["missing_images"]),
        "categories": len(state["categories_seen"]),
        "infoboxes": sum(page["infoboxes"] for page in state["page_results"]),
        "unconverted_infoboxes": sum(
            page["infoboxes_unconverted"] for page in state["page_results"]
        ),
        "missing_templates": sum(
            len(page["missing_templates"]) for page in state["page_results"]
        ),
    }
    report: ExportReport = {
        "summary": summary,
        "pages": state["page_results"],
        "unresolved_links": sorted(state["missing_links"]),
        "missing_images": sorted(state["missing_images"]),
    }
    _write_conversion_report(report, output_dir)
    return report
