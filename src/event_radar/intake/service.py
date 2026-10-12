"""The intake state machine (ADR 0003): queued, checking, profiling, finding, harvesting,
then done; review waits for a person, rejected and failed end it."""

from collections import Counter
from collections.abc import Callable
from urllib.parse import urlsplit

import httpx
from selectolax.lexbor import LexborHTMLParser

from .. import config
from ..extract import Extractor
from ..extract.router import AllModelsFailed, BudgetExhausted, DailyCapReached, Router
from ..fetch import Fetcher, Politeness
from ..harvester import Harvester, cadence_hours
from ..model import IntakeState, OrganiserStatus
from ..parse import html
from ..platforms import canonical
from ..profile import Profile
from ..render import Renderer
from ..store import Store
from . import discovery, profiling
from .discovery import Candidate
from .urls import UnsafeAddress, check_host, guarded_client, organiser_domain

MAX_CANDIDATES = 6
MAX_FOLLOW = 30


class IntakeError(Exception):
    """A step failed with a message the person should read."""


class IntakeService:
    """Works the queued intakes. Without a router nobody profiles a new organisation, so
    it waits for a person's review; without a renderer, JavaScript-only pages yield
    nothing. Every request goes through `open_client` and `guard`, which refuse
    non-public addresses (ADR 0003 D4)."""

    def __init__(
        self,
        store: Store,
        profile: Profile,
        router: Router | None = None,
        *,
        open_client: Callable[[str], httpx.Client] = guarded_client,
        guard: Callable[[str | None], None] = check_host,
        renderer: Callable[..., Renderer] | None = None,
        min_interval_s: float = 5.0,
    ):
        self.store = store
        self.profile = profile
        self.router = router
        self.open_client = open_client
        self.guard = guard
        self.renderer = renderer
        self.min_interval_s = min_interval_s
        self.log = store.intakes.log

    def drain(self) -> int:
        """Work every queued intake, then return how many (ADR 0006 D3). One drainer per
        store at a time, so an intake cut off mid-step by a killed job is queued again
        first."""
        worked = 0
        with self.store.lock("intake"):
            self.store.intakes.requeue_interrupted()
            while (row := self.store.intakes.next_queued()) is not None:
                self.process(row["id"])
                worked += 1
        return worked

    def process(self, intake_id: int) -> None:
        user_agent = config.user_agent()
        client = self.open_client(user_agent)
        fetcher = Fetcher(self.store, user_agent, self.min_interval_s, client=client)
        renderer = self._renderer(fetcher.politeness)
        try:
            row = self.store.intakes.get(intake_id)
            if row["organiser_id"]:
                self._harvest(intake_id, row["organiser_id"], fetcher, renderer)
                return
            home, body = self._check(intake_id, fetcher)
            found = self._profiling(intake_id, home, body, fetcher)
            if found is None:
                return
            organiser_id = self._finding(intake_id, home, body, found, fetcher, renderer)
            self._harvest(intake_id, organiser_id, fetcher, renderer)
        except IntakeError as exc:
            self._fail(intake_id, str(exc))
        except Exception as exc:
            self._fail(intake_id, f"Unexpected error: {type(exc).__name__}: {exc}"[:500])
        finally:
            if renderer:
                renderer.close()

    def _renderer(self, politeness: Politeness) -> Renderer | None:
        return self.renderer(politeness, guard=self.guard) if self.renderer else None

    def _fail(self, intake_id: int, message: str) -> None:
        self.log(intake_id, "error", message, "error")
        self.store.intakes.update(intake_id, state=IntakeState.FAILED, error=message)

    def _check(self, intake_id: int, fetcher: Fetcher) -> tuple[str, bytes]:
        """The homepage, once it is public, new to the registry, and a web page."""
        url = self.store.intakes.get(intake_id)["url"]
        self.store.intakes.update(intake_id, state=IntakeState.CHECKING)
        try:
            self.guard(urlsplit(url).hostname)
        except UnsafeAddress as exc:
            raise IntakeError(f"This address cannot be fetched: {exc}.") from exc
        known = self.store.organisers.by_domain(organiser_domain(url))
        if known:
            raise IntakeError(f"Already in the registry as {known['name']} ({known['status']}).")
        fetched = fetcher.get(url)
        if not fetched.ok:
            raise IntakeError(f"The homepage did not load: {fetched.error}.")
        body = self.store.pages.body(fetched.content_hash)
        if "html" not in (fetched.content_type or "") and b"<html" not in body[:2000].lower():
            raise IntakeError(f"The homepage is not a web page ({fetched.content_type}).")
        self.log(
            intake_id,
            "checking",
            f"The homepage answers (HTTP {fetched.status}, {len(body) // 1024} KB) and may be"
            " fetched.",
            "ok",
        )
        return url, body

    def _profiling(self, intake_id: int, home: str, body: bytes, fetcher: Fetcher) -> dict | None:
        """The organisation's profile, or None when the intake now waits for a person or
        was rejected. An approved intake skips the LLM."""
        row = self.store.intakes.get(intake_id)
        if row["approved"]:
            return row["profile"] or {}
        self.store.intakes.update(intake_id, state=IntakeState.PROFILING)
        if self.router is None:
            self.log(
                intake_id,
                "profiling",
                "No LLM key is configured, so nobody checked who this"
                " organisation is. Approve it if you know it.",
                "warn",
            )
            self.store.intakes.update(intake_id, state=IntakeState.REVIEW)
            return None
        anchors = html.anchors(LexborHTMLParser(body), home)
        about = profiling.about_page(anchors, home, self.profile.about_signals)
        about_text = ""
        if about:
            fetched = fetcher.get(about)
            if fetched.ok:
                about_text = html.text(self.store.pages.body(fetched.content_hash), about, 4000)
        try:
            found = profiling.ask(self.router, home, row["name"], body, anchors, about, about_text)
        except (BudgetExhausted, DailyCapReached, AllModelsFailed, httpx.HTTPError) as exc:
            self.log(
                intake_id,
                "profiling",
                f"The LLM could not be asked ({type(exc).__name__})."
                " Approve it if you know the organisation.",
                "warn",
            )
            self.store.intakes.update(intake_id, state=IntakeState.REVIEW)
            return None
        self.store.intakes.update(intake_id, profile=found)
        legitimate, runs_events, in_munich = (
            found["legitimate"],
            found["runs_events"],
            found["based_in_munich"],
        )
        self.log(intake_id, "profiling", f"{found['name']}: {found['summary']}", "info")
        if legitimate == "yes" and "no" not in (runs_events, in_munich):
            self.log(
                intake_id, "profiling", "Looks like a real organisation that runs events.", "ok"
            )
            return found
        if legitimate == "no":
            self.log(intake_id, "profiling", "The LLM judged this not a genuine organiser.", "warn")
            self.store.intakes.update(intake_id, state=IntakeState.REJECTED)
        else:
            self.log(intake_id, "profiling", "The LLM was not sure. Your call.", "warn")
            self.store.intakes.update(intake_id, state=IntakeState.REVIEW)
        return None

    def _finding(
        self,
        intake_id: int,
        home: str,
        body: bytes,
        found: dict,
        fetcher: Fetcher,
        renderer: Renderer | None,
    ) -> int:
        """Probe the candidate event pages, then admit the organisation with those that
        yield events; returns its id."""
        row = self.store.intakes.get(intake_id)
        self.store.intakes.update(intake_id, state=IntakeState.FINDING)
        unique = discovery.candidates(
            home,
            body,
            row["events_url"],
            found.get("event_links") or [],
            self.profile.link_signals,
        )
        self.log(
            intake_id,
            "finding",
            f"{len(unique)} candidate event pages; checking up to {MAX_CANDIDATES}.",
            "info",
        )
        kept = self._probe_all(intake_id, unique[:MAX_CANDIDATES], fetcher, renderer)
        if not kept:
            raise IntakeError(
                "No page with events was found. Give the events page directly and try again."
            )
        domain = organiser_domain(home)
        title = discovery.page_title(LexborHTMLParser(body))
        organiser_id = self.store.organisers.add(
            row["name"] or found.get("name") or title or domain,
            domain,
            "manual",
            homepage=home,
            kind=found.get("kind") if found.get("kind") != "other" else None,
            ai_focus=bool(found.get("ai_focus")),
            discovery_url=home,
        )
        if not organiser_id:
            raise IntakeError("This domain was removed from the registry before.")
        for c in kept:
            self.store.sources.add(
                c.url,
                c.kind,
                self.profile.source_class(c.url).value,
                cadence_hours(c.kind),
                "intake",
                organiser_id,
                c.browser,
            )
        self.store.intakes.update(intake_id, organiser_id=organiser_id)
        return organiser_id

    def _probe_all(
        self,
        intake_id: int,
        candidates: list[Candidate],
        fetcher: Fetcher,
        renderer: Renderer | None,
    ) -> list[Candidate]:
        kept: dict[str, Candidate] = {}
        for candidate in candidates:
            # A rendered page can land on another candidate, e.g. /events on its Luma page.
            if canonical(candidate.url) in kept:
                self.log(intake_id, "finding", f"{candidate.url}: already found ({candidate.why}).")
                continue
            source = discovery.probe(candidate, self.store, fetcher, renderer)
            if source is None:
                self.log(
                    intake_id, "finding", f"{candidate.url}: nothing usable ({candidate.why})."
                )
                continue
            if canonical(source.url) in kept:
                self.log(
                    intake_id, "finding", f"{candidate.url} leads to {source.url}, already found."
                )
                continue
            kept[canonical(source.url)] = source
            how = " after rendering it" if source.browser else ""
            self.log(
                intake_id,
                "finding",
                f"{source.url}: {source.events} events, {source.links} event"
                f" links{how} ({source.why}).",
                "ok",
            )
        return list(kept.values())

    def _harvest(
        self, intake_id: int, organiser_id: int, fetcher: Fetcher, renderer: Renderer | None
    ) -> None:
        """Harvest the organisation's sources and the event pages they link to, then
        record what was found; it is active once it has an upcoming event."""
        self.store.intakes.update(intake_id, state=IntakeState.HARVESTING)
        extractor = Extractor(self.router, self.store) if self.router else None
        harvester = Harvester(self.store, self.profile, fetcher, renderer, extractor)
        for source in self.store.sources.of_organiser(organiser_id):
            if not source["added_via"].startswith("link:"):
                harvester.harvest(source)
        follow = [
            s
            for s in self.store.sources.of_organiser(organiser_id)
            if s["added_via"].startswith("link:") and not s["last_fetched_at"]
        ][:MAX_FOLLOW]
        if follow:
            self.log(intake_id, "harvesting", f"Reading {len(follow)} event pages.", "info")
        for source in follow:
            harvester.harvest(source)
        urls = [s["url"] for s in self.store.sources.of_organiser(organiser_id)]
        if extractor and self.store.extraction.pending(1, urls):
            self.log(
                intake_id,
                "harvesting",
                "Asking the LLM for registration details on pages without structured data.",
                "info",
            )
            harvester.extract(urls)
        harvester.judge_events()
        tally = self._tally(set(urls))
        status = OrganiserStatus.ACTIVE if tally["upcoming"] else OrganiserStatus.DORMANT
        self.store.organisers.set_status(organiser_id, status)
        result = {
            "organiser_id": organiser_id,
            "status": status,
            "pages_read": harvester.counts["fetched"],
            "fetch_errors": harvester.counts["fetch_errors"],
            **tally,
        }
        self.store.intakes.update(intake_id, state=IntakeState.DONE, result=result)
        summary = (
            f"{tally['upcoming']} upcoming events (A {tally['A']}, B {tally['B']}, C {tally['C']}),"
            f" {tally['past']} past, {tally['dropped']} online or outside Munich."
        )
        if status is OrganiserStatus.DORMANT:
            summary += " No upcoming event yet, so it is dormant and rechecked on its schedule."
        self.log(intake_id, "done", summary, "ok")

    def _tally(self, urls: set[str]) -> dict:
        tally = Counter({"A": 0, "B": 0, "C": 0, "upcoming": 0, "past": 0, "dropped": 0})
        for event in self.store.events.all():
            if not urls & set(event["sources"]):
                continue
            if event["tier"]:
                tally[event["tier"]] += 1
                tally["upcoming"] += 1
            elif event["drop_reason"] == "past":
                tally["past"] += 1
            else:
                tally["dropped"] += 1
        return dict(tally)
