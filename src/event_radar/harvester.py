"""Harvesting (ADR 0002): fetch the due sources, ask the LLM about the pages parsing could
not read and what each event is about (ADR 0018), and judge the events that all
observations describe."""

from collections import Counter
from datetime import UTC, datetime

import httpx

from .extract import Extractor, judgments
from .extract.router import AllModelsFailed, BudgetExhausted, DailyCapReached
from .fetch import Fetched, Fetcher
from .judge import Judge
from .model import NOT_STORED, JobState, JudgedEvent, Kind, Observation, ParsedEvent, SourceClass
from .parse import parse
from .pipeline import ingest
from .profile import Profile
from .render import Renderer
from .resolve import Resolved, resolve
from .store import Store

EVENT_PAGE_HOURS = 72
LISTING_HOURS = 24
# Event pages of A and B events are fetched daily.
HOT_HOURS = 24
LLM_ATTEMPTS = 3
LLM_BATCH = 100


def cadence_hours(kind: str) -> int:
    return EVENT_PAGE_HOURS if kind == Kind.EVENT_PAGE else LISTING_HOURS


def _follow_kind(url: str) -> Kind:
    return Kind.FEED if "/ics" in url or url.endswith((".ics", "/ical/")) else Kind.EVENT_PAGE


class Harvester:
    """One job's harvest. Without a renderer it skips browser sources; without an
    extractor, pages wait in the LLM queue. `counts` tallies what happened."""

    def __init__(
        self,
        store: Store,
        profile: Profile,
        fetcher: Fetcher,
        renderer: Renderer | None = None,
        extractor: Extractor | None = None,
    ):
        self.store = store
        self.profile = profile
        self.fetcher = fetcher
        self.renderer = renderer
        self.extractor = extractor
        self.judge = Judge(profile)
        self.counts: Counter = Counter()

    def harvest_due(self) -> None:
        """Every source whose cadence has passed: the browser sources when this harvester
        renders, the others when it does not."""
        for source in self.store.sources.due(browser=self.renderer is not None):
            self.harvest(source)

    def harvest(self, source: dict) -> None:
        """Fetch one source, or render it when it is a browser source, and ingest it. The
        event pages it links to become sources of the same organiser; a source without one,
        an added event's page, adds none (ADR 0014 D4)."""
        if source["browser"]:
            if self.renderer is None:
                return
            fetched = self.renderer.fetch(source["url"])
        else:
            fetched = self.fetcher.get(source["url"])
        self.take(source, fetched)

    def take(self, source: dict, fetched: Fetched) -> None:
        """Ingest one fetch of `source`, as `harvest` does after fetching it."""
        if not fetched.ok:
            self.store.sources.mark(source["id"], False, fetched.error, None)
            self.counts["fetch_errors"] += 1
            return
        result = ingest(
            self.store,
            source["url"],
            fetched.content_hash,
            fetched.content_type,
            self.store.pages.body(fetched.content_hash),
            Kind(source["kind"]),
            SourceClass(source["source_class"]),
            base=fetched.final_url,
        )
        self.store.sources.mark(source["id"], True, None, len(result.events) + len(result.follow))
        self.counts["fetched"] += 1
        self.counts["observed"] += len(result.events)
        if source["organiser_id"] is None:
            return
        for link in result.follow:
            self.store.sources.add(
                link,
                _follow_kind(link).value,
                self.profile.source_class(link).value,
                EVENT_PAGE_HOURS,
                f"link:{source['url']}",
                source["organiser_id"],
            )

    def extract(
        self,
        urls: list[str] | None = None,
        until: datetime | None = None,
        *,
        sort: bool = True,
    ) -> None:
        """Work the LLM queue, or only the pages of `urls`, until the budget runs out or
        `until` passes; jobs for a page's older versions are dropped first. Without `sort`
        every page is read, with no relevance question first (an added event, ADR 0014 D6).
        A page without an organiser holds one event: read as several, it keeps none
        (ADR 0014 D4)."""
        if self.extractor is None:
            return
        self.counts["llm_superseded"] += self.store.extraction.drop_superseded()
        try:
            jobs = self.store.extraction.pending(LLM_BATCH, urls)
            if sort:
                jobs = self._worth_extracting(jobs)
        except (BudgetExhausted, DailyCapReached):
            self.counts["llm_deferred"] += 1
            return
        except (AllModelsFailed, httpx.HTTPError):
            self.counts["llm_failed"] += 1
            return
        for job in jobs:
            if until and datetime.now(UTC) >= until:
                self.counts["llm_deferred"] += 1
                return
            url, digest = job["url"], job["snapshot_hash"]
            source = self.store.sources.get(url)
            event_page = bool(source and source["kind"] == Kind.EVENT_PAGE)
            body = self.store.pages.body(digest)
            try:
                method, events = self.extractor.extract(url, body, event_page)
            except (BudgetExhausted, DailyCapReached):
                self.counts["llm_deferred"] += 1
                return
            except (AllModelsFailed, httpx.HTTPError):
                gave_up = job["attempts"] + 1 >= LLM_ATTEMPTS
                state = JobState.NEEDS_LOOK if gave_up else JobState.PENDING
                self.store.extraction.finish(digest, url, state)
                self.counts["llm_failed"] += 1
                continue
            if len(events) > 1 and source and source["organiser_id"] is None:
                events = []
                self.counts["llm_several"] += 1
            candidates = judgments.candidates(events, body, url, event_page)
            self.store.observations.add(
                digest, url, self.profile.source_class(url), method, events, candidates
            )
            self.store.extraction.finish(digest, url, JobState.DONE)
            self.counts["llm_done"] += 1

    def _worth_extracting(self, jobs: list[dict]) -> list[dict]:
        """The jobs worth an extraction call (ADR 0010, ADR 0018 D7): the judge skips the
        events it drops for their audience or a fact, whatever their topic, an AI-focused or
        startup organiser's pages need no judgment, and the others are extracted unless the
        judgment of their page puts them out of scope. One left unanswered, or whose page says
        too little to tell, is extracted. The
        skipped become `skipped`."""
        now = datetime.now(UTC)
        focused = self.store.sources.focused_urls()
        startups = self.store.sources.startup_urls()
        asked: dict[tuple[str, str], str] = {}
        candidates: dict[str, dict] = {}
        kept: set[tuple[str, str]] = set()
        for job in jobs:
            ident = (job["snapshot_hash"], job["url"])
            body = self.store.pages.body(ident[0])
            fields = _one_event(job["url"], body)
            if fields and (self.judge.audience(fields)[0] or self.judge.fact_drop(fields, now)):
                self.store.extraction.skip(*ident)
                self.counts["llm_skipped"] += 1
            elif job["url"] in focused or job["url"] in startups:
                kept.add(ident)
            elif candidate := judgments.page(body, job["url"]):
                asked[ident] = judgments.key(candidate)
                candidates[asked[ident]] = candidate
            else:
                kept.add(ident)
        self.store.judgments.want(candidates)
        self.counts["judged"] += self.extractor.judge(candidates)
        answers = self.store.judgments.answers(candidates, judgments.VERSION)
        for ident, key in asked.items():
            judgment = judgments.telling(answers.get(key))
            verdict = judgment and self.judge.judge(
                {}, now, ai_focus=False, startup_focus=False, judgment=judgment
            )
            if verdict and verdict.drop_reason in NOT_STORED:
                self.store.extraction.skip(*ident)
                self.counts["llm_unrelated"] += 1
            else:
                kept.add(ident)
        return [j for j in jobs if (j["snapshot_hash"], j["url"]) in kept]

    def ask_judgments(self, urls: list[str] | None = None, until: datetime | None = None) -> None:
        """Ask what each event is about (ADR 0018), of every event no fact drops, or only of
        the events of `urls`, by the candidate of its best observation; outside the judge
        lock, so an added event never waits for the harvest's calls, and no batch starts
        after `until`. An observation made before judgments were asked gets its candidate
        first."""
        if self.extractor is None:
            return
        if urls is None:
            observations = self.store.observations.all()
        else:
            observations = [o for url in urls for o in self.store.observations.of_url(url)]
        now = datetime.now(UTC)
        owed = [g for g in resolve(observations) if self._owes_judgment(g, now)]
        unnamed = {_parse(o) for g in owed for o in g.observations if not o.judgment_key}
        self._name_candidates([o for o in observations if _parse(o) in unnamed])
        keys = [keys[0] for g in owed if (keys := _judgment_keys(g))]
        try:
            self.counts["judged"] += self.extractor.judge(keys, until=until)
        except (BudgetExhausted, DailyCapReached):
            self.counts["judgments_deferred"] += 1
        except (AllModelsFailed, httpx.HTTPError):
            self.counts["judgments_failed"] += 1

    def _owes_judgment(self, group: Resolved, now: datetime) -> bool:
        """Whether what the event is about decides it: no title phrase names another
        audience and no fact drops it (ADR 0018 invariant 6)."""
        fields = group.fields
        return not (self.judge.audience(fields)[0] or self.judge.fact_drop(fields, now))

    def _name_candidates(self, observations: list[Observation]) -> None:
        """Give the events of parses made before judgments were asked their candidates, from
        the page they read, or from their fields once its body is gone; `observations`
        holds every item of those parses."""
        if not observations:
            return
        kinds = {s["url"]: s["kind"] for s in self.store.sources.all()}
        parses: dict[tuple[str, str, str], list[Observation]] = {}
        for obs in sorted(observations, key=lambda o: o.id):
            parses.setdefault(_parse(obs), []).append(obs)
        named = {}
        for (digest, url, _), items in parses.items():
            try:
                body = self.store.pages.body(digest)
            except KeyError:
                body = b""
            events = [ParsedEvent(o.fields) for o in items]
            found = judgments.candidates(events, body, url, kinds.get(url) == Kind.EVENT_PAGE)
            for obs, candidate in zip(items, found, strict=True):
                if candidate:
                    obs.judgment_key = judgments.key(candidate)
                    named[obs.id] = (obs.judgment_key, candidate)
        self.store.observations.name_candidates(named)

    def judge_events(self) -> None:
        """Rewrite the events from all observations and the judgments asked of them; one job
        at a time (ADR 0006 D4). An event whose candidate is not answered yet is judged by
        the judgment it was last judged by, then by an answer to an older version of the
        question, or by its words without one or with one that could not tell (ADR 0018 D6)."""
        with self.store.lock("judge"):
            now = datetime.now(UTC)
            focused = self.store.sources.focused_urls()
            startups = self.store.sources.startup_urls()
            judged, hot = [], set()
            groups = resolve(self.store.observations.all())
            stars = self.store.events.star_counts([g.aliases for g in groups])
            keys = [_judgment_keys(g) for g in groups]
            previous = self.store.events.judgment_keys([g.aliases for g in groups])
            wanted = {k for each in keys for k in each} | set(filter(None, previous))
            answers = self.store.judgments.answers(wanted, judgments.VERSION)
            held = self.store.judgments.answers(wanted)
            for group, starred, candidates, last in zip(groups, stars, keys, previous, strict=True):
                urls = {o.url for o in group.observations}
                # An answer to an older question holds until the current one is answered.
                used = next((k for k in [*candidates, last] if k in answers), None) or next(
                    (k for k in [*candidates, last] if k in held), None
                )
                judgment = judgments.telling(held.get(used))
                verdict = self.judge.judge(
                    group.fields,
                    now,
                    ai_focus=bool(urls & focused),
                    startup_focus=bool(urls & startups),
                    stars=starred,
                    judgment=judgment,
                )
                judged.append(
                    JudgedEvent(
                        group.aliases,
                        group.fields,
                        group.merge_basis,
                        verdict,
                        sorted(urls),
                        used,
                    )
                )
                self.counts[f"tier_{verdict.tier or verdict.drop_reason}"] += 1
                owed = bool(candidates) and candidates[0] not in answers
                self.counts["judgment_pending"] += owed and self._owes_judgment(group, now)
                if verdict.tier in {"A", "B"}:
                    hot |= urls
            if hot:
                self.store.sources.set_cadence(sorted(hot), HOT_HOURS)
            self.counts.update(self.store.events.save(judged))


def _parse(obs: Observation) -> tuple[str, str, str]:
    """The parse an observation is one item of."""
    return obs.snapshot_hash, obs.url, obs.method


def _judgment_keys(group: Resolved) -> list[str]:
    """The candidates an event is judged by, its best observation's first (ADR 0018)."""
    return [o.judgment_key for o in group.ranked() if o.judgment_key]


def _one_event(url: str, body: bytes) -> dict | None:
    """The fields of a page's one structured event, in the judge's form."""
    result = parse(url, None, body, Kind.EVENT_PAGE)
    if len(result.events) == 1 and "title" in result.events[0].fields:
        return {k: {"value": v.value} for k, v in result.events[0].fields.items()}
    return None
