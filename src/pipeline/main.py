# pyright: reportAny=false
import json
from pathlib import Path
from typing import Literal

import click
import defusedxml.ElementTree as ET

from pipeline.bfs import bfs, depth_histogram, render_json, render_text
from pipeline.categories import build_category_report, write_category_report
from pipeline.click_types import IN_PATH, OUT_PATH
from pipeline.links import LinkExtractor
from pipeline.templates import (
    build_template_category_report,
    write_template_category_report,
)
from pipeline.filename_audit import audit_dump
from pipeline.wiki import load_wiki

__all__ = ["cli"]

CONTEXT_SETTINGS = {"help_option_names": ["-h", "--help"]}


@click.group(context_settings=CONTEXT_SETTINGS)
def cli() -> None:
    pass


def local(tag: str) -> str:
    return tag.removeprefix("{http://www.mediawiki.org/xml/export-0.11/}")


@cli.command()
@click.argument("wiki_dump", type=IN_PATH)
def read_dump(wiki_dump: Path) -> None:
    root = ET.fromstring(wiki_dump.read_text())
    tags: set[str] = set()

    for element in root.iter():
        tags.add(local(element.tag))

    print(tags)


@cli.command()
@click.argument("wiki_dump", type=IN_PATH)
@click.option("--root", default="The Long Dark Wiki", show_default=True)
@click.option(
    "--namespace",
    "namespaces",
    type=int,
    multiple=True,
    default=(0,),
    show_default=True,
    help="Namespace keys to traverse; repeat for several.",
)
@click.option("--max-depth", type=click.IntRange(min=0), help="Truncate the output.")
@click.option("--format", "fmt", type=click.Choice(["text", "json"]), default="text")
@click.option("--counts/--no-counts", default=True, help="Show descendant counts.")
@click.option("-o", "--output", type=OUT_PATH, help="Write here instead of stdout.")
def bfs_tree(
    wiki_dump: Path,
    root: str,
    namespaces: tuple[int, ...],
    max_depth: int | None,
    fmt: Literal["text", "json"],
    output: Path | None,
    *,
    counts: bool,
) -> None:
    """Breadth-first traversal of the wiki's link graph, printed as a tree."""
    wiki = load_wiki(wiki_dump)
    start = wiki.lookup(root)
    if start is None:
        msg = f"root page {root!r} not found in the dump"
        raise click.ClickException(msg)

    allowed = set(namespaces)
    extractor = LinkExtractor(wiki)

    def neighbors(title: str) -> list[str]:
        return [t for t in extractor.links(title) if wiki.pages[t].ns in allowed]

    tree = bfs(start, neighbors)
    unreachable = sorted(
        p.title
        for p in wiki.pages.values()
        if p.ns in allowed and p.redirect is None and p.title not in tree
    )

    if fmt == "json":
        document = render_json(tree, wiki.aliases())
        document["unreachable"] = unreachable
        rendered = json.dumps(document, ensure_ascii=False, indent=2)
    else:
        rendered = render_text(tree, start, max_depth, counts=counts)
        if unreachable:
            rendered += f"\n\nNot reachable from {start} ({len(unreachable)}):\n"
            rendered += "\n".join(f"- {title}" for title in unreachable)

    if output is None:
        click.echo(rendered)
    else:
        output.write_text(rendered + "\n", encoding="utf-8")

    click.echo(f"reachable: {len(tree)}  unreachable: {len(unreachable)}", err=True)
    click.echo(f"red links: {len(extractor.dangling)}", err=True)
    for depth, count in depth_histogram(tree).items():
        click.echo(f"  depth {depth}: {count}", err=True)


@cli.command()
@click.argument("wiki_dump", type=IN_PATH)
@click.option(
    "--output-dir",
    type=click.Path(path_type=Path, file_okay=False),
    default=Path("reports/categories"),
    show_default=True,
)
def categories(wiki_dump: Path, output_dir: Path) -> None:
    """Extract explicit category assignments from the wiki dump."""
    click.echo("Loading wiki...", err=True)
    wiki = load_wiki(wiki_dump)

    click.echo(f"Loaded {len(wiki.pages)} pages.", err=True)
    click.echo("Extracting categories...", err=True)

    report = build_category_report(wiki)
    write_category_report(report, output_dir)

    for name, value in report["summary"].items():
        click.echo(f"{name}: {value}", err=True)

    click.echo(f"Reports written to {output_dir}", err=True)


@cli.command("template-categories")
@click.argument("wiki_dump", type=IN_PATH)
@click.option(
    "--output-dir",
    type=click.Path(path_type=Path, file_okay=False),
    default=Path("reports/categories/templates"),
    show_default=True,
)
def template_categories(wiki_dump: Path, output_dir: Path) -> None:
    """Find potential categories introduced through template transclusion."""
    click.echo("Loading wiki...", err=True)
    wiki = load_wiki(wiki_dump)

    click.echo(f"Loaded {len(wiki.pages)} pages.", err=True)
    click.echo("Analyzing template transclusions...", err=True)

    report = build_template_category_report(wiki)
    write_template_category_report(report, output_dir)

    for name, value in report["summary"].items():
        click.echo(f"{name}: {value}", err=True)

    click.echo(f"Reports written to {output_dir}", err=True)

@cli.command("audit-filenames")
@click.argument("wiki_dump", type=IN_PATH)
@click.option(
    "--include-redirects/--exclude-redirects",
    default=False,
    show_default=True,
    help="Include redirect pages as separate Markdown files.",
)
@click.option(
    "--output",
    "output_path",
    type=click.Path(file_okay=True, dir_okay=False, path_type=Path),
    help="Write the full title-to-filename mapping and issues as JSON.",
)
def audit_filenames(
    wiki_dump: Path, include_redirects: bool, output_path: Path | None
) -> None:
    """Audit wiki page titles for portable Markdown filenames."""
    report = audit_dump(wiki_dump, include_redirects=include_redirects)

    click.echo(f"Article pages: {report['article_pages']}")
    click.echo(f"Unique titles: {report['unique_titles']}")
    click.echo(f"Redirects seen: {report['redirects_seen']}")
    click.echo(f"Redirects excluded: {report['redirects_excluded']}")
    click.echo(f"Issues found: {len(report['issues'])}")

    for issue in report["issues"]:
        click.echo(f"\n[{issue['kind']}] {', '.join(issue['titles'])}")
        click.echo(f"  {issue['detail']}")
        click.echo(f"  Filenames: {', '.join(issue['filenames'])}")

    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        click.echo(f"JSON report written to {output_path}")

    if not report["issues"]:
        click.echo("No filename issues found.")
