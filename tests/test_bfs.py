from typing import TYPE_CHECKING

from pipeline.bfs import bfs, render_json, render_text
from pipeline.links import LinkExtractor
from pipeline.wiki import Wiki, load_wiki

if TYPE_CHECKING:
    from pathlib import Path

HEAD = """<mediawiki xmlns="http://www.mediawiki.org/xml/export-0.11/">
<siteinfo><namespaces>
<namespace key="0" case="first-letter" />
<namespace key="6" case="first-letter">File</namespace>
<namespace key="10" case="first-letter">Template</namespace>
<namespace key="14" case="first-letter">Category</namespace>
</namespaces></siteinfo>
"""

HOME_LINKS = "[[Alpha]] [[beta_page|Beta]] {{Nav}} [[Category:Things]]"
HOME_MEDIA = "[[File:x.png|link=Gamma]] [[Missing]] [[Ru:Foo]]"
HOME = f"{HOME_LINKS} {HOME_MEDIA}"

PAGES = [
    ("Home", 0, HOME, None),
    ("Alpha", 0, "[[Delta#Part]] [[Home]]", None),
    ("Beta page", 0, "[[Delta]] [[Old name]]", None),
    ("Gamma", 0, "leaf", None),
    ("Delta", 0, "leaf", None),
    ("Epsilon", 0, "orphan", None),
    ("Old name", 0, "#REDIRECT [[Zeta]]", "Zeta"),
    ("Zeta", 0, "leaf", None),
    ("Template:Nav", 10, "[[Zeta]]<noinclude>[[Epsilon]]</noinclude>", None),
]


def _dump() -> str:
    parts = [HEAD]
    for title, ns, text, redirect in PAGES:
        tag = f'<redirect title="{redirect}" />' if redirect else ""
        body = f"<ns>{ns}</ns>{tag}<revision><text>{text}</text></revision>"
        parts.append(f"<page><title>{title}</title>{body}</page>\n")
    parts.append("</mediawiki>")
    return "".join(parts)


def _wiki(tmp_path: Path) -> Wiki:
    path = tmp_path / "dump.xml"
    path.write_text(_dump(), encoding="utf-8")
    return load_wiki(path)


def test_normalize_and_resolve(tmp_path: Path) -> None:
    wiki = _wiki(tmp_path)
    assert wiki.normalize("beta_page#x") == "Beta page"
    assert wiki.normalize(":Category:foo") == "Category:Foo"
    assert wiki.lookup("old name") == "Zeta"
    assert wiki.lookup("Nope") is None


def test_links_expand_templates_and_skip_tags(tmp_path: Path) -> None:
    wiki = _wiki(tmp_path)
    extractor = LinkExtractor(wiki)
    links = extractor.links("Home")
    # File link= and template body count; noinclude, category and file do not.
    assert set(links) == {"Alpha", "Beta page", "Gamma", "Zeta"}
    assert extractor.dangling == {"Missing"}


def test_bfs_tree_shape(tmp_path: Path) -> None:
    wiki = _wiki(tmp_path)
    extractor = LinkExtractor(wiki)
    tree = bfs(
        "Home", lambda t: [x for x in extractor.links(t) if wiki.pages[x].ns == 0]
    )
    assert tree["Delta"].parent == "Alpha"
    assert tree["Delta"].depth == 2
    assert "Epsilon" not in tree
    assert list(tree) == ["Home", "Alpha", "Beta page", "Gamma", "Zeta", "Delta"]
    text = render_text(tree, "Home")
    assert text.splitlines()[0] == "Home [5]"
    assert "│   └── Delta" in text
    assert render_json(tree, wiki.aliases())["descendants"] == 5
