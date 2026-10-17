import hashlib
from dataclasses import replace
from datetime import date

from selectolax.lexbor import LexborHTMLParser

from .. import html
from ..model import Evidence, ParsedEvent
from ..platforms import is_ticket_host, platform_key
from ..store import Store
from .router import Router
from .schema import SCHEMA, SYSTEM, VERSION
from .verify import verify


class Extractor:
    """Asks the LLM for the events on a page without structured data, keeping only the
    fields its quotes ground in the page (ADR 0002 D8). Answers are cached in `cache` by the
    hash of the question, the page's URL and text, so a page whose markup changes but whose
    text does not is never asked about again (ADR 0009)."""

    def __init__(self, router: Router, cache: Store):
        self.router = router
        self.cache = cache

    def extract(self, url: str, body: bytes, event_page: bool) -> tuple[str, list[ParsedEvent]]:
        """The observation method and the events found; an event page lends its image."""
        text = html.text(body, url)
        question = f"Page URL: {url}\n\nPage text:\n{text}"
        key = hashlib.sha256(question.encode()).hexdigest()
        cached = self.cache.extraction.cached(key, VERSION)
        if cached is None:
            messages = [
                {"role": "system", "content": SYSTEM.replace("TODAY", date.today().isoformat())},
                {"role": "user", "content": question},
            ]
            model, result = self.router.complete(messages, SCHEMA)
            cached = {"model": model, "result": result}
            self.cache.extraction.cache(key, VERSION, model, cached)
        events = []
        for raw in cached["result"].get("events") or []:
            event, _ = verify(raw, text)
            if "title" in event.fields:
                events.append(_with_keys(url, event))
        if len(events) == 1 and event_page:
            events[0] = _with_image(events[0], body, url)
        return f"llm:{cached['model']}@{VERSION}", events


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
