"""The relevance profile, `profile.yaml` (ADR 0002 D6): what counts as Munich, AI and each
kind of event, how intake recognises event and about pages, and what the web search asks."""

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from .model import SourceClass, Tier
from .platforms import is_ticket_host


def _keywords(words: list[str]) -> tuple[str, ...]:
    """The judge matches keywords against spaced words, so one holding punctuation could
    never match."""
    if bad := [w for w in words if not re.fullmatch(r"[^\W_]+( [^\W_]+)*", w.strip())]:
        raise ValueError(f"keywords with punctuation never match: {bad}")
    return tuple(words)


@dataclass(frozen=True)
class Online:
    venues: tuple[str, ...]
    phrases: tuple[str, ...]
    in_person: tuple[str, ...]


@dataclass(frozen=True)
class Search:
    """The daily web search (ADR 0016)."""

    queries: tuple[str, ...]
    exclude_domains: tuple[str, ...]
    daily_cap: int  # searches a UTC day, one credit each
    daily_candidates: int  # new intakes in review a UTC day


@dataclass(frozen=True)
class Profile:
    cities: frozenset[str]
    postal_codes: tuple[str, ...]
    aggregators: tuple[str, ...]
    a_kinds: frozenset[str]
    any_topic_kinds: frozenset[str]
    star_tiers: tuple[tuple[int, str], ...]
    ai_keywords: tuple[str, ...]
    kind_keywords: dict[str, tuple[str, ...]]
    scope_keywords: tuple[str, ...]
    audiences: dict[str, tuple[str, ...]]
    online: Online
    link_signals: tuple[str, ...]
    about_signals: tuple[str, ...]
    search: Search

    @classmethod
    def load(cls, path: Path) -> "Profile":
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        return cls(
            cities=frozenset(c.lower() for c in raw["munich"]["cities"]),
            postal_codes=tuple(raw["munich"]["postal_codes"]),
            aggregators=tuple(raw["aggregators"]),
            a_kinds=frozenset(raw["a_kinds"]),
            any_topic_kinds=frozenset(raw["any_topic_kinds"]),
            star_tiers=tuple(
                sorted(((n, Tier(t).value) for t, n in raw["stars"].items()), reverse=True)
            ),
            ai_keywords=_keywords(raw["keywords"]["ai"]),
            kind_keywords={k: _keywords(v) for k, v in raw["keywords"]["kinds"].items()},
            scope_keywords=_keywords([w for words in raw["scope"].values() for w in words]),
            audiences={k: tuple(v) for k, v in raw["audience"].items()},
            online=Online(**{k: tuple(v) for k, v in raw["online"].items()}),
            link_signals=tuple(raw["intake"]["link_signals"]),
            about_signals=tuple(raw["intake"]["about_signals"]),
            search=Search(
                queries=tuple(raw["search"]["queries"]),
                exclude_domains=tuple(raw["search"]["exclude_domains"]),
                daily_cap=int(raw["search"]["daily_cap"]),
                daily_candidates=int(raw["search"]["daily_candidates"]),
            ),
        )

    def source_class(self, url: str) -> SourceClass:
        """AGG for an aggregator, even on a ticket platform (Eventbrite's search pages),
        TICKET for a ticket platform, else the organiser's OWN."""
        if any(a in url for a in self.aggregators):
            return SourceClass.AGG
        return SourceClass.TICKET if is_ticket_host(url) else SourceClass.OWN
