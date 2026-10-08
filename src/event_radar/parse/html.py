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


def image_url(value: object, base: str) -> str | None:
    """An https image URL from a schema.org image (text, list or ImageObject), else None."""
    if isinstance(value, list):
        value = value[0] if value else None
    if isinstance(value, dict):
        value = value.get("url") or value.get("contentUrl")
    if not isinstance(value, str) or not value.strip():
        return None
    url = urljoin(base, value.strip())
    return url if url.startswith("https://") and len(url) <= 2048 else None


def og_image(tree: LexborHTMLParser, base: str) -> str | None:
    for selector in ('meta[property="og:image"]', 'meta[name="twitter:image"]'):
        node = tree.css_first(selector)
        if node and (url := image_url(node.attributes.get("content"), base)):
            return url
    return None


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
    cleaned = _LINES.sub(
        "\n\n", "\n".join(_SPACE.sub(" ", line).strip() for line in raw.splitlines())
    )
    return cleaned.strip()[:limit]
