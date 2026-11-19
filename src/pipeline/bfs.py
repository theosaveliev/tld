from collections import Counter, deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

__all__ = ["Node", "bfs", "depth_histogram", "render_json", "render_text"]


@dataclass(slots=True)
class Node:
    title: str
    depth: int
    parent: str | None
    children: list[str] = field(default_factory=list)


def bfs(root: str, neighbors: Callable[[str], Iterable[str]]) -> dict[str, Node]:
    """Breadth-first traversal; returns the BFS tree keyed by title.

    A page's parent is the page that first discovered it, so every page sits at
    its shortest link distance from the root. Dict order is BFS order.
    """
    tree = {root: Node(root, 0, None)}
    queue = deque([root])
    while queue:
        node = tree[queue.popleft()]
        for target in neighbors(node.title):
            if target in tree:
                continue
            tree[target] = Node(target, node.depth + 1, node.title)
            node.children.append(target)
            queue.append(target)
    return tree


def descendants(tree: dict[str, Node]) -> dict[str, int]:
    """Count the descendants of every node."""
    sizes = dict.fromkeys(tree, 0)
    # Reversed BFS order visits every child before its parent.
    for title in reversed(tree):
        parent = tree[title].parent
        if parent is not None:
            sizes[parent] += sizes[title] + 1
    return sizes


def depth_histogram(tree: dict[str, Node]) -> dict[int, int]:
    counts = Counter(node.depth for node in tree.values())
    return dict(sorted(counts.items()))


def render_text(
    tree: dict[str, Node],
    root: str,
    max_depth: int | None = None,
    *,
    counts: bool = True,
) -> str:
    """Draw the tree with box characters, children in discovery order."""
    sizes = descendants(tree)
    lines: list[str] = []
    # Stack items: (title, prefix of the node's own line, prefix of its children).
    stack = [(root, "", "")]
    while stack:
        title, line_prefix, child_prefix = stack.pop()
        node = tree[title]
        label = f"{line_prefix}{title}"
        if counts and sizes[title]:
            label += f" [{sizes[title]}]"
        lines.append(label)
        if max_depth is not None and node.depth >= max_depth:
            continue
        last = len(node.children) - 1
        for i in range(last, -1, -1):
            branch, extension = ("└── ", "    ") if i == last else ("├── ", "│   ")
            stack.append(
                (node.children[i], child_prefix + branch, child_prefix + extension)
            )
    return "\n".join(lines)


def render_json(
    tree: dict[str, Node], aliases: dict[str, list[str]] | None = None
) -> dict[str, object]:
    """Nested dict form of the tree, built without recursion."""
    sizes = descendants(tree)
    built: dict[str, dict[str, object]] = {}
    for title in reversed(tree):
        node = tree[title]
        entry: dict[str, object] = {
            "title": title,
            "depth": node.depth,
            "descendants": sizes[title],
        }
        if aliases and title in aliases:
            entry["redirects"] = sorted(aliases[title])
        entry["children"] = [built.pop(child) for child in node.children]
        built[title] = entry
    return built[next(iter(tree))]
