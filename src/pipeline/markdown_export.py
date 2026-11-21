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
from typing import TYPE_CHECKING, Any, TypedDict, cast

import defusedxml.ElementTree as SafeET
from lxml import etree
from lxml import html as lhtml

if TYPE_CHECKING:
    from collections.abc import Iterator
    from xml.etree.ElementTree import Element


REDIRECT_RE = re.compile(r"\s*#redirect\s*:?\s*\[\[([^\]|]+)", re.IGNORECASE)
MAX_FILENAME_BYTES = 255
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


class ExportState(TypedDict):
    title_to_filename: dict[str, str]
    context: Any
    articles: list[DumpPage]
    redirects: dict[str, str]
    redirect_count: int
    preprocess: Any
    postprocess: Any
    infobox_defs: Any
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


def resolve_internal_href(
    href: str,
    current_title: str,
    title_to_filename: dict[str, str],
    redirects: dict[str, str],
) -> str | None:
    """Resolve a Pandoc HTML link to a local Markdown file.

    External URLs and same-page anchors are returned unchanged. Unresolved
    wiki links return None so the caller can retain their visible label.
    """
    if not href or href.startswith("#") or href.startswith("//"):
        return href

    parsed = urllib.parse.urlsplit(href)
    scheme = parsed.scheme.casefold()
    is_interwiki = len(scheme) in {2, 3} or scheme in NAMED_INTERWIKI_SCHEMES
    if scheme in EXTERNAL_SCHEMES or (
        scheme and scheme not in LOCAL_WIKI_SCHEMES and is_interwiki
    ):
        return href

    raw_path = parsed.path if not scheme else href
    raw_target = urllib.parse.unquote(raw_path).strip()
    raw_target = raw_target.removeprefix("./").removeprefix("/")
    if not raw_target:
        return "#" + _safe_destination(parsed.fragment) if parsed.fragment else None

    key = normalize_title(raw_target.lstrip(":"))
    current_key = normalize_title(current_title)
    fragment = urllib.parse.unquote(parsed.fragment).replace(" ", "_")
    visited: set[str] = set()

    while key in redirects and key not in visited:
        visited.add(key)
        redirect_target = redirects[key]
        redirect_title, separator, redirect_fragment = redirect_target.partition("#")
        key = normalize_title(redirect_title)
        if not fragment and separator:
            fragment = redirect_fragment

    filename = title_to_filename.get(key)
    if filename is None:
        return None

    destination = "./" + _safe_destination(filename)
    if key == current_key and fragment:
        return "#" + _safe_destination(fragment)
    if fragment:
        destination += "#" + _safe_destination(fragment)
    return destination


def load_filename_mapping(path: Path) -> dict[str, str]:
    """Load and validate the committed title-to-filename audit mapping."""
    data = json.loads(path.read_text(encoding="utf-8"))
    entries = data.get("mappings")
    if not isinstance(entries, list):
        raise ValueError("Filename mapping JSON must contain a 'mappings' list.")

    title_to_filename: dict[str, str] = {}
    used_filenames: dict[str, str] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("Every filename mapping entry must be an object.")
        title = entry.get("title")
        filename = entry.get("filename")
        if not isinstance(title, str) or not isinstance(filename, str):
            raise ValueError("Each mapping needs string 'title' and 'filename' fields.")
        if (
            not filename.endswith(".md")
            or "/" in filename
            or "\\" in filename
            or ":" in filename
            or filename in {".md", "..md"}
        ):
            raise ValueError(f"Unsafe Markdown filename for {title!r}: {filename!r}")
        if len(filename.encode("utf-8")) > MAX_FILENAME_BYTES:
            raise ValueError(
                f"Filename exceeds {MAX_FILENAME_BYTES} bytes: {filename!r}"
            )
        if any(unicodedata.category(char) == "Cc" for char in filename):
            raise ValueError(f"Filename contains control characters: {filename!r}")

        normalized_title = normalize_title(title)
        folded_filename = unicodedata.normalize("NFC", filename).casefold()
        if normalized_title in title_to_filename:
            raise ValueError(f"Duplicate normalized page title: {title!r}")
        if folded_filename in used_filenames:
            other = used_filenames[folded_filename]
            raise ValueError(f"Filename collision in mapping: {other!r}, {filename!r}")
        title_to_filename[normalized_title] = filename
        used_filenames[folded_filename] = filename

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


def _read_revision(
    revision: Element, model: str, text: str
) -> tuple[str, str]:
    """Read legacy revision fields or slot-based revision content."""
    for child in revision:
        tag = _xml_local_name(child)
        if tag == "slots":
            slots = [
                slot for slot in child if _xml_local_name(slot) == "slot"
            ]
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
    dump_path: Path, wtp_factory: Any
) -> tuple[Any, list[DumpPage], dict[str, str], int]:
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
        get_pandoc_path = getattr(pypandoc, "get_pandoc_path")
        return str(get_pandoc_path())
    except (ImportError, OSError, RuntimeError):
        binary = shutil.which("pandoc")
        if binary is None:
            raise RuntimeError(
                "Pandoc was not found. Install the project dependencies first."
            )
        return binary


