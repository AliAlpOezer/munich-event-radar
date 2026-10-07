import contextlib
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import httpx
import yaml

from . import config
from .extract import Extractor
from .extract.router import AllModelsFailed, BudgetExhausted, DailyCapReached, Router
from .fetch import Fetcher
from .judge import Judge
from .model import Kind, SourceClass
from .pipeline import ingest
from .platforms import source_class
from .resolve import resolve
from .snapshots import SnapshotStore
from .store import Store

EVENT_PAGE_HOURS = 72
HOT_HOURS = 24
LLM_ATTEMPTS = 3


@contextlib.contextmanager
def single_flight(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("w")
    try:
        import fcntl

        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except ImportError:
        pass
    except BlockingIOError as exc:
        raise SystemExit("another run holds the lock") from exc
    try:
        yield
    finally:
        handle.close()


def _follow_kind(url: str) -> Kind:
    return Kind.FEED if "/ics" in url or url.endswith((".ics", "/ical/")) else Kind.EVENT_PAGE


def seed(settings: config.Settings) -> int:
    store = Store(settings.db_path)
    profile = _profile(settings)
    rows = yaml.safe_load((settings.repo_dir / "seed" / "organisers.yaml").read_text("utf-8"))
    for row in rows:
        organiser = store.add_organiser(
            row["name"],
            row["domain"],
            "seed",
            kind=row.get("kind"),
            discovery_url="seed/organisers.yaml",
        )
        for src in row["sources"]:
            store.add_source(
                src["url"],
                src["kind"],
                src.get("class") or source_class(src["url"], profile["aggregators"]).value,
                24 if src["kind"] != "event_page" else EVENT_PAGE_HOURS,
                "seed",
                organiser or None,
                src.get("browser", False),
            )
    return len(rows)


def _profile(settings: config.Settings) -> dict:
    return yaml.safe_load((settings.repo_dir / "profile.yaml").read_text(encoding="utf-8"))


def _ping(settings: config.Settings, suffix: str, body: dict | None = None) -> None:
    if not settings.healthcheck_url:
        return
    with contextlib.suppress(httpx.HTTPError):
        httpx.post(settings.healthcheck_url + suffix, content=json.dumps(body or {}), timeout=10)


def _harvest(store, fetcher, snapshots, profile, counts: Counter) -> None:
    now = datetime.now(UTC).isoformat(timespec="seconds")
    for source in store.due_sources(now):
        fetched = fetcher.get(source["url"])
        if not fetched.ok:
            store.mark_source(source["id"], False, fetched.error, None)
            counts["fetch_errors"] += 1
            continue
        result = ingest(
            store,
            source["url"],
            fetched.content_hash,
            fetched.content_type,
            snapshots.get(fetched.content_hash),
            Kind(source["kind"]),
            SourceClass(source["source_class"]),
        )
        store.mark_source(source["id"], True, None, len(result.events) + len(result.follow))
        counts["fetched"] += 1
        counts["observed"] += len(result.events)
        for link in result.follow:
            store.add_source(
                link,
                _follow_kind(link).value,
                source_class(link, profile["aggregators"]).value,
                EVENT_PAGE_HOURS,
                f"link:{source['url']}",
                source["organiser_id"],
            )


def _extract(store, snapshots, extractor, profile, counts: Counter) -> None:
    for job in store.pending_extractions(limit=100):
        body = snapshots.get(job["snapshot_hash"])
        try:
            extractor(
                store,
                job["url"],
                job["snapshot_hash"],
                body,
                source_class(job["url"], profile["aggregators"]),
            )
        except (BudgetExhausted, DailyCapReached):
            counts["llm_deferred"] += 1
            return
        except (AllModelsFailed, httpx.HTTPError):
            state = "needs_look" if job["attempts"] + 1 >= LLM_ATTEMPTS else "pending"
            store.finish_extraction(job["snapshot_hash"], job["url"], state)
            counts["llm_failed"] += 1
            continue
        store.finish_extraction(job["snapshot_hash"], job["url"], "done")
        counts["llm_done"] += 1


def run(settings: config.Settings) -> dict:
    store = Store(settings.db_path)
    snapshots = SnapshotStore(settings.snapshot_dir)
    profile = _profile(settings)
    judge = Judge(profile)
    counts: Counter = Counter()
    with single_flight(settings.data_dir / "run.lock"):
        run_id = store.start_run()
        _ping(settings, "/start")
        try:
            _harvest(
                store, Fetcher(store, snapshots, config.user_agent()), snapshots, profile, counts
            )
            if settings.openrouter_key:
                router = Router(settings.openrouter_key, store, settings.llm_daily_cap)
                _extract(store, snapshots, Extractor(router, store), profile, counts)
            now = datetime.now(UTC)
            resolved = []
            for group in resolve(store.observations()):
                verdict = judge.judge(group.fields, now)
                resolved.append((group.aliases, group.fields, group.merge_basis, verdict))
                counts[f"tier_{verdict.tier or verdict.drop_reason}"] += 1
                if verdict.tier in {"A", "B"}:
                    store.set_cadence([o.url for o in group.observations], HOT_HOURS)
            counts.update(store.save_events(resolved))
        except Exception as exc:
            store.finish_run(run_id, "failed", {**counts, "error": repr(exc)[:500]})
            _ping(settings, "/fail", {**counts, "error": repr(exc)[:500]})
            raise
        store.finish_run(run_id, "ok", dict(counts))
        _ping(settings, "", dict(counts))
    return dict(counts)
