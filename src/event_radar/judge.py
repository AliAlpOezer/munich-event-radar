import re
from datetime import datetime

from .model import Verdict, field_value
from .profile import Profile
from .resolve import local_date


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
        parts = (field_value(fields, n) for n in ("title", "description", "organiser"))
        return " " + " ".join(str(p) for p in parts if p).lower() + " "

    def kind(self, fields: dict) -> str | None:
        if stated := field_value(fields, "kind"):
            return str(stated)
        title = " " + str(field_value(fields, "title") or "").lower() + " "
        text = self._text(fields)
        for scope in (title, text):
            for kind, words in self.profile.kind_keywords.items():
                if any(w in scope for w in words):
                    return kind
        return None

    def ai_relevance(self, fields: dict) -> str:
        if stated := field_value(fields, "ai_relevance"):
            return str(stated)
        return (
            "core" if any(w in self._text(fields) for w in self.profile.ai_keywords) else "unknown"
        )

    def judge(self, fields: dict, now: datetime, *, ai_focus: bool) -> Verdict:
        today = now.date().isoformat()
        last_day = local_date(field_value(fields, "ends_at") or field_value(fields, "starts_at"))
        if last_day and last_day < today:
            return Verdict(None, (), "past")
        attendance, evidence = self.attendance(fields)
        if attendance == "online":
            return Verdict(None, tuple(filter(None, (evidence,))), "online_only")
        if self._in_munich(fields) is False:
            return Verdict(None, (), "outside_munich")
        price = field_value(fields, "price") or "unknown"
        kind = self.kind(fields)
        relevance = self.ai_relevance(fields)
        reasons = (f"price={price}", f"kind={kind}", f"ai={relevance}")
        if price == "paid":
            return Verdict("C", reasons + ("paid, no free route",))
        if relevance == "none":
            return Verdict("C", reasons + ("not AI-relevant",))
        if relevance == "unknown" and not ai_focus and kind not in self.profile.any_topic_kinds:
            return Verdict("C", reasons + ("AI relevance unknown, organiser not AI-focused",))
        if kind in self.profile.a_kinds:
            return Verdict("A", reasons)
        return Verdict("B", reasons)