def _run_pandoc(
    binary: str, text: str, source_format: str, target_format: str
) -> str:
    """Convert one document fragment with Pandoc."""
    command = [
        binary,
        "--from",
        source_format,
        "--to",
        target_format,
        "--wrap=none",
    ]
    if target_format == "html":
        command.append("--mathml")
    result = subprocess.run(  # noqa: S603
        command,
        input=text,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if result.returncode:
        raise RuntimeError("Pandoc conversion failed: " + result.stderr.strip()[:1000])
    return result.stdout.strip()


def _build_image_index(image_dir: Path | None) -> dict[str, Path]:
    """Index image assets by normalized basename."""
    if image_dir is None or not image_dir.is_dir():
        return {}

    index: dict[str, Path] = {}
    for path in sorted(image_dir.rglob("*")):
        if path.is_file() and not path.name.endswith(".desc"):
            index.setdefault(normalize_image_name(path.name), path)
    return index


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

    output_name = _portable_asset_name(source.name)
    key = unicodedata.normalize("NFC", output_name).casefold()
    previous = used_names.get(key)
    if previous is not None and previous != source:
        relative_source = str(source.relative_to(image_dir))
        digest = hashlib.sha256(relative_source.encode()).hexdigest()[:8]
        output_name = _portable_asset_name(f"{source.stem}-{digest}{source.suffix}")
        key = unicodedata.normalize("NFC", output_name).casefold()
        suffix = 2
        while key in used_names and used_names[key] != source:
            candidate = f"{source.stem}-{digest}-{suffix}{source.suffix}"
            output_name = _portable_asset_name(candidate)
            key = unicodedata.normalize("NFC", output_name).casefold()
            suffix += 1

    asset_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, asset_dir / output_name)
    copied[source] = output_name
    used_names[key] = source
    return output_name


def _restore_generated_tables(root: Any) -> None:
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
        if not caption.text.strip() and len(caption) == 0:
            table.remove(caption)
        if table_class != "infobox":
            continue

        for row in table.iter("tr"):
            cells = [child for child in row if child.tag == "td"]
            if len(cells) == 2:
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


def _restore_thumbnail_captions(root: Any) -> None:
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
                figcaption = etree.Element("figcaption")
                figcaption.text = caption
                parent.append(figcaption)
            continue

        wrapper = etree.Element("span")
        wrapper.set("class", "thumb")
        old_tail = image.tail
        image.addprevious(wrapper)
        wrapper.append(image)
        if caption:
            caption_node = etree.Element("span")
            caption_node.set("class", "thumbcaption")
            caption_node.text = caption
            wrapper.append(caption_node)
        image.tail = None
        wrapper.tail = old_tail


def _add_mediawiki_heading_ids(root: Any) -> None:
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
        headings.append((int(heading.tag[1]), label, identifier))

    if len(headings) < 4:
        return

    nav = etree.Element("nav")
    nav.set("class", "toc")
    heading = etree.SubElement(nav, "strong")
    heading.text = "Contents"
    items = etree.SubElement(nav, "ul")
    for level, label, identifier in headings:
        item = etree.SubElement(items, "li")
        item.set("class", f"lvl{level}")
        link = etree.SubElement(item, "a")
        link.set("href", "#" + _safe_destination(identifier))
        link.text = label
    root.insert(0, nav)


def _add_footnote_heading(root: Any) -> None:
    """Add a Notes heading when Pandoc emits a footnotes section without one."""
    for section in root.iter("section", "aside"):
        if "footnotes" not in (section.get("class") or ""):
            continue
        previous = section.getprevious()
        if previous is None or previous.tag not in {"h1", "h2", "h3", "h4"}:
            heading = etree.Element("h2")
            heading.text = "Notes"
            section.insert(0, heading)


