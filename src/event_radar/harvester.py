"""Harvesting (ADR 0002): fetch the due sources, ask the LLM about the pages parsing could
not read, and judge the events that all observations describe."""

from collections import Counter
from datetime import UTC, datetime

import httpx

from .extract import Extractor, triage
from .extract.router import AllModelsFailed, BudgetExhausted, DailyCapReached
from .fetch import Fetcher
from .judge import Judge
from .model import OFF_TOPIC, JobState, JudgedEvent, Kind, SourceClass
from .pipeline import ingest
from .profile import Profile
from .render import Renderer
from .resolve import resolve
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
        event pages it links to become sources of the same organiser."""
        if source["browser"]:
            if self.renderer is None:
                return
            fetched = self.renderer.fetch(source["url"])
        else:
            fetched = self.fetcher.get(source["url"])
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
        for link in result.follow:
            self.store.sources.add(
                link,
                _follow_kind(link).value,
                self.profile.source_class(link).value,
                EVENT_PAGE_HOURS,
                f"link:{source['url']}",
                source["organiser_id"],
            )

    def extract(self, urls: list[str] | None = None, until: datetime | None = None) -> None:
        """Work the LLM queue, or only the pages of `urls`, until the budget runs out or
        `until` passes; jobs for a page's older versions are dropped first."""
        if self.extractor is None:
            return
        self.counts["llm_superseded"] += self.store.extraction.drop_superseded()
        try:
            jobs = self._worth_extracting(self.store.extraction.pending(LLM_BATCH, urls))
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
            try:
                method, events = self.extractor.extract(
                    url, self.store.pages.body(digest), event_page
                )
            except (BudgetExhausted, DailyCapReached):
                self.counts["llm_deferred"] += 1
                return
            except (AllModelsFailed, httpx.HTTPError):
                gave_up = job["attempts"] + 1 >= LLM_ATTEMPTS
                state = JobState.NEEDS_LOOK if gave_up else JobState.PENDING
                self.store.extraction.finish(digest, url, state)
                self.counts["llm_failed"] += 1
                continue
            self.store.observations.add(digest, url, self.profile.source_class(url), method, events)
            self.store.extraction.finish(digest, url, JobState.DONE)
            self.counts["llm_done"] += 1

    def _worth_extracting(self, jobs: list[dict]) -> list[dict]:
        """The jobs worth an extraction call (ADR 0010): the judge skips the events it
        drops for a fact, an AI-focused or startup organiser's pages need no question, and
        the LLM sorts the rest in batches, off topic or not (ADR 0011). The others become
        `skipped`."""
        now = datetime.now(UTC)
        focused = self.store.sources.focused_urls()
        startups = self.store.sources.startup_urls()
        asked: dict[tuple[str, str], str] = {}
        candidates: dict[str, dict] = {}
        kept: set[tuple[str, str]] = set()
        for job in jobs:
            ident = (job["snapshot_hash"], job["url"])
            candidate, fields = triage.candidate(job["url"], self.store.pages.body(ident[0]))
            ai_focus, startup_focus = job["url"] in focused, job["url"] in startups
            drop = (
                fields
                and self.judge.judge(
                    fields, now, ai_focus=ai_focus, startup_focus=startup_focus
                ).drop_reason
            )
            if drop and drop != OFF_TOPIC:
                self.store.extraction.skip(*ident)
                self.counts["llm_skipped"] += 1
            elif ai_focus or startup_focus:
                kept.add(ident)
            else:
                asked[ident] = triage.key(candidate)
                candidates.setdefault(asked[ident], candidate)
        verdicts = (
            self.extractor.relevant(candidates, self.profile.any_topic_kinds) if candidates else {}
        )
        for ident, key in asked.items():
            if verdicts.get(key, True):
                kept.add(ident)
            else:
                self.store.extraction.skip(*ident)
                self.counts["llm_unrelated"] += 1
        return [j for j in jobs if (j["snapshot_hash"], j["url"]) in kept]

    def judge_events(self) -> None:
        """Rewrite the events from all observations; one job at a time (ADR 0006 D4)."""
        with self.store.lock("judge"):
            now = datetime.now(UTC)
            focused = self.store.sources.focused_urls()
            startups = self.store.sources.startup_urls()
            judged = []
            for group in resolve(self.store.observations.all()):
                urls = {o.url for o in group.observations}
                verdict = self.judge.judge(
                    group.fields,
                    now,
                    ai_focus=bool(urls & focused),
                    startup_focus=bool(urls & startups),
                )
                judged.append(
                    JudgedEvent(
                        group.aliases, group.fields, group.merge_basis, verdict, sorted(urls)
                    )
                )
                self.counts[f"tier_{verdict.tier or verdict.drop_reason}"] += 1
                if verdict.tier in {"A", "B"}:
                    self.store.sources.set_cadence(sorted(urls), HOT_HOURS)
            self.counts.update(self.store.events.save(judged))
