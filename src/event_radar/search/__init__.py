"""The daily web search (ADR 0016): the day's queries go to Tavily, and each hit the radar does
not know, from a site it does not know, becomes an intake waiting for an admin's review."""

from collections import Counter
from datetime import date

import httpx

from ..intake.urls import organiser_domain
from ..judge import Judge
from ..model import IntakeKind
from ..platforms import canonical
from ..profile import Profile, Search
from ..store import Store
from . import gate
from .tavily import SearchStopped, Tavily

PROVIDER = "tavily"  # its row in the daily budget


def todays_queries(search: Search, today: date) -> list[str]:
    """The day's slice of the query list, so every query runs within a few days."""
    queries = search.queries
    per_day = min(search.daily_cap, len(queries))
    start = today.toordinal() * per_day % len(queries)
    return [queries[(start + i) % len(queries)] for i in range(per_day)]


def run(store: Store, profile: Profile, tavily: Tavily, today: date, dry_run: bool) -> dict:
    """Search, then propose the unknown candidates, best score first, up to the day's cap.
    A dry run proposes nothing and lists every hit with its verdict instead."""
    counts: Counter = Counter()
    judge = Judge(profile)
    found: dict[tuple[str, str], gate.Candidate] = {}
    seen: list[dict] = []
    stopped = None
    for query in todays_queries(profile.search, today):
        if not store.extraction.spend(PROVIDER, profile.search.daily_cap):
            counts["budget_spent"] += 1
            break
        try:
            hits = tavily.search(query)
        except SearchStopped as stop:
            stopped = stop
            break
        except httpx.HTTPError:
            counts["searches_failed"] += 1
            continue
        counts["searches"] += 1
        for hit in hits:
            made = gate.candidate(hit, query, profile, judge)
            if isinstance(made, str):
                counts[made] += 1
                seen.append({"url": hit.url, "query": query, "verdict": made})
                continue
            ident = (made.kind, canonical(made.url))
            if ident not in found or found[ident].score < made.score:
                found[ident] = made
    with store.lock("search"):
        known = _known(store)
        fresh = []
        for made in sorted(found.values(), key=lambda c: -c.score):
            stored = made.kind == IntakeKind.EVENT and bool(store.events.known(set(made.aliases)))
            reason = gate.known_reason(made, known, stored)
            counts[reason or "unknown"] += 1
            seen.append({"url": made.url, "query": made.query, "verdict": reason or made.kind})
            if reason is None:
                fresh.append(made)
        room = max(0, profile.search.daily_candidates - store.intakes.proposed_today())
        if not dry_run:
            for made in fresh[:room]:
                store.intakes.propose(
                    made.kind,
                    made.url,
                    made.events_url,
                    f'Found by the web search for "{made.query}": {made.title}',
                )
                counts["proposed"] += 1
    summary: dict = dict(counts)
    if stopped is not None:
        summary["stopped"] = str(stopped)
        summary["failed"] = stopped.fatal
    if dry_run:
        summary["hits"] = seen
    return summary


def _known(store: Store) -> gate.Known:
    intakes = store.intakes.all()
    sites = {o["domain"] for o in store.organisers.all()}
    sites |= {organiser_domain(i["url"]) for i in intakes if i["kind"] == IntakeKind.ORGANISATION}
    pages = {canonical(s["url"]) for s in store.sources.all()}
    pages |= {canonical(i["url"]) for i in intakes if i["kind"] == IntakeKind.EVENT}
    return gate.Known(frozenset(sites), frozenset(pages))
