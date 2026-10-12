from .model import Kind, ParseResult, SourceClass
from .parse import parse
from .store import Store

LLM_PRIORITY = {Kind.EVENT_PAGE: 0, Kind.LISTING: 2, Kind.FEED: 3}


def ingest(
    store: Store,
    url: str,
    content_hash: str,
    content_type: str | None,
    body: bytes,
    kind: Kind,
    source_class: SourceClass,
    base: str | None = None,
) -> ParseResult:
    """Parse a fetched page into observations; queue it for the LLM where parsing fell short."""
    result = parse(base or url, content_type, body, kind)
    store.observations.add(content_hash, url, source_class, result.method, result.events)
    if result.needs_llm:
        store.extraction.enqueue(content_hash, url, LLM_PRIORITY[kind])
    return result
