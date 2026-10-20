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


_MONTH_OF = {name: month for month, names in MONTHS.items() for name in names}
_MONTH = "|".join(sorted(_MONTH_OF, key=len, reverse=True))
_ORD = r"(?:\.|st|nd|rd|th)?"
_SEP = r"\s*(?P<sep>-|to|until|through|bis|and|und|&)\s*"
_LIST = frozenset({"and", "und", "&"})
# Two days and their months: "11-13 november", "30. oktober bis 2. november",
# "november 11-13", "11.-13.11.", "4 and 11 november". A month named once is both days'.
_SPANS = (
    re.compile(
        rf"\b(?P<d1>\d{{1,2}}){_ORD}\s*(?:(?P<m1>{_MONTH})\.?)?{_SEP}"
        rf"(?P<d2>\d{{1,2}}){_ORD}\s*(?P<m2>{_MONTH})\b"
    ),
    re.compile(
        rf"\b(?P<m1>{_MONTH})\.?\s*(?P<d1>\d{{1,2}}){_ORD}{_SEP}"
        rf"(?:(?P<m2>{_MONTH})\.?\s*)?(?P<d2>\d{{1,2}})\b"
    ),
    re.compile(
        r"\b(?P<d1>\d{1,2})\.(?:(?P<m1>\d{1,2})\.)?\s?(?P<sep>-|bis)\s?"
        r"(?P<d2>\d{1,2})\.(?P<m2>\d{1,2})\b"
    ),
)


def _flat(text: str) -> str:
    folded = unicodedata.normalize("NFKC", text).lower()
    return re.sub(r"\s+", " ", folded.replace("–", "-").replace("—", "-")).strip()


def _month(name: str | None) -> int | None:
    if name is None:
        return None
    month = int(name) if name.isdigit() else _MONTH_OF[name]
    return month if 1 <= month <= 12 else None


def _spans(flat: str) -> list[tuple[tuple[int, int], tuple[int, int]]]:
    """Each first and last (month, day) a text names together; the two days of a list
    ("4 and 11 November") are spans of one day each."""
    found = []
    for pattern in _SPANS:
        for m in pattern.finditer(flat):
            first, last = _month(m["m1"]), _month(m["m2"])
            first, last = first or last, last or first
            if not first:
                continue
            start, end = (first, int(m["d1"])), (last, int(m["d2"]))
            found += [(start, start), (end, end)] if m["sep"] in _LIST else [(start, end)]
    return found


def _holds(start: tuple[int, int], end: tuple[int, int], day: date) -> bool:
    """`day` lies from `start` to `end`, in its own year or across a new year."""
    for first, last in ((0, 0),) if start <= end else ((-1, 0), (0, 1)):
        try:
            if date(day.year + first, *start) <= day <= date(day.year + last, *end):
                return True
        except ValueError:
            continue
    return False


def names_date(value: str, quote: str, *, within: bool = False) -> tuple[bool, bool]:
    """Whether `quote` names the day of the ISO date `value`, in German or English, and
    whether it states its year. A range names its first and last day; `within` counts the
    days between too."""
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
    spans = _spans(flat)
    found = bool(
        re.search(numeric, flat)
        or re.search(named, flat)
        or any((day.month, day.day) in span for span in spans)
        or (within and any(_holds(*span, day) for span in spans))
    )
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
