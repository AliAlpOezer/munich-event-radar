import re
from urllib.parse import urljoin

from selectolax.lexbor import LexborHTMLParser

_DROP = "script, style, noscript, svg, nav, footer, header, form, iframe"
_SPACE = re.compile(r"[ \t ]+")
_LINES = re.compile(r"\n\s*\n+")


def links(tree: LexborHTMLParser, base: str) -> list[str]:
    found = []
    for anchor in tree.css("a[href]"):
        href = anchor.attributes.get("href") or ""
        if href.startswith(("http", "/")):
            found.append(urljoin(base, href.split("#")[0]))
    return list(dict.fromkeys(found))


def text(body: bytes, base: str, limit: int = 12000) -> str:
    tree = LexborHTMLParser(body)
    for node in tree.css(_DROP):
        node.decompose()
    for anchor in tree.css("a[href]"):
        href = anchor.attributes.get("href") or ""
        if href.startswith(("http", "/")):
            label = anchor.text(strip=True)
            anchor.replace_with(f"{label} <{urljoin(base, href)}>")
    root = tree.body or tree.root
    raw = root.text(separator="\n") if root else ""
    cleaned = _LINES.sub("\n\n", "\n".join(_SPACE.sub(" ", line).strip() for line in raw.splitlines()))
    return cleaned.strip()[:limit]
