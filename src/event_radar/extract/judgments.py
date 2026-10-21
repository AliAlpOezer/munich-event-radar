"""What an event is about (ADR 0018): the candidate a judgment reads, the key that makes an
unchanged candidate one question, and the batched question itself. A model states topics,
a kind and an audience; the judge decides the board."""

import json
from collections.abc import Sequence

from selectolax.lexbor import LexborHTMLParser

from .. import html
from ..model import Evidence, ParsedEvent, json_key
from .schema import ENUMS

VERSION = "judgments-v3"
BATCH = 10
TEXT = 2000
AI = ("core", "some", "none", "unknown")
TOPICS = ("startup", "career", "tech")
_FIELDS = ("title", "description", "organiser", "venue")
# Equal for candidates with the same JSON, wherever they came from.
key = json_key

SCHEMA = {
    "type": "object",
    "properties": {
        "judgments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "ai": {"type": "string", "enum": list(AI)},
                    **{topic: {"type": "boolean"} for topic in TOPICS},
                    "kind": {"type": "string", "enum": ENUMS["kind"]},
                    "audience": {"type": "string", "enum": ENUMS["audience"]},
                },
                "required": ["id", "ai", *TOPICS, "kind", "audience"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["judgments"],
    "additionalProperties": False,
}

SYSTEM = f"""You judge what events in Munich are about, for a board of AI, startup, career and
tech events for students and professionals. Each numbered line is one event as JSON: either
its fields, or the title and opening text of the event's own page. For each event answer:
- ai: "core" if its text names artificial intelligence, machine learning or data science as
  its main topic, "some" if as one theme among others, "none" if its text says what the event
  is about and that is not them, "unknown" if its text says too little to tell what the event
  is about, such as a bare title or a link.
- startup: true if it is about startups, founding a company or investing in one.
- career: true if it is about careers, jobs, internships or hiring, or employers or
  recruiters meet the attendees.
- tech: true if its subject is software, computing, IT, engineering or a digital industry,
  or a technology company presents its work; science, art or a show that touches on
  technology is not tech.
- kind, its format: industry_talk is a talk or panel by people from companies;
  flagship_lecture a renowned lecture series; programme an application-based programme,
  fellowship or accelerator; otherwise one of: {", ".join(ENUMS["kind"])}.
- audience: "children", "school_pupils", "families", or "adults" (university students,
  professionals or the general public, also when children are welcome).
Judge each event only by what its own line says it is, not by its organiser, series or
venue: a university's concert is not about tech, an academy's lecture on religion is not
about AI, and a theatre show about physics is not tech. Answer every id with
{{"id", "ai", "startup", "career", "tech", "kind", "audience"}}."""


def page(body: bytes, url: str) -> dict | None:
    """The candidate of a page's one event: its title and opening text, without the links'
    addresses; None when the page shows no text."""
    text = html.text(body, url, TEXT, links=False)
    if not text:
        return None
    title = LexborHTMLParser(body).css_first("title")
    return {"page": title.text(strip=True) if title else "", "text": text}


def of_fields(fields: dict[str, Evidence]) -> dict | None:
    """The candidate of an event of a listing or feed: what its fields say; None without a
    title."""
    shown = {
        name: str(fields[name].value)[:TEXT]
        for name in _FIELDS
        if name in fields and fields[name].value not in (None, "")
    }
    return shown if "title" in shown else None


def candidates(
    events: Sequence[ParsedEvent], body: bytes, url: str, event_page: bool
) -> list[dict | None]:
    """A candidate per event: the page's for the one event of an event page, which is the
    same before and after an extraction, else each event's fields."""
    if event_page and len(events) == 1 and (whole := page(body, url)):
        return [whole]
    return [of_fields(event.fields) for event in events]


def question(candidates: list[dict]) -> str:
    return "\n".join(
        f"{i}. {json.dumps(c, ensure_ascii=False, default=str)}" for i, c in enumerate(candidates)
    )


def telling(judgment: dict | None) -> dict | None:
    """The judgment, or None when its text said too little to tell what the event is about:
    such an event is judged as without a judgment (invariant 7)."""
    return judgment if judgment and judgment["ai"] != "unknown" else None


def answers(result: dict, count: int) -> dict[int, dict]:
    """The judgments the model gave for ids 0 to `count` - 1, each in the schema's shape;
    others are left out."""
    found = {}
    for judgment in result.get("judgments") or []:
        if not isinstance(judgment, dict) or type(ident := judgment.get("id")) is not int:
            continue
        answer = {k: judgment.get(k) for k in ("ai", *TOPICS, "kind", "audience")}
        if (
            0 <= ident < count
            and answer["ai"] in AI
            and all(isinstance(answer[t], bool) for t in TOPICS)
            and answer["kind"] in ENUMS["kind"]
            and answer["audience"] in ENUMS["audience"]
        ):
            found[ident] = answer
    return found
