"""Organisation intake (ADR 0003): check, profile, find sources, harvest, admit."""

import ipaddress
import re
import socket
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from urllib.parse import urljoin, urlsplit

import httpx
from selectolax.lexbor import LexborHTMLParser

from . import config, render
from .extract import Extractor
from .extract.router import AllModelsFailed, BudgetExhausted, DailyCapReached, Router
from .fetch import Fetcher
from .judge import Judge
from .model import Kind
from .parse import html, luma, parse
from .platforms import canonical, is_ticket_host, platform_key, source_class
from .runner import (
    EVENT_PAGE_HOURS,
    _profile,
    extract,
    harvest_one,
    judge_events,
    single_flight,
)
from .snapshots import SnapshotStore
from .store import Store

MAX_CANDIDATES = 6
# The worker's fallback when a notification is lost (ADR 0005).
POLL_S = 30.0
MAX_FOLLOW = 30
SHELL_TEXT = 300
SHELL_LINKS = 5
DECIDABLE = {"review", "rejected"}
KINDS = ["academic", "community", "industry", "startup", "student", "other"]
_LANG = re.compile(r"^[a-z]{2}(-[a-z]{2})?$")
_WORD = re.compile(r"[\wäöüß]+")
_FEED_TYPES = ("calendar", "rss", "atom")

PROFILE_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "The organisation's name as the site gives it"},
        "kind": {"type": "string", "enum": KINDS},
        "summary": {"type": "string", "description": "One or two sentences: who they are"},
        "based_in_munich": {"type": "string", "enum": ["yes", "no", "unclear"]},
        "runs_events": {"type": "string", "enum": ["yes", "no", "unclear"]},
        "ai_focus": {"type": "boolean", "description": "AI, ML or data is its main topic"},
        "legitimate": {"type": "string", "enum": ["yes", "no", "unclear"]},
        "reasons": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Two to four short sentences citing what on the pages supports it",
        },
        "event_links": {
            "type": "array",
            "items": {"type": "string"},
            "description": "URLs from the link list that likely list this organisation's events",
        },
    },
    "required": [
        "name",
        "kind",
        "summary",
        "based_in_munich",
        "runs_events",
        "ai_focus",
        "legitimate",
        "reasons",
        "event_links",
    ],
    "additionalProperties": False,
}

PROFILE_SYSTEM = """You profile an organisation from its own website for a tracker of AI,
tech and startup events in Munich. Use only the text given; do not rely on memory.
- legitimate: "yes" if this is a genuine organisation's own site (a club, community,
  institute, company, programme or conference). "no" for parked domains, spam, shops,
  link farms, or sites that only aggregate other organisers' events. "unclear" otherwise.
- runs_events: does the organisation itself host events (talks, meetups, workshops,
  hackathons, conferences, programmes)?
- based_in_munich: is it based in or does it hold events in Munich or its surroundings?
- reasons: cite the page, e.g. an address, an events section, named past events.
- event_links: copy URLs exactly from the link list; never invent one.
Today is TODAY. Return JSON only."""


class IntakeError(Exception):
    """A step failed with a message the person should read."""


class UnsafeAddress(httpx.HTTPError):
    pass


