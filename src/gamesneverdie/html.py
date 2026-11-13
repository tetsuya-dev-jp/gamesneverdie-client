"""Small HTML tree for the site's server-rendered pages; no JavaScript execution."""

from collections.abc import Iterator
from dataclasses import dataclass, field
from html.parser import HTMLParser


@dataclass
class Node:
    tag: str = ""
    attrs: dict[str, str | None] = field(default_factory=dict)
    children: list["Node | str"] = field(default_factory=list)

    def find(self, tag: str = "", *, cls: str = "", **attrs: str) -> Iterator["Node"]:
        for child in self.children:
            if isinstance(child, Node):
                if (
                    (not tag or child.tag == tag)
                    and (not cls or cls in (child.attrs.get("class") or "").split())
                    and all(child.attrs.get(k) == v for k, v in attrs.items())
                ):
                    yield child
                yield from child.find(tag, cls=cls, **attrs)

    def first(self, tag: str = "", *, cls: str = "", **attrs: str) -> "Node":
        return next(self.find(tag, cls=cls, **attrs), Node())

    def text(self) -> str:
        if self.tag in {"script", "style", "svg"} or "hidden" in self.attrs:
            return ""
        return " ".join(
            "".join(
                c.text() if isinstance(c, Node) else c for c in self.children
            ).split()
        )


class _Parser(HTMLParser):
    VOID = frozenset(
        (
            "area",
            "base",
            "br",
            "col",
            "embed",
            "hr",
            "img",
            "input",
            "link",
            "meta",
            "param",
            "source",
            "track",
            "wbr",
        )
    )

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node()
        self.stack = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = Node(tag, dict(attrs))
        self.stack[-1].children.append(node)
        if tag not in self.VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                break

    def handle_data(self, data: str) -> None:
        if self.stack[-1].tag not in {"script", "style"}:
            self.stack[-1].children.append(data)


def parse_html(html: str) -> Node:
    parser = _Parser()
    parser.feed(html)
    return parser.root
