# pyright: reportAny=false
"""Convert a MediaWiki XML dump into a linked Markdown workspace."""

from __future__ import annotations

import hashlib
import html
import importlib
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import types
import unicodedata
import urllib.parse
import warnings
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
WTP_RE_SHIM_MIN_PYTHON_VERSION = (3, 11)


class ExportSummary(TypedDict):
    article_pages: int
    exported_pages: int
    redirects: int
    expansion_errors: int
    conversion_errors: int
    unresolved_links: int
    missing_images: int
    categories: int


class PageExportResult(TypedDict):
    title: str
    filename: str
    status: str
    categories: int
    infoboxes: int
    missing_templates: int
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
    if parsed.scheme in {"http", "https", "mailto", "ftp", "tel"}:
        return href

    raw_path = parsed.path if not parsed.scheme else href
    raw_target = urllib.parse.unquote(raw_path).strip()
    raw_target = raw_target.removeprefix("./").removeprefix("/")
    if not raw_target:
        return "#" + _safe_destination(parsed.fragment) if parsed.fragment else None

    key = normalize_title(raw_target.lstrip(":"))
    current_key = normalize_title(current_title)
    fragment = parsed.fragment
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
        if event != "end" or element.tag.rsplit("}", 1)[-1] != "page":
            continue

        title = ""
        namespace = 0
        model = "wikitext"
        text = ""
        redirect: str | None = None

        for child in element:
            tag = child.tag.rsplit("}", 1)[-1]
            if tag == "title":
                title = child.text or ""
            elif tag == "ns":
                namespace = int(child.text or "0")
            elif tag == "redirect":
                redirect = child.get("title")
            elif tag == "revision":
                for revision_child in child:
                    revision_tag = revision_child.tag.rsplit("}", 1)[-1]
                    if revision_tag == "text":
                        text = revision_child.text or ""
                    elif revision_tag == "model":
                        model = revision_child.text or model

        if not redirect:
            match = REDIRECT_RE.match(text)
            if match:
                redirect = match.group(1).strip()

        root.clear()
        if title:
            yield DumpPage(title, namespace, model, text, redirect)


def _create_wtp() -> Any:
    """Create Wtp with compatibility shims required by current Python."""
    warnings.filterwarnings(
        "ignore", message=".*global interpreter lock.*", category=RuntimeWarning
    )
    warnings.filterwarnings("ignore", category=SyntaxWarning)

    try:
        importlib.import_module("pkg_resources")
    except ImportError:
        package_resources = types.ModuleType("pkg_resources")

        def resource_filename(package: str, resource: str) -> str:
            spec = importlib.util.find_spec(package)
            if spec is None or spec.origin is None:
                raise ModuleNotFoundError(package)
            return str(Path(spec.origin).parent / resource)

        setattr(package_resources, "resource_filename", resource_filename)
        sys.modules["pkg_resources"] = package_resources

    regex_module = re
    luaexec = importlib.import_module("wikitextprocessor.luaexec")

    class RegexShim:
        def __getattr__(self, name: str) -> Any:
            return getattr(regex_module, name)

        def match(self, pattern: Any, string: str, flags: int = 0) -> Any:
            if isinstance(pattern, str):
                pattern = pattern.replace("^(?s)", "(?s)^")
            return regex_module.match(pattern, string, flags)

    if sys.version_info >= WTP_RE_SHIM_MIN_PYTHON_VERSION:
        setattr(luaexec, "re", RegexShim())

    processor = importlib.import_module("wikitextprocessor")
    wtp_factory = getattr(processor, "Wtp")
    return wtp_factory(quiet=True, num_threads=1)


