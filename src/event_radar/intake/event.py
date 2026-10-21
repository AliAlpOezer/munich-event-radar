"""Adding one event from its page (ADR 0014): one fetch of the page and a check that needs
no LLM, then the page as a source of its own, without an organiser, judged like any other.
The day and place the person gives only test the page; they never become the event's
fields."""

import re
from collections.abc import Callable
from datetime import UTC, date, datetime
from urllib.parse import urlsplit

import httpx

from .. import html
from ..extract import Extractor, judgments
from ..extract.router import AllModelsFailed, BudgetExhausted, DailyCapReached, Router
from ..extract.verify import names_date
from ..fetch import Fetched, Fetcher
from ..harvester import EVENT_PAGE_HOURS, Harvester
from ..judge import Judge
from ..model import (
    AUDIENCE,
    OFF_TOPIC,
    Evidence,
    IntakeState,
    Kind,
    ParsedEvent,
    ParseResult,
    field_value,
)
from ..parse import parse
from ..platforms import canonical, platform_key
from ..profile import Profile
from ..resolve import aliases, local_date, resolve
from ..store import Store
from .urls import UnsafeAddress

# Why the judge would not show an event, for the person who gave its page.
DROPPED = {
    "past": "The event is over.",
    "online_only": "The event is online only; the radar shows events in Munich.",
    "outside_munich": "The event is not in Munich.",
    OFF_TOPIC: "The page is not about AI, startups, careers or tech.",
    AUDIENCE: "The event is for children, school pupils or families; the radar is for adults.",
}
# Words of a place that name none.
FILLER = frozenset("the and und der die das bei near von for für with mit".split())
MUNICH = "münchen"


class Refused(Exception):
    """A check refused the page, with a message for the person who gave it: `rejected`, or
    `failed` when the page could not be read."""

    def __init__(self, message: str, state: IntakeState = IntakeState.REJECTED):
        super().__init__(message)
        self.state = state


def one_event(result: ParseResult) -> ParsedEvent | None:
    """Check 4: the page's one structured event, or None when its text is the evidence."""
    if len(result.events) > 1:
        raise Refused(f"The page lists {len(result.events)} events; give the event's own page.")
    return result.events[0] if result.events else None


def check_day(day: date, fields: dict, text: str) -> None:
    """Check 5: the event's local days, start to end, hold `day`; without a stated start,
    the page text names that day and its year."""
    start = local_date(field_value(fields, "starts_at"))
    if start:
        end = max(start, local_date(field_value(fields, "ends_at")) or start)
        if not start <= day.isoformat() <= end:
            when = start if end == start else f"{start} to {end}"
            raise Refused(f"The page says the event is on {when}, not {day.isoformat()}.")
        return
    named, year_stated = names_date(day.isoformat(), text)
    if not (named and year_stated):
        raise Refused(f"The page does not name {day.day} {day:%B %Y}.")


def _place_words(text: str, cities: frozenset[str]) -> set[str]:
    return {MUNICH if w in cities else w for w in re.findall(r"[^\W_]+", text.lower())}


def check_place(place: str, fields: dict, text: str, cities: frozenset[str]) -> None:
    """Check 6: a word of the given place is on the page, in the event's venue, address or
    city or in its text, and every Munich name counts as one. One word is enough: the day
    already ties the page to the event, and the judge checks that it is in Munich."""
    given = _place_words(place, cities)
    wanted = {w for w in given if w not in FILLER and not w.isdigit()} or given
    stated = " ".join(str(field_value(fields, n) or "") for n in ("venue", "address", "city"))
    if not wanted & _place_words(f"{stated}\n{text}", cities):
        raise Refused(f"The page does not name {place}.")


def check_relevance(
    judge: Judge,
    fields: dict,
    text: str,
    needs_llm: bool,
    now: datetime,
    judgment: dict | None = None,
) -> None:
    """Check 7: the judge keeps the event as the board would, with no organiser to lend it
    a focus, by what the LLM judged its page to be about (ADR 0018). Without a judgment: the
    structured event by its fields, and a page without one by an AI or scope word; a page
    the LLM will read passes on such a word too when only its parsed fields are off topic,
    since the LLM states the event's relevance (ADR 0010)."""
    if judgment is not None:
        verdict = judge.judge(fields, now, ai_focus=False, startup_focus=False, judgment=judgment)
        if verdict.tier:
            return
        raise Refused(DROPPED[verdict.drop_reason])
    if not fields:
        if not judge.names_scope(text):
            raise Refused(DROPPED[OFF_TOPIC])
        return
    verdict = judge.judge(fields, now, ai_focus=False, startup_focus=False)
    if verdict.tier:
        return
    if verdict.drop_reason == OFF_TOPIC and needs_llm and judge.names_scope(text):
        return
    raise Refused(DROPPED[verdict.drop_reason])


