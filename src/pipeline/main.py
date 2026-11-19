# pyright: reportAny=false
from typing import TYPE_CHECKING

import click
import defusedxml.ElementTree as ET

from pipeline.click_types import IN_PATH

if TYPE_CHECKING:
    from pathlib import Path

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
