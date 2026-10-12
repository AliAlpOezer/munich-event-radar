from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..intake.urls import organiser_domain
from ..model import IntakeState, UserStatus, field_value
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
    return field_value(event["fields"], "image")


class _Refused(Exception):
    """A submission the person must correct (422) or that is a duplicate (409)."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def _normalise_url(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        raise _Refused(422, "Enter the organisation's website.")
    if "://" not in text:
        text = f"https://{text}"
    parts = urlsplit(text)
    if parts.scheme not in {"http", "https"} or not parts.hostname or "." not in parts.hostname:
        raise _Refused(422, "That is not a web address (http or https).")
    return text.split("#")[0]


def _submit(store: Store, request: "IntakeRequest") -> int:
    url = _normalise_url(request.url)
    events_url = request.events_url
    events_url = _normalise_url(events_url) if events_url and events_url.strip() else None
    known = store.organisers.by_domain(organiser_domain(url))
    if known:
        raise _Refused(409, f"Already in the registry as {known['name']} ({known['status']}).")
    return store.intakes.create(url, events_url, (request.name or "").strip() or None)


def _decide(store: Store, intake_id: int, approve: bool) -> None:
    row = store.intakes.get(intake_id)
    if row is None:
        raise HTTPException(404, "No intake with this id")
    if row["state"] not in {IntakeState.REVIEW, IntakeState.REJECTED}:
        raise HTTPException(409, f"An intake in state {row['state']} cannot be decided")
    if approve:
        store.intakes.log(intake_id, "review", "Approved by you.", "ok")
        store.intakes.update(intake_id, state=IntakeState.QUEUED, approved=True)
    else:
        store.intakes.log(intake_id, "review", "Rejected by you.", "warn")
        store.intakes.update(intake_id, state=IntakeState.REJECTED, approved=False)


def _intake(row) -> dict:
    return {
        k: row[k]
        for k in (
            "id",
            "url",
            "events_url",
            "name",
            "state",
            "approved",
            "profile",
            "log",
            "result",
            "organiser_id",
            "error",
            "created_at",
            "updated_at",
        )
    }


def create_app(store: Store, intakes_enabled: bool = False) -> FastAPI:
    app = FastAPI(title="Event radar", docs_url=None, redoc_url=None)

    @app.get("/api/status")
    def status() -> dict:
        return {"runs": [_run(r) for r in store.runs.recent(RUN_HISTORY)]}

    @app.get("/api/events")
    def events() -> dict:
        source_of = {s["url"]: s for s in store.sources.all()}
        runs = store.runs.recent(RUN_HISTORY)
        last_ok = next((r for r in runs if r["outcome"] == "ok"), None)
        rows = []
        events = store.events.all()
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
        for s in store.sources.all():
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
                for o in store.organisers.all()
            ]
        }

    @app.post("/api/events/{key}/status")
    def set_status(key: str, change: StatusChange) -> dict:
        try:
            return {
                "key": key,
                "user_status": store.users.set_owner_status(key, change.status).value,
            }
        except KeyError as exc:
            raise HTTPException(404, "No event with this key") from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/api/intakes")
    def intakes() -> dict:
        return {
            "enabled": intakes_enabled,
            "intakes": [_intake(r) for r in store.intakes.recent(INTAKE_HISTORY)],
        }

    @app.get("/api/intakes/{intake_id}")
    def intake_one(intake_id: int) -> dict:
        row = store.intakes.get(intake_id)
        if row is None:
            raise HTTPException(404, "No intake with this id")
        return _intake(row)

    @app.post("/api/intakes", status_code=202)
    def submit(request: IntakeRequest) -> dict:
        if not intakes_enabled:
            raise HTTPException(503, "Adding organisations is not enabled on this server")
        try:
            intake_id = _submit(store, request)
        except _Refused as exc:
            raise HTTPException(exc.status, str(exc)) from exc
        return _intake(store.intakes.get(intake_id))

    @app.post("/api/intakes/{intake_id}/decision")
    def decide(intake_id: int, decision: IntakeDecision) -> dict:
        if not intakes_enabled:
            raise HTTPException(503, "Adding organisations is not enabled on this server")
        _decide(store, intake_id, decision.approve)
        return _intake(store.intakes.get(intake_id))

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
