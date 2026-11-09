import re
from datetime import datetime

from .model import AUDIENCE, NOT_EVENT, OFF_TOPIC, Standing, Verdict, field_value
from .profile import Profile
from .resolve import local_date

STRONG, WEAK = "strong", "weak"
TOPIC = {STRONG: "about AI, startups or careers", WEAK: "AI or tech as a side theme"}


def _words(*parts: object) -> str:
    """The parts as their words, lowercased and spaced, so a keyword matches whatever
    punctuation surrounds it."""
    text = " ".join(str(p) for p in parts if p).lower()
    return " " + " ".join(re.findall(r"[^\W_]+", text)) + " "


class Judge:
    """Tiers an event by the profile's rules, or drops it with the rule that dropped it."""

    def __init__(self, profile: Profile):
        self.profile = profile

    def _in_munich(self, fields: dict) -> bool | None:
        city = str(field_value(fields, "city") or "").strip().lower()
        if city:
            return city in self.profile.cities
        place = " ".join(str(field_value(fields, n) or "") for n in ("address", "venue")).lower()
        if postcodes := re.findall(r"\b\d{5}\b", place):
            return any(p.startswith(self.profile.postal_codes) for p in postcodes)
        return True if any(c in place for c in self.profile.cities) else None

    def attendance(self, fields: dict) -> tuple[str | None, str | None]:
        if stated := field_value(fields, "attendance"):
            return str(stated), None
        online = self.profile.online
        venue = str(field_value(fields, "venue") or "")
        if " ".join(re.findall(r"[^\W_]+", venue.lower())) in online.venues:
            return "online", f"venue '{venue}'"
        if venue or field_value(fields, "address"):
            return None, None
        description = str(field_value(fields, "description") or "").lower()
        if any(p in description for p in online.in_person):
            return None, None
        phrase = next((p for p in online.phrases if p in description), None)
        return ("online", f"description '{phrase}'") if phrase else (None, None)

    def _text(self, fields: dict) -> str:
        return _words(*(field_value(fields, n) for n in ("title", "description", "organiser")))

    def kind(self, fields: dict, judgment: dict | None = None) -> str | None:
        if stated := field_value(fields, "kind"):
            return str(stated)
        if judgment:
            return judgment["kind"]
        title = _words(field_value(fields, "title"))
        for kind, words in self.profile.kind_keywords.items():
            if any(w in title for w in words):
                return kind
        return None

    def audience(self, fields: dict, judgment: dict | None = None) -> tuple[str | None, str | None]:
        """The audience other than adults an event is aimed at (ADR 0012), as the LLM
        states or judges it or a title phrase names it, and the phrase."""
        stated = field_value(fields, "audience")
        if stated in self.profile.audiences:
            return str(stated), None
        if judgment and judgment["audience"] in self.profile.audiences:
            return judgment["audience"], "judged"
        title = str(field_value(fields, "title") or "").lower().replace("’", "'")
        for audience, phrases in self.profile.audiences.items():
            if phrase := next((p for p in phrases if p in title), None):
                return audience, f"title '{phrase}'"
        return None, None

    def ai_relevance(self, fields: dict, judgment: dict | None = None) -> str:
        if judgment:
            return judgment["ai"]
        if stated := field_value(fields, "ai_relevance"):
            return str(stated)
        return (
            "core" if any(w in self._text(fields) for w in self.profile.ai_keywords) else "unknown"
        )

    def names_scope(self, text: str) -> bool:
        """`text` holds an AI or scope word: the check of a page without structured data
        (ADR 0014)."""
        found = _words(text)
        return any(w in found for w in self.profile.ai_keywords + self.profile.scope_keywords)

    def topics(self, fields: dict, judgment: dict | None = None) -> list[str]:
        """The topics besides AI the event is about: as judged, else as its words name them."""
        if judgment:
            return [t for t in sorted(self.profile.topics) if judgment[t]]
        text = self._text(fields)
        return [
            t
            for t in sorted(self.profile.topics)
            if any(w in text for w in self.profile.scope_groups.get(t, ()))
        ]

    def topic(self, relevance: str, topics: list[str], *, ai_focus: bool) -> str | None:
        """How squarely the event is about the board's topics (ADR 0021): STRONG for AI as
        its main topic, a strong topic of the profile, or AI unknown at an AI-focused
        organiser; WEAK for AI as one theme among others or a weak topic; else None."""
        if (
            relevance == "core"
            or (relevance == "unknown" and ai_focus)
            or self.profile.strong_topics.intersection(topics)
        ):
            return STRONG
        if relevance == "some" or self.profile.weak_topics.intersection(topics):
            return WEAK
        return None

    def networking(self, fields: dict, judgment: dict | None = None) -> bool:
        """Meeting people is the point of the event: as judged, else as its title names a
        networking format. A judgment of an older question has no answer to it."""
        if judgment and judgment.get("networking") is not None:
            return bool(judgment["networking"])
        title = _words(field_value(fields, "title"))
        return any(w in title for w in self.profile.networking)

    def last_day(self, fields: dict) -> str | None:
        """The day after which the event is past: its end, else its start, else the close of
        its registration. A programme is joined by applying, so its close ends it however
        far ahead its class starts (ADR 0022 D1)."""
        closes = local_date(field_value(fields, "reg_closes_at"))
        if closes and self.kind(fields) == "programme":
            return closes
        own = field_value(fields, "ends_at") or field_value(fields, "starts_at")
        return local_date(own) if own else closes

    def fact_drop(self, fields: dict, now: datetime) -> Verdict | None:
        """The drop for a fact of the event, whatever its topic: past, online only or
        outside Munich."""
        today = now.date().isoformat()
        last_day = self.last_day(fields)
        if last_day and last_day < today:
            return Verdict(None, (), "past")
        attendance, evidence = self.attendance(fields)
        if attendance == "online":
            return Verdict(None, tuple(filter(None, (evidence,))), "online_only")
        if self._in_munich(fields) is False:
            return Verdict(None, (), "outside_munich")
        return None

    def judge(
        self,
        fields: dict,
        now: datetime,
        *,
        standing: Standing = Standing.CALENDAR,
        ai_focus: bool = False,
        stars: int = 0,
        judgment: dict | None = None,
    ) -> Verdict:
        """The tier or drop of an event by its fields, its organiser's standing and, when the
        LLM has judged what its page says, by that judgment (ADR 0018, ADR 0021)."""
        verdict = self._by_rules(
            fields, now, standing=standing, ai_focus=ai_focus, judgment=judgment
        )
        if verdict.tier is None:
            return verdict
        # An anchor's event is at least B (ADR 0021), stars lift a tier (ADR 0015). Tiers are
        # letters, A the highest.
        floors = [(standing is Standing.ANCHOR, "B", "an anchor community's event")]
        floors += [
            (stars >= count, tier, f"starred by {count} or more")
            for count, tier in self.profile.star_tiers
        ]
        for applies, tier, reason in floors:
            if applies and tier < verdict.tier:
                verdict = Verdict(tier, verdict.reasons + (reason,))
        return verdict

    def _by_rules(
        self,
        fields: dict,
        now: datetime,
        *,
        standing: Standing,
        ai_focus: bool,
        judgment: dict | None,
    ) -> Verdict:
        audience, phrase = self.audience(fields, judgment)
        if audience:
            return Verdict(None, tuple(filter(None, (f"audience={audience}", phrase))), AUDIENCE)
        price = field_value(fields, "price") or "unknown"
        kind = self.kind(fields, judgment)
        relevance = self.ai_relevance(fields, judgment)
        topics = self.topics(fields, judgment)
        topic = self.topic(relevance, topics, ai_focus=ai_focus)
        networking = self.networking(fields, judgment)
        reasons = (f"price={price}", f"kind={kind}", f"ai={relevance}")
        if judgment:
            reasons += (f"judged: {', '.join(topics) or 'no topic'}",)
        if networking:
            reasons += ("networking",)
        if standing is not Standing.CALENDAR:
            reasons += (f"organiser={standing}",)
        any_topic = kind in self.profile.any_topic_kinds
        # Only a general calendar's events need a topic (ADR 0011, ADR 0021 invariant 3).
        if not (topic or any_topic or ai_focus or standing is not Standing.CALENDAR):
            return Verdict(None, reasons, OFF_TOPIC)
        if judgment and judgment.get("event") is False:
            return Verdict(
                None, reasons + ("judged a news page or report, not an event",), NOT_EVENT
            )
        if drop := self.fact_drop(fields, now):
            return drop
        if price == "paid":
            return Verdict("C", reasons + ("paid, no free route",))
        if any_topic:
            return Verdict("A", reasons + ("a career fair or hackathon, whatever its topic",))
        if topic == STRONG and (kind in self.profile.a_kinds or networking):
            door = "built for meeting people" if networking else f"a {kind.replace('_', ' ')}"
            return Verdict("A", reasons + (f"{TOPIC[STRONG]}, and {door}",))
        if topic == STRONG:
            return Verdict("B", reasons + (TOPIC[STRONG],))
        if topic == WEAK:
            followed = standing is not Standing.CALENDAR
            where = "an organiser the radar follows" if followed else "a general calendar"
            return Verdict("B" if followed else "C", reasons + (f"{TOPIC[WEAK]}, from {where}",))
        return Verdict("C", reasons + ("about none of the board's topics",))
