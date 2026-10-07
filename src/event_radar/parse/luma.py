import json

from selectolax.lexbor import LexborHTMLParser

from ..model import Evidence, ParsedEvent


def _availability(data: dict, ticket: dict, event: dict) -> str:
    if ticket.get("is_sold_out"):
        return "waitlist" if event.get("waitlist_status") == "enabled" else "sold_out"
    return {"open": "open", "not_open_yet": "not_open", "closed": "closed"}.get(
        data.get("registration_availability"), "unknown"
    )


def _closes_at(data: dict) -> str | None:
    ends = [t.get("valid_end_at") for t in data.get("ticket_types") or [] if not t.get("is_hidden")]
    ends = [e for e in ends if e]
    return max(ends) if ends else None


def events(tree: LexborHTMLParser) -> list[ParsedEvent]:
    script = tree.css_first("script#__NEXT_DATA__")
    if script is None:
        return []
    try:
        data = json.loads(script.text())["props"]["pageProps"]["initialData"]["data"]
        event = data["event"]
    except (ValueError, KeyError, TypeError):
        return []
    ticket = data.get("ticket_info") or {}
    geo = event.get("geo_address_info") or {}
    values = {
        "title": event.get("name"),
        "starts_at": event.get("start_at"),
        "ends_at": event.get("end_at"),
        "venue": geo.get("address"),
        "address": geo.get("full_address"),
        "city": geo.get("city"),
        "attendance": {"offline": "in_person", "online": "online"}.get(event.get("location_type")),
        "organiser": ", ".join(h["name"] for h in data.get("hosts") or [] if h.get("name")) or None,
        "event_url": f"https://luma.com/{event['url']}" if event.get("url") else None,
        "reg_mode": "approval" if ticket.get("require_approval") else "open_signup",
        "reg_url": f"https://luma.com/{event['url']}" if event.get("url") else None,
        "reg_availability": _availability(data, ticket, event),
        "reg_closes_at": _closes_at(data),
        "spots_left": ticket.get("spots_remaining"),
        "price": "free" if ticket.get("is_free") else "paid" if "is_free" in ticket else None,
    }
    keys = tuple(f"luma:{k}" for k in (event.get("api_id"), event.get("url")) if k)
    return [ParsedEvent({k: Evidence(v) for k, v in values.items() if v is not None}, keys)]
