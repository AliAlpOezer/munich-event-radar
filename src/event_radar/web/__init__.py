import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..model import UserStatus
from ..store import Store

STATIC = Path(__file__).parent / "static"
RUN_HISTORY = 12


class StatusChange(BaseModel):
    status: UserStatus


def _run(row) -> dict:
    return {
        "id": row["id"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "outcome": row["outcome"],
        "summary": json.loads(row["summary"]) if row["summary"] else {},
    }


def create_app(store: Store) -> FastAPI:
    app = FastAPI(title="Event radar", docs_url=None, redoc_url=None)

    @app.get("/api/status")
    def status() -> dict:
        return {"runs": [_run(r) for r in store.runs(RUN_HISTORY)]}

    @app.get("/api/events")
    def events() -> dict:
        source_of = {s["url"]: s for s in store.sources()}
        runs = store.runs(RUN_HISTORY)
        last_ok = next((r for r in runs if r["outcome"] == "ok"), None)
        rows = []
        for e in store.events():
            sources = [
                {
                    "url": url,
                    "class": source_of[url]["source_class"] if url in source_of else None,
                    "organiser_id": source_of[url]["organiser_id"] if url in source_of else None,
                }
                for url in json.loads(e["sources"])
            ]
            rows.append(
                {
                    "key": e["key"],
                    "tier": e["tier"],
                    "drop_reason": e["drop_reason"],
                    "reasons": json.loads(e["tier_reasons"] or "[]"),
                    "merge_basis": e["merge_basis"],
                    "user_status": e["user_status"],
                    "first_seen": e["first_seen"],
                    "updated_at": e["updated_at"],
                    "stale": bool(last_ok and e["updated_at"] < last_ok["started_at"]),
                    "fields": json.loads(e["fields"]),
                    "sources": sources,
                    "organiser_ids": sorted(
                        {s["organiser_id"] for s in sources if s["organiser_id"] is not None}
                    ),
                }
            )
        return {"events": rows}

    @app.get("/api/organisers")
    def organisers() -> dict:
        by_organiser: dict[int | None, list[dict]] = {}
        for s in store.sources():
            by_organiser.setdefault(s["organiser_id"], []).append(
                {
                    k: s[k]
                    for k in (
                        "url",
                        "kind",
                        "source_class",
                        "browser",
                        "added_via",
                        "last_fetched_at",
                        "last_ok_at",
                        "last_yield",
                        "empty_streak",
                        "error_streak",
                        "last_error",
                    )
                }
            )
        return {
            "organisers": [
                {
                    "id": o["id"],
                    "name": o["name"],
                    "domain": o["domain"],
                    "kind": o["kind"],
                    "ai_focus": bool(o["ai_focus"]),
                    "status": o["status"],
                    "discovered_via": o["discovered_via"],
                    "sources": by_organiser.get(o["id"], []),
                }
                for o in store.organisers()
            ]
        }

    @app.post("/api/events/{key}/status")
    def set_status(key: str, change: StatusChange) -> dict:
        try:
            return {"key": key, "user_status": store.set_user_status(key, change.status).value}
        except KeyError as exc:
            raise HTTPException(404, "No event with this key") from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
