import re
from datetime import datetime

from .model import AUDIENCE, OFF_TOPIC, Verdict, field_value
from .profile import Profile
from .resolve import local_date


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

    def in_scope(
        self,
        fields: dict,
        kind: str | None,
        relevance: str,
        *,
        ai_focus: bool,
        startup_focus: bool,
        judgment: dict | None = None,
    ) -> bool:
        """About AI, or an AI-focused or startup organiser's, or of a kind shown whatever its
        topic, or judged to be about a topic of the profile; without a judgment, a startup,
        career or coding event by its words."""
        if relevance in {"core", "some"} or ai_focus or startup_focus:
            return True
        if kind in self.profile.any_topic_kinds:
            return True
        if judgment:
            return any(judgment[t] for t in self.profile.topics)
        return any(w in self._text(fields) for w in self.profile.scope_keywords)

    def fact_drop(self, fields: dict, now: datetime) -> Verdict | None:
        """The drop for a fact of the event, whatever its topic: past, online only or
        outside Munich."""
        today = now.date().isoformat()
        last_day = local_date(field_value(fields, "ends_at") or field_value(fields, "starts_at"))
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
        ai_focus: bool,
        startup_focus: bool,
        stars: int = 0,
        judgment: dict | None = None,
    ) -> Verdict:
        """The tier or drop of an event by its fields and, when the LLM has judged what its
        page says, by that judgment (ADR 0018)."""
        verdict = self._by_rules(
            fields, now, ai_focus=ai_focus, startup_focus=startup_focus, judgment=judgment
        )
        if verdict.tier is None:
            return verdict
        # Stars lift a tier (ADR 0015). Tiers are letters, A the highest.
        for count, tier in self.profile.star_tiers:
            if stars >= count and tier < verdict.tier:
                return Verdict(tier, verdict.reasons + (f"starred by {count} or more",))
        return verdict

    def _by_rules(
        self,
        fields: dict,
        now: datetime,
        *,
        ai_focus: bool,
        startup_focus: bool,
        judgment: dict | None,
    ) -> Verdict:
        audience, phrase = self.audience(fields, judgment)
        if audience:
            return Verdict(None, tuple(filter(None, (f"audience={audience}", phrase))), AUDIENCE)
        price = field_value(fields, "price") or "unknown"
        kind = self.kind(fields, judgment)
        relevance = self.ai_relevance(fields, judgment)
        reasons = (f"price={price}", f"kind={kind}", f"ai={relevance}")
        if judgment:
            topics = [t for t in sorted(self.profile.topics) if judgment[t]]
            reasons += (f"judged: {', '.join(topics) or 'no topic'}",)
        if not self.in_scope(
            fields,
            kind,
            relevance,
            ai_focus=ai_focus,
            startup_focus=startup_focus,
            judgment=judgment,
        ):
            return Verdict(None, reasons, OFF_TOPIC)
        if drop := self.fact_drop(fields, now):
            return drop
        if price == "paid":
            return Verdict("C", reasons + ("paid, no free route",))
        # A career fair or hackathon ranks by its kind, whatever its topic.
        any_topic = kind in self.profile.any_topic_kinds
        if relevance == "none" and not any_topic:
            return Verdict("C", reasons + ("not AI-relevant",))
        if relevance == "unknown" and not ai_focus and not any_topic:
            return Verdict("C", reasons + ("AI relevance unknown, organiser not AI-focused",))
        if kind in self.profile.a_kinds:
            return Verdict("A", reasons)
        return Verdict("B", reasons)
