"""Asking the LLM who an organisation is, from its own homepage and about page."""

from datetime import date
from urllib.parse import urlsplit

from ..extract.router import Router
from ..parse import html
from .discovery import matches

KINDS = ["academic", "community", "industry", "startup", "student", "other"]

PROFILE_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "The organisation's name as the site gives it"},
        "kind": {"type": "string", "enum": KINDS},
        "summary": {"type": "string", "description": "One or two sentences: who they are"},
        "based_in_munich": {"type": "string", "enum": ["yes", "no", "unclear"]},
        "runs_events": {"type": "string", "enum": ["yes", "no", "unclear"]},
        "ai_focus": {"type": "boolean", "description": "AI, ML or data is its main topic"},
        "legitimate": {"type": "string", "enum": ["yes", "no", "unclear"]},
        "reasons": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Two to four short sentences citing what on the pages supports it",
        },
        "event_links": {
            "type": "array",
            "items": {"type": "string"},
            "description": "URLs from the link list that likely list this organisation's events",
        },
    },
    "required": [
        "name",
        "kind",
        "summary",
        "based_in_munich",
        "runs_events",
        "ai_focus",
        "legitimate",
        "reasons",
        "event_links",
    ],
    "additionalProperties": False,
}

PROFILE_SYSTEM = """You profile an organisation from its own website for a tracker of AI,
tech and startup events in Munich. Use only the text given; do not rely on memory.
- legitimate: "yes" if this is a genuine organisation's own site (a club, community,
  institute, company, programme or conference). "no" for parked domains, spam, shops,
  link farms, or sites that only aggregate other organisers' events. "unclear" otherwise.
- runs_events: does the organisation itself host events (talks, meetups, workshops,
  hackathons, conferences, programmes)?
- based_in_munich: is it based in or does it hold events in Munich or its surroundings?
- reasons: cite the page, e.g. an address, an events section, named past events.
- event_links: copy URLs exactly from the link list; never invent one.
Today is TODAY. Return JSON only."""


def about_page(anchors: list[tuple[str, str]], home: str, signals: tuple[str, ...]) -> str | None:
    """The first link on the homepage to its own about or imprint page."""
    site = urlsplit(home).hostname
    return next(
        (u for u, text in anchors if urlsplit(u).hostname == site and matches(text, signals)),
        None,
    )


def ask(
    router: Router,
    home: str,
    name: str | None,
    body: bytes,
    anchors: list[tuple[str, str]],
    about: str | None,
    about_text: str,
) -> dict:
    """The LLM's profile of the organisation, with the model that answered and only the
    event links that are really on the page. Raises what the router raises."""
    link_list = "\n".join(f"- {label or '(no text)'}: {u}" for u, label in anchors[:120])
    user = (
        f"Submitted URL: {home}\nName given: {name or '-'}\n\n"
        f"Homepage text:\n{html.text(body, home, 8000)}\n\n"
        + (f"About page {about}:\n{about_text}\n\n" if about_text else "")
        + f"Links on the homepage:\n{link_list}"
    )
    system = PROFILE_SYSTEM.replace("TODAY", date.today().isoformat())
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    model, profile = router.complete(messages, PROFILE_SCHEMA, name="organisation")
    page_links = {u for u, _ in anchors}
    profile["event_links"] = [u for u in profile.get("event_links") or [] if u in page_links]
    profile["model"] = model
    return profile
