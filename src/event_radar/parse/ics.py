import re
from datetime import date, datetime

from icalendar import Calendar

from ..model import Evidence, ParsedEvent
from ..platforms import platform_key

_UID_KEYS = (
    (re.compile(r"^(evt-[A-Za-z0-9]+)@events\.lu\.ma$"), "luma"),
    (re.compile(r"^event_(\d+)@meetup\.com$"), "meetup"),
)
_URL = re.compile(r"https?://[^\s<>\"']+")


def _iso(value: object) -> str | None:
    if isinstance(value, datetime | date):
        return value.isoformat()
    return None


def _uid_key(uid: str) -> str | None:
    for pattern, platform in _UID_KEYS:
        match = pattern.match(uid)
        if match:
            return f"{platform}:{match.group(1)}"
    return f"ical:{uid}" if uid and "@" in uid else None


def events(body: bytes) -> list[ParsedEvent]:
    try:
        calendar = Calendar.from_ical(body)
    except ValueError:
        return []
    found = []
    for component in calendar.walk("VEVENT"):
        start = component.get("DTSTART")
        end = component.get("DTEND")
        url = str(component.get("URL") or "") or None
        description = str(component.get("DESCRIPTION") or "")
        uid = str(component.get("UID") or "").strip()
        links = tuple(dict.fromkeys(_URL.findall(description)))
        keys = {k for k in (_uid_key(uid), url and platform_key(url)) if k}
        values = {
            "title": str(component.get("SUMMARY") or "") or None,
            "starts_at": _iso(start.dt) if start else None,
            "ends_at": _iso(end.dt) if end else None,
            "venue": str(component.get("LOCATION") or "") or None,
            "event_url": url,
            "description": description[:2000] or None,
        }
        found.append(
            ParsedEvent(
                {k: Evidence(v) for k, v in values.items() if v}, tuple(sorted(keys)), links
            )
        )
    return found
