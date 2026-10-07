VERSION = "events-v1"

ENUMS = {
    "attendance": ["in_person", "online", "mixed"],
    "reg_mode": [
        "none_needed",
        "open_signup",
        "approval",
        "application",
        "invite_only",
        "ticket_purchase",
    ],
    "reg_availability": ["not_open", "open", "waitlist", "sold_out", "closed"],
    "price": ["free", "free_student_route", "paid"],
    "kind": [
        "career_fair",
        "conference",
        "symposium",
        "flagship_lecture",
        "hackathon",
        "programme",
        "demo_day",
        "industry_talk",
        "meetup",
        "workshop",
        "talk",
        "other",
    ],
    "ai_relevance": ["core", "some", "none"],
}

TEXT_FIELDS = {
    "title": "Event name as written",
    "starts_at": "Start, ISO 8601 (date, or date and time with Europe/Berlin offset)",
    "ends_at": "End, ISO 8601",
    "venue": "Venue name and address",
    "city": "City of the venue",
    "organiser": "Organising body",
    "reg_url": "URL where one registers, applies or buys a ticket, copied from the page",
    "reg_opens_at": "When registration or applications open, ISO 8601",
    "reg_closes_at": "Registration or application deadline, ISO 8601",
    "prerequisites": "Who may attend, eligibility, or separate sign-ups, in a short phrase",
}

DATE_FIELDS = {"starts_at", "ends_at", "reg_opens_at", "reg_closes_at"}
URL_FIELDS = {"reg_url"}


def _field(value_schema: dict, description: str) -> dict:
    return {
        "type": "object",
        "description": description,
        "properties": {
            "value": value_schema,
            "quote": {
                "type": ["string", "null"],
                "description": "Verbatim text from the page that states the value",
            },
        },
        "required": ["value", "quote"],
        "additionalProperties": False,
    }


def _event_schema() -> dict:
    properties = {
        name: _field({"type": ["string", "null"]}, description)
        for name, description in TEXT_FIELDS.items()
    }
    properties |= {
        name: _field({"type": ["string", "null"], "enum": [*values, None]}, name)
        for name, values in ENUMS.items()
    }
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


SCHEMA = {
    "type": "object",
    "properties": {"events": {"type": "array", "items": _event_schema()}},
    "required": ["events"],
    "additionalProperties": False,
}

SYSTEM = f"""You extract facts about events from one web page for a student's event tracker.
Rules:
- Only events this page announces. A page about an application round (a club, programme or
  hackathon application) counts as one event; use kind "programme" or "hackathon".
- Every non-null value needs "quote": text copied verbatim from the page, at most 200
  characters, that states it. If the page does not state a fact, value and quote are null.
  Never infer or guess a date, time, price, deadline or URL.
- Dates are ISO 8601. If the page omits the year, use the next occurrence after TODAY.
- reg_closes_at is the deadline to register or apply, not the event date.
- price "free_student_route" means students can attend free (volunteering, free student pass).
- ai_relevance: "core" if AI, machine learning or data science is the topic, "some" if it is
  one theme among others, "none" if absent.
- kind is one of: {", ".join(ENUMS["kind"])}.
Return JSON only."""