def normalise_url(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        raise IntakeError("Enter the organisation's website.")
    if "://" not in text:
        text = f"https://{text}"
    parts = urlsplit(text)
    if parts.scheme not in {"http", "https"} or not parts.hostname or "." not in parts.hostname:
        raise IntakeError("That is not a web address (http or https).")
    return text.split("#")[0]


def organiser_domain(url: str) -> str:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower().removeprefix("www.")
    host = "luma.com" if host == "lu.ma" else host
    segments = [s for s in parts.path.split("/") if s]
    if is_ticket_host(url):
        depth = 2 if segments[:1] == ["user"] else 1
        return "/".join([host, *segments[:depth]])
    if segments and _LANG.match(segments[0].lower()):
        segments = segments[1:]
    return f"{host}/{segments[0]}" if segments else host


def check_host(host: str | None) -> None:
    """ADR 0003 D4: refuse hosts that resolve to anything but public addresses."""
    if not host:
        raise UnsafeAddress("no host")
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise UnsafeAddress(f"{host} does not resolve") from exc
    for info in infos:
        address = ipaddress.ip_address(info[4][0].split("%")[0])
        if not address.is_global or address.is_multicast:
            raise UnsafeAddress(f"{host} resolves to a non-public address ({address})")


def _guard_request(request: httpx.Request) -> None:
    check_host(request.url.host)


def guarded_client(user_agent: str) -> httpx.Client:
    """Every request, redirects included, passes the address check."""
    return httpx.Client(
        headers={"User-Agent": user_agent},
        follow_redirects=True,
        timeout=15.0,
        event_hooks={"request": [_guard_request]},
    )


def _words(text: str) -> set[str]:
    return set(_WORD.findall(text.lower()))


def _matches(text: str, signals: list[str]) -> bool:
    words = _words(text)
    lowered = f" {text.lower()} "
    return any(f" {s} " in lowered if " " in s else s in words for s in signals)


def _anchors(tree: LexborHTMLParser, base: str) -> list[tuple[str, str]]:
    found = {}
    for anchor in tree.css("a[href]"):
        href = (anchor.attributes.get("href") or "").strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        url = urljoin(base, href.replace("webcal://", "https://").split("#")[0])
        if url.startswith("http"):
            found.setdefault(url, anchor.text(strip=True))
    return list(found.items())


def platform_source(url: str) -> str | None:
    """A ticket-platform organiser page, as the URL to track, or None."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower().removeprefix("www.")
    segments = [s for s in parts.path.split("/") if s]
    if not segments:
        return None
    if host in {"luma.com", "lu.ma"}:
        depth = 2 if segments[0] == "user" else 1
        return f"https://luma.com/{'/'.join(segments[:depth])}"
    if host == "meetup.com" and len(segments) <= 2 and segments[1:] in ([], ["events"]):
        return f"https://www.meetup.com/{segments[0]}/events/ical/"
    if host == "pretix.eu" and len(segments) == 1:
        return f"https://pretix.eu/{segments[0]}/"
    if host.startswith("eventbrite.") and segments[0] == "o" and len(segments) == 2:
        return url
    return None


def declared_feeds(tree: LexborHTMLParser, base: str) -> list[str]:
    feeds = []
    for link in tree.css("link[rel~='alternate'][href]"):
        kind = (link.attributes.get("type") or "").lower()
        if any(t in kind for t in _FEED_TYPES):
            feeds.append(urljoin(base, link.attributes["href"]))
    feeds += [url for url, _ in _anchors(tree, base) if urlsplit(url).path.endswith(".ics")]
    if feed := luma.calendar_feed(tree):
        feeds.append(feed)
    return feeds


def scan_links(tree: LexborHTMLParser, base: str, signals: list[str]) -> list[str]:
    """Anchors that carry an event signal in their text or path, and platform pages."""
    host = urlsplit(base).hostname
    found = []
    for url, label in _anchors(tree, base):
        if platform := platform_source(url):
            found.append(platform)
            continue
        parts = urlsplit(url)
        if parts.hostname != host or url.rstrip("/") == base.rstrip("/"):
            continue
        segments = [s.lower() for s in parts.path.split("/") if s]
        if _matches(label, signals) or any(s in signals for s in segments):
            found.append(url)
    return list(dict.fromkeys(found))


def is_shell(body: bytes, url: str) -> bool:
    """Plain HTML that only renders its content with JavaScript."""
    tree = LexborHTMLParser(body)
    return len(html.text(body, url)) < SHELL_TEXT or len(_anchors(tree, url)) < SHELL_LINKS


@dataclass
class Candidate:
    url: str
    why: str
    kind: str = "listing"
    browser: bool = False
    events: int = 0
    links: int = 0


class IntakeService:
    def __init__(self, settings: config.Settings, store: Store):
        self.settings = settings
        self.store = store
        self.snapshots = SnapshotStore(settings.snapshot_dir)
        self.min_interval_s = 5.0

    # API side
    def submit(self, url: str, events_url: str | None, name: str | None) -> int:
        url = normalise_url(url)
        events_url = normalise_url(events_url) if events_url and events_url.strip() else None
        known = self.store.organiser_by_domain(organiser_domain(url))
        if known:
            raise DuplicateOrganiser(known["id"], known["name"], known["status"])
        return self.store.create_intake(url, events_url, (name or "").strip() or None)

    def decide(self, intake_id: int, approve: bool) -> None:
        row = self.store.intake(intake_id)
        if row is None:
            raise KeyError(intake_id)
        if row["state"] not in DECIDABLE:
            raise ValueError(f"An intake in state {row['state']} cannot be decided")
        if approve:
            self.store.log_intake(intake_id, "review", "Approved by you.", "ok")
            self.store.update_intake(intake_id, state="queued", approved=True)
        else:
            self.store.log_intake(intake_id, "review", "Rejected by you.", "warn")
            self.store.update_intake(intake_id, state="rejected", approved=False)

    # Worker side
    def serve(self, poll_s: float = POLL_S, stop: Callable[[], bool] = lambda: False) -> None:
        """Work queued intakes until `stop()`: woken by NOTIFY, with a poll as the fallback,
        so a lost notification only delays work. One worker per store; crash-only, since an
        intake cut off mid-step is queued again by the next start."""
        with self.store.intake_listener() as wait:
            self.store.requeue_interrupted_intakes()
            while not stop():
                while (row := self.store.next_intake()) is not None:
                    self.process(row["id"])
                wait(poll_s)

    def process(self, intake_id: int) -> None:
        log = self.store.log_intake
        user_agent = config.user_agent()
        fetcher = Fetcher(
            self.store,
            self.snapshots,
            user_agent,
            self.min_interval_s,
            client=guarded_client(user_agent),
        )
        renderer = (
            render.Renderer(fetcher, self.snapshots, guard=check_host)
            if render.available()
            else None
        )
        try:
            row = self.store.intake(intake_id)
            if row["organiser_id"]:
                self._harvest(intake_id, row["organiser_id"], fetcher, renderer)
                return
            home, body = self._check(intake_id, fetcher)
            profile = self._profiling(intake_id, home, body, fetcher)
            if profile is None:
                return
            self._finding(intake_id, home, body, profile, fetcher, renderer)
        except IntakeError as exc:
            log(intake_id, "error", str(exc), "error")
            self.store.update_intake(intake_id, state="failed", error=str(exc))
        except Exception as exc:
            message = f"Unexpected error: {type(exc).__name__}: {exc}"[:500]
            log(intake_id, "error", message, "error")
            self.store.update_intake(intake_id, state="failed", error=message)
        finally:
            if renderer:
                renderer.close()

    def _check(self, intake_id: int, fetcher: Fetcher) -> tuple[str, bytes]:
        row = self.store.intake(intake_id)
        self.store.update_intake(intake_id, state="checking")
        url = row["url"]
        try:
            check_host(urlsplit(url).hostname)
        except UnsafeAddress as exc:
            raise IntakeError(f"This address cannot be fetched: {exc}.") from exc
        known = self.store.organiser_by_domain(organiser_domain(url))
        if known:
            raise IntakeError(f"Already in the registry as {known['name']} ({known['status']}).")
        fetched = fetcher.get(url)
        if not fetched.ok:
            raise IntakeError(f"The homepage did not load: {fetched.error}.")
        body = self.snapshots.get(fetched.content_hash)
        if "html" not in (fetched.content_type or "") and b"<html" not in body[:2000].lower():
            raise IntakeError(f"The homepage is not a web page ({fetched.content_type}).")
        self.store.log_intake(
            intake_id,
            "checking",
            f"The homepage answers (HTTP {fetched.status}, {len(body) // 1024} KB) and may be"
            " fetched.",
            "ok",
        )
        return url, body

    def _router(self) -> Router | None:
        key = self.settings.openrouter_key
        return Router(key, self.store, self.settings.llm_daily_cap) if key else None

    def _profiling(self, intake_id: int, url: str, body: bytes, fetcher: Fetcher) -> dict | None:
        row = self.store.intake(intake_id)
        log = self.store.log_intake
        if row["approved"]:
            return row["profile"] or {}
        self.store.update_intake(intake_id, state="profiling")
        router = self._router()
        if router is None:
            log(
                intake_id,
                "profiling",
                "No LLM key is configured, so nobody checked who this"
                " organisation is. Approve it if you know it.",
                "warn",
            )
            self.store.update_intake(intake_id, state="review")
            return None
        tree = LexborHTMLParser(body)
        anchors = _anchors(tree, url)
        signals = _profile(self.settings)["intake"]["about_signals"]
        host = urlsplit(url).hostname
        about = next(
            (u for u, text in anchors if urlsplit(u).hostname == host and _matches(text, signals)),
            None,
        )
        about_text = ""
        if about:
            fetched = fetcher.get(about)
            if fetched.ok:
                about_text = html.text(self.snapshots.get(fetched.content_hash), about, 4000)
        link_list = "\n".join(f"- {label or '(no text)'}: {u}" for u, label in anchors[:120])
        user = (
            f"Submitted URL: {url}\nName given: {row['name'] or '-'}\n\n"
            f"Homepage text:\n{html.text(body, url, 8000)}\n\n"
            + (f"About page {about}:\n{about_text}\n\n" if about_text else "")
            + f"Links on the homepage:\n{link_list}"
        )
        system = PROFILE_SYSTEM.replace("TODAY", date.today().isoformat())
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        try:
            model, profile = router.complete(messages, PROFILE_SCHEMA, name="organisation")
        except (BudgetExhausted, DailyCapReached, AllModelsFailed, httpx.HTTPError) as exc:
            log(
                intake_id,
                "profiling",
                f"The LLM could not be asked ({type(exc).__name__})."
                " Approve it if you know the organisation.",
                "warn",
            )
            self.store.update_intake(intake_id, state="review")
            return None
        page_links = {u for u, _ in anchors}
        profile["event_links"] = [u for u in profile.get("event_links") or [] if u in page_links]
        profile["model"] = model
        self.store.update_intake(intake_id, profile=profile)
        verdict = (profile["legitimate"], profile["runs_events"], profile["based_in_munich"])
        log(intake_id, "profiling", f"{profile['name']}: {profile['summary']}", "info")
        if verdict[0] == "yes" and "no" not in verdict[1:]:
            log(intake_id, "profiling", "Looks like a real organisation that runs events.", "ok")
            return profile
        if verdict[0] == "no":
            log(intake_id, "profiling", "The LLM judged this not a genuine organiser.", "warn")
            self.store.update_intake(intake_id, state="rejected")
        else:
            log(intake_id, "profiling", "The LLM was not sure. Your call.", "warn")
            self.store.update_intake(intake_id, state="review")
        return None

    def _finding(self, intake_id, url, body, profile, fetcher, renderer) -> None:
        row = self.store.intake(intake_id)
        log = self.store.log_intake
        self.store.update_intake(intake_id, state="finding")
        tree = LexborHTMLParser(body)
        signals = _profile(self.settings)["intake"]["link_signals"]
        candidates: list[Candidate] = []
        if row["events_url"]:
            candidates.append(Candidate(row["events_url"], "the events page you gave"))
        feeds = declared_feeds(tree, url)
        candidates += [Candidate(f, "a feed the homepage declares") for f in feeds]
        if parse(url, "text/html", body, Kind.LISTING).events:
            candidates.append(Candidate(url, "events listed on the homepage"))
        links = scan_links(tree, url, signals)
        candidates += [Candidate(u, "an events link on the homepage") for u in links]
        candidates += [Candidate(u, "named by the LLM") for u in profile.get("event_links") or []]
        if not candidates:
            parts = urlsplit(url)
            candidates.append(Candidate(f"{parts.scheme}://{parts.netloc}/events", "a guess"))
        seen, unique = set(), []
        for c in candidates:
            if canonical(c.url) not in seen:
                seen.add(canonical(c.url))
                unique.append(c)
        log(
            intake_id,
            "finding",
            f"{len(unique)} candidate event pages; checking up to {MAX_CANDIDATES}.",
            "info",
        )
        kept: dict[str, Candidate] = {}
        for candidate in unique[:MAX_CANDIDATES]:
            # A rendered page can land on another candidate, e.g. /events on its Luma page.
            if canonical(candidate.url) in kept:
                log(intake_id, "finding", f"{candidate.url}: already found ({candidate.why}).")
                continue
            found = self._probe(candidate, fetcher, renderer)
            if found is None:
                log(intake_id, "finding", f"{candidate.url}: nothing usable ({candidate.why}).")
                continue
            if canonical(found.url) in kept:
                log(intake_id, "finding", f"{candidate.url} leads to {found.url}, already found.")
                continue
            kept[canonical(found.url)] = found
            how = " after rendering it" if found.browser else ""
            log(
                intake_id,
                "finding",
                f"{found.url}: {found.events} events, {found.links} event"
                f" links{how} ({found.why}).",
                "ok",
            )
        if not kept:
            raise IntakeError(
                "No page with events was found. Give the events page directly and try again."
            )
        aggregators = _profile(self.settings)["aggregators"]
        name = row["name"] or profile.get("name") or _title(tree) or organiser_domain(url)
        organiser_id = self.store.add_organiser(
            name,
            organiser_domain(url),
            "manual",
            homepage=url,
            kind=profile.get("kind") if profile.get("kind") != "other" else None,
            ai_focus=bool(profile.get("ai_focus")),
            discovery_url=url,
        )
        if not organiser_id:
            raise IntakeError("This domain was removed from the registry before.")
        for c in kept.values():
            self.store.add_source(
                c.url,
                c.kind,
                source_class(c.url, aggregators).value,
                EVENT_PAGE_HOURS if c.kind == "event_page" else 24,
                "intake",
                organiser_id,
                c.browser,
            )
        self.store.update_intake(intake_id, organiser_id=organiser_id)
        self._harvest(intake_id, organiser_id, fetcher, renderer)

    def _probe(self, candidate: Candidate, fetcher, renderer) -> Candidate | None:
        fetched = fetcher.get(candidate.url)
        if not fetched.ok:
            return None
        body = self.snapshots.get(fetched.content_hash)
        result = parse(candidate.url, fetched.content_type, body, Kind.LISTING)
        url, browser = candidate.url, False
        useful = result.events or result.follow or result.method == "ics@1"
        if (
            not useful
            and renderer
            and "html" in (fetched.content_type or "")
            and is_shell(body, candidate.url)
        ):
            rendered = renderer.fetch(candidate.url)
            if rendered.ok:
                url, browser = rendered.final_url or candidate.url, True
                body = self.snapshots.get(rendered.content_hash)
                result = parse(url, "text/html", body, Kind.LISTING)
                useful = result.events or result.follow
        if not useful:
            return None
        kind = "feed" if result.method in {"ics@1", "rss@1"} else "listing"
        if platform_key(url) and len(result.events) == 1 and not result.follow:
            kind = "event_page"
        return Candidate(url, candidate.why, kind, browser, len(result.events), len(result.follow))

    def _harvest(self, intake_id: int, organiser_id: int, fetcher, renderer) -> None:
        log = self.store.log_intake
        self.store.update_intake(intake_id, state="harvesting")
        profile = _profile(self.settings)
        counts: Counter = Counter()
        for source in self.store.organiser_sources(organiser_id):
            if not source["added_via"].startswith("link:"):
                harvest_one(self.store, fetcher, self.snapshots, profile, source, counts, renderer)
        follow = [
            s
            for s in self.store.organiser_sources(organiser_id)
            if s["added_via"].startswith("link:") and not s["last_fetched_at"]
        ][:MAX_FOLLOW]
        if follow:
            log(intake_id, "harvesting", f"Reading {len(follow)} event pages.", "info")
        for source in follow:
            harvest_one(self.store, fetcher, self.snapshots, profile, source, counts, renderer)
        urls = [s["url"] for s in self.store.organiser_sources(organiser_id)]
        router = self._router()
        if router and self.store.pending_extractions(1, urls):
            log(
                intake_id,
                "harvesting",
                "Asking the LLM for registration details on pages without structured data.",
                "info",
            )
            extractor = Extractor(router, self.store)
            extract(self.store, self.snapshots, extractor, profile, counts, urls)
        with single_flight(self.settings.data_dir / "run.lock", wait=True):
            judge_events(self.store, Judge(profile), Counter())
        tally = self._tally(set(urls))
        status = "active" if tally["upcoming"] else "dormant"
        self.store.set_organiser_status(organiser_id, status)
        result = {
            "organiser_id": organiser_id,
            "status": status,
            "pages_read": counts["fetched"],
            "fetch_errors": counts["fetch_errors"],
            **tally,
        }
        self.store.update_intake(intake_id, state="done", result=result)
        summary = (
            f"{tally['upcoming']} upcoming events (A {tally['A']}, B {tally['B']}, C {tally['C']}),"
            f" {tally['past']} past, {tally['dropped']} online or outside Munich."
        )
        if status == "dormant":
            summary += " No upcoming event yet, so it is dormant and rechecked on its schedule."
        log(intake_id, "done", summary, "ok")

    def _tally(self, urls: set[str]) -> dict:
        tally = Counter({"A": 0, "B": 0, "C": 0, "upcoming": 0, "past": 0, "dropped": 0})
        for event in self.store.events():
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


class DuplicateOrganiser(Exception):
    def __init__(self, organiser_id: int, name: str, status: str):
        super().__init__(f"Already in the registry as {name} ({status}).")
        self.organiser_id = organiser_id


def _title(tree: LexborHTMLParser) -> str | None:
    node = tree.css_first("title")
    text = node.text(strip=True) if node else ""
    return re.split(r"\s+[|\-–]\s+", text)[0] or None


def as_json(row) -> dict:
    return {
        "id": row["id"],
        "url": row["url"],
        "events_url": row["events_url"],
        "name": row["name"],
        "state": row["state"],
        "approved": row["approved"],
        "profile": row["profile"],
        "log": row["log"],
        "result": row["result"],
        "organiser_id": row["organiser_id"],
        "error": row["error"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
