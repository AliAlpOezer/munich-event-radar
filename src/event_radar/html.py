import re
from urllib.parse import urljoin

from selectolax.lexbor import LexborHTMLParser

_DROP = "script, style, noscript, svg, nav, footer, header, form, iframe"
_SPACE = re.compile(r"[ \t ]+")
_LINES = re.compile(r"\n\s*\n+")
_NOT_PAGES = ("#", "mailto:", "tel:", "javascript:")


def absolute(base: str, href: str) -> str | None:
    """`href` resolved against `base`; None when it is no URL (`http://[broken`)."""
    try:
        return urljoin(base, href)
    except ValueError:
        return None


def anchors(tree: LexborHTMLParser, base: str) -> list[tuple[str, str]]:
    """Each distinct web link on the page, absolute and without its fragment, with the
    text of its first anchor. Calendar links (`webcal://`) count as https."""
    found = {}
    for anchor in tree.css("a[href]"):
        href = (anchor.attributes.get("href") or "").strip()
        if not href or href.startswith(_NOT_PAGES):
            continue
        url = absolute(base, href.replace("webcal://", "https://").split("#")[0])
        if url and url.startswith("http"):
            found.setdefault(url, anchor.text(strip=True))
    return list(found.items())


def links(tree: LexborHTMLParser, base: str) -> list[str]:
    return [url for url, _ in anchors(tree, base)]


def image_url(value: object, base: str) -> str | None:
    """An https image URL from a schema.org image (text, list or ImageObject), else None."""
    if isinstance(value, list):
        value = value[0] if value else None
    if isinstance(value, dict):
        value = value.get("url") or value.get("contentUrl")
    if not isinstance(value, str) or not value.strip():
        return None
    url = absolute(base, value.strip())
    return url if url and url.startswith("https://") and len(url) <= 2048 else None


def og_image(tree: LexborHTMLParser, base: str) -> str | None:
    for selector in ('meta[property="og:image"]', 'meta[name="twitter:image"]'):
        node = tree.css_first(selector)
        if node and (url := image_url(node.attributes.get("content"), base)):
            return url
    return None


def text(body: bytes, base: str, limit: int = 12000, links: bool = True) -> str:
    """The page's visible text; with `links`, each link followed by its address."""
    tree = LexborHTMLParser(body)
    for node in tree.css(_DROP):
        node.decompose()
    for anchor in tree.css("a[href]") if links else ():
        href = anchor.attributes.get("href") or ""
        if href.startswith(("http", "/")) and (url := absolute(base, href)):
            anchor.replace_with(f"{anchor.text(strip=True)} <{url}>")
    root = tree.body or tree.root
    raw = root.text(separator="\n") if root else ""
    cleaned = _LINES.sub(
        "\n\n", "\n".join(_SPACE.sub(" ", line).strip() for line in raw.splitlines())
    )
    return cleaned.strip()[:limit]
