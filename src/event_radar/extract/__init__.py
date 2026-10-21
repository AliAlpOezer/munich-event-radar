import hashlib
from collections.abc import Iterable
from dataclasses import replace
from datetime import UTC, date, datetime

from selectolax.lexbor import LexborHTMLParser

from .. import html
from ..model import Evidence, ParsedEvent
from ..platforms import is_ticket_host, platform_key
from ..store import Store
from . import judgments
from .router import Router, conforms
from .schema import SCHEMA, SYSTEM, VERSION
from .verify import verify


class Extractor:
    """Asks the LLM for the events on a page without structured data, keeping only the
    fields its quotes ground in the page (ADR 0002 D8). Answers are cached in `cache` by the
    hash of the question, the page's URL and text, so a page whose markup changes but whose
    text does not is never asked about again (ADR 0009), unless its cached answer is out of
    the schema's shape."""

    def __init__(self, router: Router, cache: Store):
        self.router = router
        self.cache = cache

    def extract(self, url: str, body: bytes, event_page: bool) -> tuple[str, list[ParsedEvent]]:
        """The observation method and the events found; an event page lends its image."""
        text = html.text(body, url)
        cached = self.cache.extraction.cached(key(url, text), VERSION)
        if cached is None or not conforms(cached["result"], SCHEMA):
            messages = [
                {"role": "system", "content": SYSTEM.replace("TODAY", date.today().isoformat())},
                {"role": "user", "content": _question(url, text)},
            ]
            model, result = self.router.complete(messages, SCHEMA)
            cached = {"model": model, "result": result}
            self.cache.extraction.cache(key(url, text), VERSION, model, cached)
        events = []
        for raw in cached["result"].get("events") or []:
            event, _ = verify(raw, text)
            if "title" in event.fields:
                events.append(_with_keys(url, event))
        if len(events) == 1 and event_page:
            events[0] = _with_image(events[0], body, url)
        return f"llm:{cached['model']}@{VERSION}", events

    def judge(
        self, keys: Iterable[str], first: str | None = None, until: datetime | None = None
    ) -> int:
        """Ask the judgments of `keys` that have no answer yet, in batches, provider `first`
        first, starting no batch after `until` (ADR 0018); returns how many were answered.
        A candidate left unanswered is asked again next time."""
        unanswered = self.cache.judgments.unanswered(keys, judgments.VERSION)
        batches = list(unanswered.items())
        answered = 0
        for start in range(0, len(batches), judgments.BATCH):
            if until and datetime.now(UTC) >= until:
                break
            batch = batches[start : start + judgments.BATCH]
            messages = [
                {"role": "system", "content": judgments.SYSTEM},
                {"role": "user", "content": judgments.question([c for _, c in batch])},
            ]
            model, result = self.router.complete(
                messages, judgments.SCHEMA, name="judgments", first=first
            )
            for i, answer in judgments.answers(result, len(batch)).items():
                self.cache.judgments.answer(batch[i][0], judgments.VERSION, model, answer)
                answered += 1
        return answered


def _question(url: str, text: str) -> str:
    return f"Page URL: {url}\n\nPage text:\n{text}"


def key(url: str, text: str) -> str:
    """The cache key of the extraction question about a page's text."""
    return hashlib.sha256(_question(url, text).encode()).hexdigest()


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
