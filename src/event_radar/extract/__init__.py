from dataclasses import replace
from datetime import date

from selectolax.lexbor import LexborHTMLParser

from ..model import Evidence, Kind, ParsedEvent, SourceClass
from ..parse import html
from ..platforms import is_ticket_host, platform_key
from ..store import Store
from .router import Router
from .schema import SCHEMA, SYSTEM, VERSION
from .verify import verify


class Extractor:
    def __init__(self, router: Router, cache: Store):
        self.router = router
        self.cache = cache
        self.rejected: dict[str, list[str]] = {}

    def __call__(
        self, store: Store, url: str, digest: str, body: bytes, source_class: SourceClass
    ) -> int:
        text = html.text(body, url)
        cached = self.cache.cached_extraction(digest, VERSION)
        if cached is None:
            messages = [
                {"role": "system", "content": SYSTEM.replace("TODAY", date.today().isoformat())},
                {"role": "user", "content": f"Page URL: {url}\n\nPage text:\n{text}"},
            ]
            model, result = self.router.complete(messages, SCHEMA)
            cached = {"model": model, "result": result}
            self.cache.cache_extraction(digest, VERSION, model, cached)
        events = []
        for raw in cached["result"].get("events") or []:
            event, rejected = verify(raw, text)
            self.rejected[url] = rejected
            if "title" in event.fields:
                events.append(_with_keys(url, event))
        source = store.source(url)
        if len(events) == 1 and source and source["kind"] == Kind.EVENT_PAGE:
            events[0] = _with_image(events[0], body, url)
        method = f"llm:{cached['model']}@{VERSION}"
        return store.add_observations(digest, url, source_class, method, events)


def _with_image(event: ParsedEvent, body: bytes, url: str) -> ParsedEvent:
    image = html.og_image(LexborHTMLParser(body), url)
    return replace(event, fields={**event.fields, "image": Evidence(image)}) if image else event


def _with_keys(url: str, event: ParsedEvent) -> ParsedEvent:
    fields = dict(event.fields)
    fields.setdefault("event_url", Evidence(url))
    keys = {k for k in (platform_key(url),) if k}
    reg_url = fields.get("reg_url")
    if reg_url and (key := platform_key(str(reg_url.value))) and not is_ticket_host(url):
        keys.add(key)
    return replace(event, fields=fields, platform_keys=tuple(sorted(keys)))
