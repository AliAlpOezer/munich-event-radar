from collections import Counter
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..intake import DuplicateOrganiser, IntakeError, IntakeService, as_json
from ..model import UserStatus
from ..store import Store

STATIC = Path(__file__).parent / "static"
RUN_HISTORY = 12
INTAKE_HISTORY = 20
# An image on this many events is a site's default share image, not an event cover.
SHARED_IMAGE = 3


class StatusChange(BaseModel):
    status: UserStatus


class IntakeRequest(BaseModel):
    url: str
    events_url: str | None = None
    name: str | None = None


class IntakeDecision(BaseModel):
    approve: bool


def _run(row) -> dict:
    return {
        "id": row["id"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "outcome": row["outcome"],
        "summary": row["summary"] or {},
    }


def _image(event) -> str | None:
    return (event["fields"].get("image") or {}).get("value")


def create_app(store: Store, intake: IntakeService | None = None) -> FastAPI:
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
        events = store.events()
        images = Counter(_image(e) for e in events)
        for e in events:
            image = _image(e)
            sources = [
                {
                    "url": url,
                    "class": source_of[url]["source_class"] if url in source_of else None,
                    "organiser_id": source_of[url]["organiser_id"] if url in source_of else None,
                }
                for url in e["sources"]
            ]
            rows.append(
                {
                    "key": e["key"],
                    "tier": e["tier"],
                    "drop_reason": e["drop_reason"],
                    "reasons": e["tier_reasons"] or [],
                    "merge_basis": e["merge_basis"],
                    "user_status": e["user_status"],
                    "first_seen": e["first_seen"],
                    "updated_at": e["updated_at"],
                    "stale": bool(last_ok and e["updated_at"] < last_ok["started_at"]),
                    "fields": e["fields"],
                    "cover": image if images[image] < SHARED_IMAGE else None,
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
                    "ai_focus": o["ai_focus"],
                    "status": o["status"],
                    "homepage": o["homepage"],
                    "discovered_via": o["discovered_via"],
                    "discovered_at": o["discovered_at"],
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

    @app.get("/api/intakes")
    def intakes() -> dict:
        return {
            "enabled": intake is not None,
            "intakes": [as_json(r) for r in store.intakes(INTAKE_HISTORY)],
        }

    @app.get("/api/intakes/{intake_id}")
    def intake_one(intake_id: int) -> dict:
        row = store.intake(intake_id)
        if row is None:
            raise HTTPException(404, "No intake with this id")
        return as_json(row)

    @app.post("/api/intakes", status_code=202)
    def submit(request: IntakeRequest) -> dict:
        if intake is None:
            raise HTTPException(503, "Adding organisations is not enabled on this server")
        try:
            intake_id = intake.submit(request.url, request.events_url, request.name)
        except DuplicateOrganiser as exc:
            raise HTTPException(409, str(exc)) from exc
        except IntakeError as exc:
            raise HTTPException(422, str(exc)) from exc
        return as_json(store.intake(intake_id))

    @app.post("/api/intakes/{intake_id}/decision")
    def decide(intake_id: int, decision: IntakeDecision) -> dict:
        if intake is None:
            raise HTTPException(503, "Adding organisations is not enabled on this server")
        try:
            intake.decide(intake_id, decision.approve)
        except KeyError as exc:
            raise HTTPException(404, "No intake with this id") from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return as_json(store.intake(intake_id))

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    # Asset URLs carry no version, so browsers must revalidate (cheap 304s via ETag);
    # otherwise a cached old app.js can run against a newer page after a deploy.
    @app.middleware("http")
    async def revalidate(request: Request, call_next):
        response = await call_next(request)
        if request.url.path == "/" or request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache"
        return response

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
