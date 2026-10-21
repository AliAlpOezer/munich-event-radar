"""The candidate and key of ADR 0010's relevance question, which judgments replaced
(ADR 0018 D7). Kept so `event-radar calibrate` can read the `triage-v2` answers it measures
decision models against (ADR 0017)."""

from selectolax.lexbor import LexborHTMLParser

from .. import html
from ..model import Kind, json_key
from ..parse import parse

VERSION = "triage-v2"
# Equal for candidates with the same JSON, wherever they came from.
key = json_key
_FIELDS = ("title", "starts_at", "ends_at", "venue", "address", "city", "organiser", "description")
_TEXT = 600


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
