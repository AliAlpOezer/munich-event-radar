import re
import unicodedata
from datetime import date

from ..model import Evidence, ParsedEvent
from .schema import DATE_FIELDS, ENUMS, TEXT_FIELDS, URL_FIELDS

MONTHS = {
    1: ("jan", "januar", "january", "jän"),
    2: ("feb", "februar", "february"),
    3: ("mar", "mär", "märz", "march", "maerz"),
    4: ("apr", "april"),
    5: ("mai", "may"),
    6: ("jun", "juni", "june"),
    7: ("jul", "juli", "july"),
    8: ("aug", "august"),
    9: ("sep", "sept", "september"),
    10: ("okt", "oct", "oktober", "october"),
    11: ("nov", "november"),
    12: ("dez", "dec", "dezember", "december"),
}


def _flat(text: str) -> str:
    folded = unicodedata.normalize("NFKC", text).lower()
    return re.sub(r"\s+", " ", folded.replace("–", "-").replace("—", "-")).strip()


def names_date(value: str, quote: str) -> tuple[bool, bool]:
    """Whether `quote` names the day of the ISO date `value`, in German or English, and
    whether it states its year."""
    try:
        day = date.fromisoformat(value[:10])
    except ValueError:
        return False, False
    flat = _flat(quote)
    numeric = rf"\b0?{day.day}\.\s?0?{day.month}\b|\b{day.year}-{day.month:02d}-{day.day:02d}\b"
    named = (
        rf"\b0?{day.day}(\.|st|nd|rd|th)?\s*({'|'.join(MONTHS[day.month])})"
        rf"|({'|'.join(MONTHS[day.month])})\.?\s*0?{day.day}\b"
    )
    found = bool(re.search(numeric, flat) or re.search(named, flat))
    return found, str(day.year) in flat or f".{day.year % 100:02d}" in flat


def verify(raw: dict, page_text: str) -> tuple[ParsedEvent, list[str]]:
    page = _flat(page_text)
    accepted: dict[str, Evidence] = {}
    rejected: list[str] = []
    for name in (*TEXT_FIELDS, *ENUMS):
        item = raw.get(name) or {}
        value, quote = item.get("value"), item.get("quote")
        if value in (None, ""):
            continue
        if not quote or _flat(quote) not in page:
            rejected.append(name)
            continue
        if name in ENUMS and value not in ENUMS[name]:
            rejected.append(name)
            continue
        if name in URL_FIELDS and _flat(str(value)) not in page:
            rejected.append(name)
            continue
        if name in DATE_FIELDS:
            found, year_stated = names_date(str(value), quote)
            if not found:
                rejected.append(name)
                continue
            if not year_stated:
                accepted[f"{name}_year_stated"] = Evidence(False, quote)
        accepted[name] = Evidence(value, quote)
    return ParsedEvent(accepted), rejected