def _load_wiki_context(
    dump_path: Path,
) -> tuple[Any, list[DumpPage], dict[str, str]]:
    """Load templates/modules and retain main-namespace article records."""
    context = _create_wtp()
    articles: list[DumpPage] = []
    redirects: dict[str, str] = {}

    for page in _iter_dump_pages(dump_path):
        if page.redirect:
            context.add_page("redirect", page.title, page.redirect)
            redirects[normalize_title(page.title)] = page.redirect
        else:
            context.add_page(page.model, page.title, page.text)
            if page.namespace == 0:
                articles.append(page)

    context.analyze_templates()
    return context, articles, redirects


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

    output_name = source.name
    key = output_name.casefold()
    previous = used_names.get(key)
    if previous is not None and previous != source:
        relative_source = str(source.relative_to(image_dir))
        digest = hashlib.sha256(relative_source.encode()).hexdigest()[:8]
        output_name = f"{source.stem}-{digest}{source.suffix}"
        key = output_name.casefold()
        suffix = 2
        while key in used_names and used_names[key] != source:
            output_name = f"{source.stem}-{digest}-{suffix}{source.suffix}"
            key = output_name.casefold()
            suffix += 1

    asset_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, asset_dir / output_name)
    copied[source] = output_name
    used_names[key] = source
    return output_name


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
        placeholder = etree.Element("span")
        placeholder.text = f"[Missing image: {source_name}]"
        parent = image.getparent()
        if parent is not None:
            parent.replace(image, placeholder)


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
        if separator and prefix.casefold() in {"file", "image", "media"}:
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


def _markdown_label(title: str) -> str:
    """Escape a wiki title for use as a Markdown link label."""
    return (
        title.replace("\\", "\\\\")
        .replace("[", "\\[")
        .replace("]", "\\]")
        .replace("|", "\\|")
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
    }
    categories: list[str] = []
    expansion_errors = 0

    if page.model == "wikitext":
        try:
            expanded = preprocess(
                page.text, context, infobox_defs, page.title, stats
            )
            context.start_page(page.title)
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
        "missing_templates": len(set(stats["missing_templates"])),
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


def export_dump_to_markdown(
    dump_path: Path,
    mapping_path: Path,
    output_dir: Path,
    *,
    image_dir: Path | None = None,
    source_url: str | None = "https://thelongdark.fandom.com",
) -> ExportReport:
    """Convert a dump into linked Markdown files using the audited filename map."""
    title_to_filename = load_filename_mapping(mapping_path)
    renderer = importlib.import_module("pipeline.wiki_to_html")
    load_infobox_defs = getattr(renderer, "load_infobox_defs")
    preprocess = getattr(renderer, "prepass")
    postprocess = getattr(renderer, "postexpand")
    pandoc_selftest = getattr(renderer, "pandoc_selftest")
    context, articles, redirects = _load_wiki_context(dump_path)
    infobox_defs = load_infobox_defs(context)

    article_titles = {normalize_title(page.title) for page in articles}
    missing_mappings = sorted(article_titles - title_to_filename.keys())
    extra_mappings = sorted(title_to_filename.keys() - article_titles)
    if missing_mappings or extra_mappings:
        raise ValueError(
            "Filename mapping does not match this dump. "
            f"Missing mappings: {len(missing_mappings)}; "
            f"stale mappings: {len(extra_mappings)}."
        )

    pages_dir = output_dir / "pages"
    asset_dir = output_dir / "assets"
    pages_dir.mkdir(parents=True, exist_ok=True)
    image_index = _build_image_index(image_dir)
    pandoc = _pandoc_path()
    pandoc_selftest(pandoc)

    copied_images: dict[Path, str] = {}
    used_asset_names: dict[str, Path] = {}
    missing_links: set[str] = set()
    missing_images: set[str] = set()
    page_results: list[PageExportResult] = []
    categories_seen: set[str] = set()
    expansion_errors = 0
    conversion_errors = 0

    for page in sorted(articles, key=lambda item: item.title.casefold()):
        result, page_expansion_errors, page_conversion_errors = _export_one_page(
            page,
            title_to_filename,
            context,
            preprocess,
            postprocess,
            infobox_defs,
            pandoc,
            redirects,
            image_index,
            image_dir,
            pages_dir,
            asset_dir,
            copied_images,
            used_asset_names,
            missing_links,
            missing_images,
            categories_seen,
            source_url,
        )
        page_results.append(result)
        expansion_errors += page_expansion_errors
        conversion_errors += page_conversion_errors

    _write_workspace_index(page_results, output_dir)
    summary: ExportSummary = {
        "article_pages": len(articles),
        "exported_pages": len(page_results),
        "redirects": len(redirects),
        "expansion_errors": expansion_errors,
        "conversion_errors": conversion_errors,
        "unresolved_links": len(missing_links),
        "missing_images": len(missing_images),
        "categories": len(categories_seen),
    }
    report: ExportReport = {
        "summary": summary,
        "pages": page_results,
        "unresolved_links": sorted(missing_links),
        "missing_images": sorted(missing_images),
    }
    _write_conversion_report(report, output_dir)
    return report
