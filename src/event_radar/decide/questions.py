"""What the radar asks a decision model (ADR 0017). The wording lives here, the words and
thresholds in `profile.yaml`. Each catalogue entry carries a version, since bands measured
for one wording say nothing about another."""

import re
import unicodedata
from collections.abc import Iterable

from ..profile import Profile

IN_SCOPE = "in_scope-v1"
REGISTRATION = "registration-v1"

# The topics of the LLM triage (`triage.system`) and of `profile.yaml` `scope`, one question
# each, so that no option order biases the answer (ADR 0017 D5). In scope when any is yes.
_TOPICS = {
    "ai": "artificial intelligence, machine learning or data science",
    "startup": "startups, founding a company or investing",
    "career": "careers, jobs or hiring",
    "developer": "coding, programming or software development",
}

# Each question asks after one kind of registration detail. Beside it are the fields of the
# LLM's answer that state that detail, which calibration takes as the reference.
_REGISTRATION = {
    "signup": (
        "Does the text say how to register, sign up, apply or get a ticket for the event, or"
        " that no registration is needed?",
        ("reg_mode", "reg_url"),
    ),
    "window": (
        "Does the text say when registration or applications open or close, such as a deadline?",
        ("reg_opens_at", "reg_closes_at"),
    ),
    "price": ("Does the text say what attending the event costs, or that it is free?", ("price",)),
    "places": (
        "Does the text say whether registration is open, not yet open or closed, or that the"
        " event is sold out or has a waiting list?",
        ("reg_availability",),
    ),
}
REGISTRATION_FIELDS = {name: fields for name, (_, fields) in _REGISTRATION.items()}


def in_scope(profile: Profile) -> dict:
    """D1, D5 and D6: is the event about the radar's topics, or of a kind it always shows?"""
    words = {"ai": profile.ai_keywords, **profile.scope_groups}
    asked = {
        group: _noul(f"Is this event about {_TOPICS[group]}? Related terms: {_terms(terms)}.")
        for group, terms in words.items()
    }
    kinds = sorted(profile.any_topic_kinds)
    asked["kind"] = _noul(
        f"Is this event a {' or a '.join(k.replace('_', ' ') for k in kinds)}? Related terms:"
        f" {_terms(w for k in kinds for w in profile.kind_keywords[k])}."
    )
    return asked


def registration() -> dict:
    """D2: does the page state registration details, each kind asked on its own?"""
    return {name: _noul(text) for name, (text, _) in _REGISTRATION.items()}


def passages(text: str, words: Iterable[str], window: int) -> list[str]:
    """The text within `window` characters of each word, in page order, overlaps merged."""
    flat = re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text)).strip()
    pattern = re.compile("|".join(re.escape(w) for w in words), re.IGNORECASE)
    spans: list[tuple[int, int]] = []
    for match in pattern.finditer(flat):
        start, stop = max(0, match.start() - window), min(len(flat), match.end() + window)
        if spans and start <= spans[-1][1]:
            spans[-1] = (spans[-1][0], stop)
        else:
            spans.append((start, stop))
    return [flat[start:stop] for start, stop in spans]


def _noul(instructions: str) -> dict:
    return {"type": "noul", "instructions": instructions}


def _terms(words: Iterable[str]) -> str:
    return ", ".join(dict.fromkeys(w.strip() for w in words))
