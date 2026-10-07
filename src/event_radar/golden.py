import json
import mimetypes
import shutil
from pathlib import Path

import yaml

from .fetch import Fetcher
from .snapshots import SnapshotStore
from .store import now_iso


def load(repo: Path) -> dict:
    return yaml.safe_load((repo / "eval" / "golden.yaml").read_text(encoding="utf-8"))


def _extension(content_type: str | None) -> str:
    base = (content_type or "").split(";")[0].strip()
    return {"text/calendar": ".ics", "application/json": ".json"}.get(
        base, mimetypes.guess_extension(base) or ".html"
    )


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
