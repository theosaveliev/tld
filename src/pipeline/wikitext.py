"""Small helpers for inspecting the mwparserfromhell syntax tree."""

from __future__ import annotations

from typing import TYPE_CHECKING

from mwparserfromhell.nodes import Argument, Template, Wikilink

if TYPE_CHECKING:
    from mwparserfromhell.wikicode import Wikicode


def is_nested_in_template_or_argument(
    code: Wikicode, node: Template | Wikilink
) -> bool:
    """Return whether *node* occurs inside a template or template argument.

    Recursive mwparserfromhell filters also return nodes inside template
    parameters. Those nodes are not necessarily emitted by the surrounding
    template, so callers that are identifying direct page/template content
    should not attribute them to that page/template.
    """
    return any(
        isinstance(ancestor, (Template, Argument))
        for ancestor in code.get_ancestors(node)
    )
