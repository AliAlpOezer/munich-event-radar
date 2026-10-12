import json
import mimetypes
import shutil
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

import yaml

from .extract import Extractor
from .fetch import Fetcher
from .judge import Judge
from .model import Kind, field_value
from .pipeline import ingest
from .platforms import canonical, host
from .profile import Profile
from .resolve import local_date, resolve
from .store import Store, now_iso


def load(repo: Path) -> dict:
    return yaml.safe_load((repo / "eval" / "golden.yaml").read_text(encoding="utf-8"))


def _extension(content_type: str | None) -> str:
    base = (content_type or "").split(";")[0].strip()
    return {"text/calendar": ".ics", "application/json": ".json"}.get(
        base, mimetypes.guess_extension(base) or ".html"
    )


def _fixture_pages(fixtures: Path):
    manifest = json.loads((fixtures / "manifest.json").read_text())
    for url, entry in sorted(manifest.items()):
        if "file" in entry:
            yield url, entry.get("content_type"), (fixtures / entry["file"]).read_bytes()


def _focus_domains(repo: Path) -> tuple[str, ...]:
    rows = yaml.safe_load((repo / "seed" / "organisers.yaml").read_text(encoding="utf-8"))
    return tuple(r["domain"] for r in rows if r.get("ai_focus"))


def _under(url: str, domains: tuple[str, ...]) -> bool:
    return (host(url) + urlsplit(url).path).startswith(domains)


def replay(repo: Path, fixtures: Path, dsn: str | None, extractor: Extractor | None = None) -> dict:
    """Score the golden events from the saved pages, parsed in a scratch store. The
    extractor keeps its cache in the real store, so a replay asks the LLM only once."""
    golden = load(repo)
    now = datetime.fromisoformat(golden["now"])
    profile = Profile.load(repo / "profile.yaml")
    judge = Judge(profile)
    with Store.scratch(dsn) as store:
        for url, content_type, body in _fixture_pages(fixtures):
            digest = store.pages.save(body)
            source_class = profile.source_class(url)
            result = ingest(store, url, digest, content_type, body, Kind.EVENT_PAGE, source_class)
            if result.needs_llm and extractor:
                # Fixture pages have no source rows, so none counts as an event page.
                method, events = extractor.extract(url, digest, body, event_page=False)
                store.observations.add(digest, url, source_class, method, events)
        groups = resolve(store.observations.all())
    return _score(golden, groups, judge, now, _focus_domains(repo))


def _score(golden: dict, groups, judge: Judge, now: datetime, domains: tuple[str, ...]) -> dict:
    rows = []
    for event in golden["events"]:
        pages = {canonical(p) for p in event["pages"]}
        matches = [g for g in groups if pages & {canonical(o.url) for o in g.observations}]
        # Fixture pages have no source rows. An event is judged as its organiser's: AI-focused
        # when one of its pages lies under an AI-focused seed organiser's domain.
        ai_focus = any(_under(p, domains) for p in event["pages"])
        verdicts = [(g, judge.judge(g.fields, now, ai_focus=ai_focus)) for g in matches]
        surfaced = [(g, v) for g, v in verdicts if v.tier in {"A", "B"}]
        best = surfaced[0] if surfaced else (verdicts[0] if verdicts else (None, None))
        group, verdict = best
        expected_close = (event.get("registration") or {}).get("closes")
        got_close = local_date(field_value(group.fields, "reg_closes_at") if group else None)
        rows.append(
            {
                "id": event["id"],
                "expected": event["tier"],
                "got": verdict.tier if verdict else None,
                "drop": verdict.drop_reason if verdict else "not_found",
                "reasons": list(verdict.reasons) if verdict else [],
                "window_expected": expected_close,
                "window_got": got_close,
                "window_ok": not expected_close
                or bool(got_close and got_close.endswith(expected_close[5:10])),
            }
        )
    a_rows = [r for r in rows if r["expected"] == "A"]
    surfaced = [r for r in a_rows if r["got"] in {"A", "B"}]
    return {
        "a_recall": round(len(surfaced) / len(a_rows), 3),
        "pass": len(surfaced) / len(a_rows) >= golden["pass_bar"]
        and all(r["window_ok"] for r in surfaced),
        "events": rows,
    }


def freeze(repo: Path, fixtures: Path, fetcher: Fetcher) -> dict:
    fixtures.mkdir(parents=True, exist_ok=True)
    manifest_path = fixtures / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    for event in load(repo)["events"]:
        for url in event["pages"]:
            if url in manifest or event.get("needs_browser"):
                continue
            result = fetcher.get(url)
            entry = {"status": result.status, "error": result.error, "fetched_at": now_iso()}
            if result.ok:
                name = result.content_hash[:16] + _extension(result.content_type)
                (fixtures / name).write_bytes(fetcher.store.pages.body(result.content_hash))
                entry |= {"file": name, "content_type": result.content_type}
            manifest[url] = entry
    tmp = manifest_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(manifest, indent=1, sort_keys=True))
    shutil.move(tmp, manifest_path)
    return manifest
