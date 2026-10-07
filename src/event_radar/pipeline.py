from .model import Kind, ParseResult, SourceClass
from .parse import parse
from .store import Store

LLM_PRIORITY = {Kind.EVENT_PAGE: 0, Kind.LISTING: 2, Kind.FEED: 3}


def ingest(
    store: Store, url: str, content_hash: str, content_type: str | None, body: bytes,
    kind: Kind, source_class: SourceClass,
) -> ParseResult:
    result = parse(url, content_type, body, kind)
    store.add_observations(content_hash, url, source_class, result.method, result.events)
    if result.needs_llm:
        store.enqueue_extraction(content_hash, url, LLM_PRIORITY[kind])
    return result
