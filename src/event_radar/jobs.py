"""The jobs the workflows run (ADR 0006 D1), each from the settings alone."""

import contextlib
import json
from datetime import UTC, datetime, timedelta

import httpx
import yaml

from . import config, render, search
from .extract import Extractor
from .extract.router import Router
from .fetch import Fetcher
from .harvester import Harvester, cadence_hours
from .intake.service import IntakeService
from .model import RunOutcome
from .profile import Profile
from .search.tavily import Tavily
from .store import Store

# The LLM queue takes no new job this long after a harvest starts, so judging always runs
# within the workflow's 90 minutes; one job can take 12 more (four models, 180 s each).
LLM_MINUTES = 60


def _profile(settings: config.Settings) -> Profile:
    return Profile.load(settings.repo_dir / "profile.yaml")


def _router(settings: config.Settings, store: Store) -> Router | None:
    return Router.from_settings(settings, store)


def _ping(settings: config.Settings, suffix: str, body: dict | None = None) -> None:
    if not settings.healthcheck_url:
        return
    with contextlib.suppress(httpx.HTTPError):
        httpx.post(settings.healthcheck_url + suffix, content=json.dumps(body or {}), timeout=10)


def seed(settings: config.Settings) -> int:
    """Add the organisers and sources of `seed/organisers.yaml`; returns how many."""
    store = Store(settings.database_url)
    profile = _profile(settings)
    rows = yaml.safe_load((settings.repo_dir / "seed" / "organisers.yaml").read_text("utf-8"))
    for row in rows:
        organiser = store.organisers.add(
            row["name"],
            row["domain"],
            "seed",
            kind=row.get("kind"),
            ai_focus=row.get("ai_focus", False),
            discovery_url="seed/organisers.yaml",
        )
        for src in row["sources"]:
            store.sources.add(
                src["url"],
                src["kind"],
                src.get("class") or profile.source_class(src["url"]).value,
                cadence_hours(src["kind"]),
                "seed",
                organiser or None,
                src.get("browser", False),
            )
    return len(rows)


def harvest(settings: config.Settings) -> dict:
    """The scheduled run: fetch the due sources, extract, judge, prune page bodies."""
    store = Store(settings.database_url)
    router = _router(settings, store)
    harvester = Harvester(
        store,
        _profile(settings),
        Fetcher(store, config.user_agent()),
        extractor=Extractor(router, store) if router else None,
    )
    counts = harvester.counts
    started = datetime.now(UTC)
    run_id = store.runs.start()
    _ping(settings, "/start")
    try:
        harvester.harvest_due()
        harvester.extract(until=started + timedelta(minutes=LLM_MINUTES))
        harvester.judge_events()
        counts["pages_pruned"] = store.pages.prune()
    except Exception as exc:
        failure = {**counts, "error": repr(exc)[:500]}
        store.runs.finish(run_id, RunOutcome.FAILED, failure)
        _ping(settings, "/fail", failure)
        raise
    store.runs.finish(run_id, RunOutcome.OK, dict(counts))
    _ping(settings, "", dict(counts))
    return dict(counts)


def search_web(settings: config.Settings, dry_run: bool = False) -> dict:
    """ADR 0016: the day's web search; candidates the radar does not know wait for review."""
    if not settings.tavily_key:
        raise SystemExit("TAVILY_API_KEY is not set")
    profile = _profile(settings)
    tavily = Tavily(settings.tavily_key, profile.search.exclude_domains)
    store = Store(settings.database_url)
    return search.run(store, profile, tavily, datetime.now(UTC).date(), dry_run)


def browse(settings: config.Settings) -> dict:
    """ADR 0002 D11, ADR 0003 D3: render the due JavaScript-only sources."""
    store = Store(settings.database_url)
    fetcher = Fetcher(store, config.user_agent())
    with render.Renderer(fetcher.politeness) as renderer:
        harvester = Harvester(store, _profile(settings), fetcher, renderer)
        harvester.harvest_due()
    harvester.judge_events()
    return dict(harvester.counts)


def intakes(settings: config.Settings) -> int:
    """Work the queued intakes and exit (ADR 0006 D3); returns how many."""
    store = Store(settings.database_url)
    service = IntakeService(
        store,
        _profile(settings),
        _router(settings, store),
        renderer=render.Renderer if render.available() else None,
    )
    return service.drain()
