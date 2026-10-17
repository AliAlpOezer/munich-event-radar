"""The relevance profile, `profile.yaml` (ADR 0002 D6): what counts as Munich, AI and each
kind of event, and how intake recognises event and about pages."""

from dataclasses import dataclass
from pathlib import Path

import yaml

from .model import SourceClass
from .platforms import is_ticket_host


@dataclass(frozen=True)
class Online:
    venues: tuple[str, ...]
    phrases: tuple[str, ...]
    in_person: tuple[str, ...]


@dataclass(frozen=True)
class Profile:
    cities: frozenset[str]
    postal_codes: tuple[str, ...]
    aggregators: tuple[str, ...]
    a_kinds: frozenset[str]
    any_topic_kinds: frozenset[str]
    ai_keywords: tuple[str, ...]
    kind_keywords: dict[str, tuple[str, ...]]
    online: Online
    link_signals: tuple[str, ...]
    about_signals: tuple[str, ...]

    @classmethod
    def load(cls, path: Path) -> "Profile":
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        return cls(
            cities=frozenset(c.lower() for c in raw["munich"]["cities"]),
            postal_codes=tuple(raw["munich"]["postal_codes"]),
            aggregators=tuple(raw["aggregators"]),
            a_kinds=frozenset(raw["a_kinds"]),
            any_topic_kinds=frozenset(raw["any_topic_kinds"]),
            ai_keywords=tuple(raw["keywords"]["ai"]),
            kind_keywords={k: tuple(v) for k, v in raw["keywords"]["kinds"].items()},
            online=Online(**{k: tuple(v) for k, v in raw["online"].items()}),
            link_signals=tuple(raw["intake"]["link_signals"]),
            about_signals=tuple(raw["intake"]["about_signals"]),
        )

    def source_class(self, url: str) -> SourceClass:
        """AGG for an aggregator, even on a ticket platform (Eventbrite's search pages),
        TICKET for a ticket platform, else the organiser's OWN."""
        if any(a in url for a in self.aggregators):
            return SourceClass.AGG
        return SourceClass.TICKET if is_ticket_host(url) else SourceClass.OWN