def _stored(event: ParsedEvent | None) -> dict:
    """A parsed event's fields in the shape the judge reads; none for no event."""
    return {name: {"value": e.value} for name, e in event.fields.items()} if event else {}


class EventIntake:
    """Works one event intake: checking, then harvesting, then done; a refusal ends it
    `rejected`, or `failed` when the page could not be read. Every request goes through the
    fetcher's guarded client and `guard` (ADR 0003 D4). Without a router, a page that needs
    the LLM waits for the daily run."""

    def __init__(
        self,
        store: Store,
        profile: Profile,
        fetcher: Fetcher,
        guard: Callable[[str | None], None],
        router: Router | None = None,
    ):
        self.store = store
        self.profile = profile
        self.fetcher = fetcher
        self.guard = guard
        self.router = router
        self.judge = Judge(profile)
        self.log = store.intakes.log

    def process(self, row: dict) -> None:
        intake_id, url = row["id"], row["url"]
        self.store.intakes.update(intake_id, state=IntakeState.CHECKING)
        try:
            self._guard(url)
            tracked = self._tracked(url)
            # A job cut off after the check added the page already.
            resumed = tracked is not None and tracked["added_via"] == f"intake:{intake_id}"
            if tracked and not resumed:
                self._done(intake_id, self.store.events.of_source(tracked["url"]), already=True)
                return
            fetched, body = self._fetch(url)
            if not resumed:
                result = parse(url, fetched.content_type, body, Kind.EVENT_PAGE)
                event = one_event(result)
                if known := self.store.events.known(_aliases(url, event)):
                    self._done(intake_id, known, already=True)
                    return
                text = html.text(body, url)
                self._check(row, _stored(event), text, result.needs_llm, self._judgment(body, url))
        except Refused as refusal:
            self._refuse(intake_id, refusal)
            return
        self._harvest(row, fetched, body)

    def _guard(self, url: str) -> None:
        """Check 1: every address the host resolves to is public."""
        try:
            self.guard(urlsplit(url).hostname)
        except UnsafeAddress as exc:
            raise Refused(f"This address cannot be fetched: {exc}.", IntakeState.FAILED) from exc

    def _tracked(self, url: str) -> dict | None:
        """Check 2: the source that is this page, in any spelling (invariant 6)."""
        page, key = canonical(url), platform_key(url)
        return next(
            (
                s
                for s in self.store.sources.all()
                if canonical(s["url"]) == page or (key and platform_key(s["url"]) == key)
            ),
            None,
        )

    def _fetch(self, url: str) -> tuple[Fetched, bytes]:
        """Check 3: the page loads, robots.txt allows it, and it is a web page."""
        fetched = self.fetcher.get(url)
        if not fetched.ok:
            reason = (
                "its site's robots.txt does not allow the radar to read it"
                if fetched.error == "robots_disallowed"
                else fetched.error
            )
            raise Refused(f"The page did not load: {reason}.", IntakeState.FAILED)
        body = self.store.pages.body(fetched.content_hash)
        if "html" not in (fetched.content_type or "") and b"<html" not in body[:2000].lower():
            raise Refused(
                f"The address is not a web page ({fetched.content_type}).", IntakeState.FAILED
            )
        return fetched, body

    def _judgment(self, body: bytes, url: str) -> dict | None:
        """What the page's one event is about, asked at once, Groq first, as one call within
        the day's budget (ADR 0018 D8); None without an LLM, an answer, or one that could
        tell."""
        candidate = judgments.page(body, url)
        if candidate is None or self.router is None:
            return None
        key = judgments.key(candidate)
        self.store.judgments.want({key: candidate})
        try:
            Extractor(self.router, self.store).judge([key], first="groq")
        except (BudgetExhausted, DailyCapReached, AllModelsFailed, httpx.HTTPError):
            return None
        return judgments.telling(self.store.judgments.answers([key], judgments.VERSION).get(key))

    def _check(
        self, row: dict, fields: dict, text: str, needs_llm: bool, judgment: dict | None
    ) -> None:
        """Checks 5 to 7 on the page's structured event, or on its text without one."""
        if not fields and not text:
            raise Refused(
                "The page shows no text without JavaScript; give the event's ticket page instead."
            )
        check_day(row["starts_on"], fields, text)
        check_place(row["place"], fields, text, self.profile.cities)
        check_relevance(self.judge, fields, text, needs_llm, datetime.now(UTC), judgment)
        self.log(
            row["id"],
            "checking",
            "The page names the day and the place, and the event is one the radar shows.",
            "ok",
        )

    def _harvest(self, row: dict, fetched: Fetched, body: bytes) -> None:
        """Track the page as a source of its own and ingest the fetch the check read; when
        parsing fell short, have the LLM read it at once and check its reading again
        (ADR 0014 D6). Then judge the board."""
        intake_id, url = row["id"], row["url"]
        self.store.intakes.update(intake_id, state=IntakeState.HARVESTING)
        self.store.sources.add(
            url,
            Kind.EVENT_PAGE.value,
            self.profile.source_class(url).value,
            EVENT_PAGE_HOURS,
            f"intake:{intake_id}",
        )
        extractor = Extractor(self.router, self.store) if self.router else None
        harvester = Harvester(self.store, self.profile, self.fetcher, extractor=extractor)
        harvester.take(self.store.sources.get(url), fetched)
        harvester.extract([url], sort=False)
        if harvester.counts["llm_done"]:
            try:
                self._recheck(row, html.text(body, url))
            except Refused as refusal:
                self.store.intakes.drop_page(intake_id)
                self._refuse(intake_id, refusal, "harvesting")
                return
        harvester.judge_events()
        event = self.store.events.of_source(url)
        if event is not None:
            self._done(intake_id, event, already=False)
            return
        if harvester.counts["llm_deferred"]:
            why = "Today's LLM calls are used up"
        elif harvester.counts["llm_failed"]:
            why = "The LLM did not answer"
        elif extractor is None:
            why = "No LLM is configured here"
        else:
            why = "The event is not on the board now"
        self.store.intakes.update(intake_id, state=IntakeState.DONE, result={"waiting": True})
        self.log(
            intake_id,
            "done",
            f"{why}, so the LLM reads the page in the next daily run; the event shows on the"
            " board once it passes the same checks.",
            "ok",
        )

    def _recheck(self, row: dict, text: str) -> None:
        """Checks 4 to 7 again on what the page now says, the LLM's reading included."""
        groups = resolve(self.store.observations.of_url(row["url"]))
        if len(groups) != 1:
            raise Refused("The LLM found no single event on the page; give the event's own page.")
        fields = groups[0].fields
        check_day(row["starts_on"], fields, text)
        check_place(row["place"], fields, text, self.profile.cities)
        keys = [o.judgment_key for o in groups[0].ranked() if o.judgment_key]
        answers = self.store.judgments.answers(keys, judgments.VERSION)
        judgment = judgments.telling(next((answers[k] for k in keys if k in answers), None))
        check_relevance(self.judge, fields, text, False, datetime.now(UTC), judgment)

    def _done(self, intake_id: int, event: dict | None, *, already: bool) -> None:
        if event is None:
            self.store.intakes.update(intake_id, state=IntakeState.DONE, result={"already": True})
            self.log(intake_id, "done", "Already on the radar; its page shows no event now.", "ok")
            return
        title = field_value(event["fields"], "title")
        prefix = "Already on the radar" if already else "On the board"
        if event["tier"]:
            text = f"{prefix}: {title}, tier {event['tier']}."
        else:
            text = f"{prefix}: {title}, not shown. {DROPPED.get(event['drop_reason'], '')}"
        result = {
            "event_key": event["key"],
            "title": title,
            "tier": event["tier"],
            "drop_reason": event["drop_reason"],
            "already": already,
        }
        self.store.intakes.update(intake_id, state=IntakeState.DONE, result=result)
        self.log(intake_id, "done", text.strip(), "ok")

    def _refuse(self, intake_id: int, refusal: Refused, step: str = "checking") -> None:
        """End the intake with the refusal, logged under the step that refused it."""
        failed = refusal.state is IntakeState.FAILED
        self.log(
            intake_id, "error" if failed else step, str(refusal), "error" if failed else "warn"
        )
        self.store.intakes.update(intake_id, state=refusal.state, error=str(refusal))


def _aliases(url: str, event: ParsedEvent | None) -> set[str]:
    """The keys the board would know the page's event by: the structured event's, or the
    page's own, as an extraction would give it."""
    if event is not None:
        return aliases(url, event.platform_keys, event.fields)
    keys = [k for k in (platform_key(url),) if k]
    return aliases(url, keys, {"event_url": Evidence(url)})