def _rewrite_image_nodes(
    root: Any,
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
        placeholder = etree.Element("span")
        placeholder.text = f"[Missing image: {source_name}]"
        parent = image.getparent()
        if parent is not None:
            placeholder.tail = (" " + caption if caption else "") + (image.tail or "")
            image.tail = None
            parent.replace(image, placeholder)

    _restore_thumbnail_captions(root)


def _rewrite_anchor_nodes(
    root: Any,
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

        raw = urllib.parse.unquote(href).lstrip(":")
        prefix, separator, _ = raw.partition(":")
        if separator and prefix.strip().casefold() in {"file", "image", "media"}:
            output_name = _copy_image(
                raw,
                image_index,
                image_dir,
                asset_dir,
                copied_images,
                used_asset_names,
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


def _yaml_string(value: str) -> str:
    """Serialize a string as a valid YAML double-quoted scalar."""
    return json.dumps(value, ensure_ascii=False)


def _page_markdown(
    title: str,
    categories: list[str],
    body: str,
    source_url: str | None,
) -> str:
    """Compose front matter, article heading, and converted body."""
    metadata = [
        "---",
        f"title: {_yaml_string(title)}",
        "wiki_namespace: 0",
    ]
    if categories:
        metadata.append("categories: " + json.dumps(categories, ensure_ascii=False))
    if source_url:
        article_url = (
            source_url.rstrip("/")
            + "/wiki/"
            + urllib.parse.quote(title.replace(" ", "_"), safe="()'!~-._")
        )
        metadata.append(f"source: {_yaml_string(article_url)}")
    metadata.extend(["---", "", f"# {_markdown_label(title)}", ""])
    if body:
        metadata.extend([body, ""])
    return "\n".join(metadata)


MARKDOWN_SPECIAL_CHARS = frozenset("\\`*_{}[]()#+-.!|>")


def _markdown_label(title: str) -> str:
    """Escape Markdown punctuation in a wiki title used as text or a label."""
    return "".join(
        "\\" + character if character in MARKDOWN_SPECIAL_CHARS else character
        for character in title
    )


def _export_one_page(
    page: DumpPage,
    title_to_filename: dict[str, str],
    context: Any,
    preprocess: Any,
    postprocess: Any,
    infobox_defs: Any,
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
) -> tuple[PageExportResult, int, int]:
    """Expand and convert one article into its allocated Markdown file."""
    title_key = normalize_title(page.title)
    filename = title_to_filename[title_key]
    expansion_error = ""
    expanded = page.text
    stats: dict[str, Any] = {
        "infoboxes": 0,
        "missing_templates": [],
        "infobox_left": 0,
        "missing_images": [],
        "redlinks": 0,
        "unrendered": 0,
    }
    categories: list[str] = []
    expansion_errors = 0

    if page.model == "wikitext":
        try:
            context.start_page(page.title)
            expanded = preprocess(
                page.text, context, infobox_defs, page.title, stats
            )
        except Exception as exc:
            expansion_errors = 1
            expansion_error = str(exc)[:300]
        else:
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    expanded = context.expand(expanded, timeout=30)
            except Exception as exc:
                expansion_errors = 1
                expansion_error = str(exc)[:300]

    conversion_errors = 0
    try:
        expanded = postprocess(expanded, categories, stats)
        categories = sorted(set(categories), key=str.casefold)
        categories_seen.update(categories)
        html_fragment = _run_pandoc(pandoc, expanded, "mediawiki", "html")
        rewritten_html = _rewrite_html_links(
            html_fragment,
            page.title,
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
        markdown_body = _run_pandoc(pandoc, rewritten_html, "html", "gfm")
        status = "ok" if not expansion_error else "expand_failed"
    except Exception as exc:
        conversion_errors = 1
        markdown_body = "\n".join("> " + line for line in page.text.splitlines())
        status = "conversion_failed"
        expansion_error = str(exc)[:300]

    document = _page_markdown(page.title, categories, markdown_body, source_url)
    (pages_dir / filename).write_text(document, encoding="utf-8")
    result: PageExportResult = {
        "title": page.title,
        "filename": filename,
        "status": status,
        "categories": len(categories),
        "infoboxes": stats["infoboxes"],
        "infoboxes_unconverted": stats["infobox_left"],
        "missing_templates": sorted(
            set(stats["missing_templates"]), key=str.casefold
        ),
        "error": expansion_error,
    }
    return result, expansion_errors, conversion_errors


def _write_workspace_index(
    page_results: list[PageExportResult], output_dir: Path
) -> None:
    """Write the Zettlr workspace root index."""
    index_lines = [
        "# The Long Dark Wiki",
        "",
        f"Articles: {len(page_results)}",
        "",
    ]
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
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _load_renderer_pipeline(
    dump_path: Path,
) -> tuple[Any, list[DumpPage], dict[str, str], Any, Any, Any, Any, int]:
    """Load the existing Fandom renderer helpers and the expanded wiki context."""
    renderer = importlib.import_module("pipeline.wiki_to_html")
    preprocess = getattr(renderer, "prepass")
    postprocess = getattr(renderer, "postexpand")
    load_infobox_defs = getattr(renderer, "load_infobox_defs")
    wtp_factory = getattr(renderer, "Wtp")
    pandoc_selftest = getattr(renderer, "pandoc_selftest")
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
    dump_path: Path,
    mapping_path: Path,
    output_dir: Path,
    image_dir: Path | None,
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
        raise ValueError(
            "Filename mapping does not match this dump. "
            f"Missing mappings: {len(missing_mappings)}; "
            f"stale mappings: {len(extra_mappings)}."
        )

    pages_dir = output_dir / "pages"
    asset_dir = output_dir / "assets"
    pages_dir.mkdir(parents=True, exist_ok=True)
    pandoc = _pandoc_path()
    pandoc_selftest(pandoc)

    return {
        "title_to_filename": title_to_filename,
        "context": context,
        "articles": articles,
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

    for page in sorted(
        state["articles"], key=lambda item: item.title.casefold()
    ):
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
