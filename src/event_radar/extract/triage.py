"""Which pages are worth an extraction call (ADR 0010): a page's candidate, the key that
makes identical candidates one question, and the question that sorts them in batches."""

import hashlib
import json
from collections.abc import Iterable

from selectolax.lexbor import LexborHTMLParser

from .. import html
from ..model import Kind
from ..parse import parse

VERSION = "triage-v1"
BATCH = 20
_FIELDS = ("title", "starts_at", "ends_at", "venue", "address", "city", "organiser", "description")
_TEXT = 600

SCHEMA = {
    "type": "object",
    "properties": {
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"id": {"type": "integer"}, "relevant": {"type": "boolean"}},
                "required": ["id", "relevant"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["verdicts"],
    "additionalProperties": False,
}


def system(kinds: Iterable[str]) -> str:
    any_topic = ", ".join(k.replace("_", " ") + "s" for k in kinds)
    return (
        "You sort events for a radar of AI events in Munich. Each numbered line is one event"
        " as JSON, or a page's title and opening text. An event is relevant when it is about"
        " artificial intelligence, machine learning or data science, or when it is one of"
        f" these, whatever its topic: {any_topic}. Answer every id with"
        ' {"id": <number>, "relevant": true or false}; when unsure, answer true.'
    )


def candidate(url: str, body: bytes) -> tuple[dict, dict | None]:
    """What the sorting reads about a page: its one structured event, else its title and
    opening text; and that event's fields in the judge's form, None without one."""
    result = parse(url, None, body, Kind.EVENT_PAGE)
    if len(result.events) == 1 and "title" in result.events[0].fields:
        fields = result.events[0].fields
        shown = {k: fields[k].value for k in _FIELDS if k in fields and fields[k].value}
        if "description" in shown:
            shown["description"] = str(shown["description"])[:_TEXT]
        return shown, {k: {"value": v.value} for k, v in fields.items()}
    title = LexborHTMLParser(body).css_first("title")
    return {
        "page": title.text(strip=True) if title else url,
        "text": html.text(body, url)[:_TEXT],
    }, None


def key(candidate: dict) -> str:
    """Equal for candidates with the same JSON, wherever they came from."""
    text = json.dumps(candidate, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(text.encode()).hexdigest()


def question(candidates: list[dict]) -> str:
    return "\n".join(
        f"{i}. {json.dumps(c, ensure_ascii=False, default=str)}" for i, c in enumerate(candidates)
    )


def answers(result: dict, count: int) -> dict[int, bool]:
    """The verdicts the model gave for ids 0 to `count` - 1; others are left out."""
    found = {}
    for verdict in result.get("verdicts") or []:
        if not isinstance(verdict, dict):
            continue
        ident, relevant = verdict.get("id"), verdict.get("relevant")
        if isinstance(ident, int) and 0 <= ident < count and isinstance(relevant, bool):
            found[ident] = relevant
    return found
