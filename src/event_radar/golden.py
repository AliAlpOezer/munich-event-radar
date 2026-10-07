import json
import mimetypes
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

import yaml

from .fetch import Fetcher
from .judge import Judge
from .model import Kind
from .pipeline import ingest
from .platforms import canonical, source_class
from .resolve import local_date, resolve
from .snapshots import SnapshotStore
from .store import Store, now_iso


def load(repo: Path) -> dict:
    return yaml.safe_load((repo / "eval" / "golden.yaml").read_text(encoding="utf-8"))


def _extension(content_type: str | None) -> str:
    base = (content_type or "").split(";")[0].strip()
    return {"text/calendar": ".ics", "application/json": ".json"}.get(
        base, mimetypes.guess_extension(base) or ".html"
    )


def _fixture_pages(repo: Path):
    fixtures = repo / "eval" / "fixtures"
    manifest = json.loads((fixtures / "manifest.json").read_text())
    for url, entry in sorted(manifest.items()):
        if "file" in entry:
            yield url, entry.get("content_type"), (fixtures / entry["file"]).read_bytes()


def replay(repo: Path, extract=None) -> dict:
    golden = load(repo)
    now = datetime.fromisoformat(golden["now"])
    profile = yaml.safe_load((repo / "profile.yaml").read_text(encoding="utf-8"))
    judge = Judge(profile)
    with tempfile.TemporaryDirectory() as tmp:
        store = Store(Path(tmp) / "replay.db")
        snapshots = SnapshotStore(Path(tmp) / "snapshots")
        for url, content_type, body in _fixture_pages(repo):
            digest = snapshots.put(body)
            result = ingest(
                store,
                url,
                digest,
                content_type,
                body,
                Kind.EVENT_PAGE,
                source_class(url, profile["aggregators"]),
            )
            if result.needs_llm and extract:
                extract(store, url, digest, body, source_class(url, profile["aggregators"]))
        groups = resolve(store.observations())
    return _score(golden, groups, judge, now)


def _score(golden: dict, groups, judge: Judge, now: datetime) -> dict:
    rows = []
    for event in golden["events"]:
        pages = {canonical(p) for p in event["pages"]}
        matches = [g for g in groups if pages & {canonical(o.url) for o in g.observations}]
        verdicts = [(g, judge.judge(g.fields, now)) for g in matches]
        surfaced = [(g, v) for g, v in verdicts if v.tier in {"A", "B"}]
        best = surfaced[0] if surfaced else (verdicts[0] if verdicts else (None, None))
        group, verdict = best
        expected_close = (event.get("registration") or {}).get("closes")
        got_close = local_date(
            ((group.fields if group else {}).get("reg_closes_at") or {}).get("value")
        )
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


def freeze(repo: Path, fetcher: Fetcher, snapshots: SnapshotStore) -> dict:
    fixtures = repo / "eval" / "fixtures"
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
                (fixtures / name).write_bytes(snapshots.get(result.content_hash))
                entry |= {"file": name, "content_type": result.content_type}
            manifest[url] = entry
    tmp = manifest_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(manifest, indent=1, sort_keys=True))
    shutil.move(tmp, manifest_path)
    return manifest
