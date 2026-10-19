"""Offline calibration of a decision model (ADR 0017 plan step 2): ask it the radar's
questions about stored pages the LLM has already answered about, and measure how well the
two agree at each band. Read-only on the database. Writes one row per page asked about,
with no page text, and a summary."""

import gzip
import json
import re
import sys
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from itertools import groupby, islice
from pathlib import Path
from statistics import median, quantiles

import httpx
import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from .. import html
from ..extract import key as extraction_key
from ..extract import triage
from ..extract.verify import _flat, verify
from ..model import Kind
from ..profile import Profile
from . import questions
from .systemone import SystemOne

# The recall of the costly side each decision must keep (to confirm with Alp): pages the
# LLM triage kept (D1), pages with a verified registration field (D2).
TARGETS = {questions.IN_SCOPE: 0.98, questions.REGISTRATION: 0.97}
# Up to 0.95: a model with a soft fitted temperature, such as Kev-0.8B, puts the pages it
# answers no about near 0.5, not near 0 (first runs, 2026-10-19).
LOWS = (0.01, 0.02, 0.05, *(round(0.05 * i, 2) for i in range(2, 20)))
_GERMAN = re.compile(r"\b(der|die|das|und|mit|für|von|zur|zum|ist|nicht|eine?)\b")
_ENGLISH = re.compile(r"\b(the|and|with|for|of|is|not|an?|to|this)\b")
_REGISTRATION = {f for fields in questions.REGISTRATION_FIELDS.values() for f in fields}


@dataclass(frozen=True)
class Item:
    """One page to ask about, with the LLM's answer as its label."""

    decision: str
    variant: str  # what the state holds: the triage `candidate`, `passages` or the whole `text`
    url: str
    lang: str
    label: bool
    labels: dict[str, bool]  # per question, where the LLM's answer tells them apart
    basis: str  # `triage`; or `exact`, `earlier` or `approximate` (see `labelled`)
    d2_page: bool  # one structured event and no registration field from the parser
    quotes: tuple[int, int] | None  # grounding quotes inside the state, of all
    state: dict


def labelled(dsn: str, profile: Profile, schema: str = "radar") -> list[Item]:
    """The pages the LLM answered about, read from the store without writing.

    In scope: a stored page whose triage candidate has a cached verdict. Registration: each
    URL's latest stored body, since older bodies are pruned. Exact when that body gives the
    very text an extraction answer was cached for, its fields verified again; earlier when
    the text changed since, a field counting while its quote is still on the page;
    approximate negatives when it changed and no answer about the URL ever held a field."""
    items: list[Item] = []
    with psycopg.connect(dsn, row_factory=dict_row, connect_timeout=15) as conn:
        conn.read_only = True
        with conn.transaction():
            conn.execute(sql.SQL("SET LOCAL search_path TO {}").format(sql.Identifier(schema)))
            verdicts = {
                row["content_hash"]: bool(row["result"].get("relevant"))
                for row in conn.execute(
                    "SELECT content_hash, result FROM extraction WHERE version = %s",
                    (triage.VERSION,),
                )
            }
            answers = {
                row["content_hash"]: row["result"]
                for row in conn.execute(
                    "SELECT DISTINCT ON (content_hash) content_hash, result FROM extraction"
                    " WHERE version LIKE %s ORDER BY content_hash, version DESC",
                    ("events-v%",),
                )
            }
            done = {
                row["url"]
                for row in conn.execute("SELECT url FROM extraction_job WHERE state = 'done'")
            }
            earlier: dict[str, dict] = {}
            for row in conn.execute(
                "SELECT url, fields FROM observation WHERE method LIKE %s ORDER BY observed_at",
                ("llm:%@events-v%",),
            ):
                earlier.setdefault(row["url"], {}).update(
                    {k: v.get("quote") for k, v in row["fields"].items() if k in _REGISTRATION}
                )
            asked: set[str] = set()
            seen: set[tuple[str, str]] = set()
            previous = None
            with conn.cursor(name="bodies") as bodies:
                bodies.execute(
                    "SELECT s.url, s.content_hash, b.body, src.kind FROM snapshot s"
                    " JOIN page_body b ON b.content_hash = s.content_hash"
                    " LEFT JOIN source src ON src.url = s.url ORDER BY s.url, s.id DESC"
                )
                for row in bodies:
                    url = row["url"]
                    latest, previous = url != previous, url
                    if (url, row["content_hash"]) in seen:
                        continue
                    seen.add((url, row["content_hash"]))
                    body = gzip.decompress(row["body"])
                    shown, fields = triage.candidate(url, body)
                    text = html.text(body, url)
                    lang = _language(_flat(text))
                    d2_page = (
                        row["kind"] in (None, Kind.EVENT_PAGE)
                        and fields is not None
                        and not fields.keys() & _REGISTRATION
                    )
                    if (key := triage.key(shown)) in verdicts and key not in asked:
                        asked.add(key)
                        items.append(
                            Item(
                                questions.IN_SCOPE,
                                "candidate",
                                url,
                                lang,
                                verdicts[key],
                                {},
                                "triage",
                                d2_page,
                                None,
                                shown,
                            )
                        )
                    if not latest:
                        continue
                    label = _registration_label(url, text, answers, done, earlier)
                    if label is None:
                        continue
                    basis, found = label
                    event = shown if fields is not None else {"page": shown["page"]}
                    kept = questions.passages(
                        text, profile.registration.words, profile.registration.window
                    )
                    inside = _flat(" ".join(kept))
                    common = {
                        "decision": questions.REGISTRATION,
                        "url": url,
                        "lang": lang,
                        "label": bool(found),
                        "labels": {
                            name: any(f in found for f in named)
                            for name, named in questions.REGISTRATION_FIELDS.items()
                        },
                        "basis": basis,
                        "d2_page": d2_page,
                    }
                    items.append(
                        Item(
                            variant="passages",
                            quotes=(sum(_flat(q) in inside for q in found.values()), len(found)),
                            state={"event": event, "passages": kept},
                            **common,
                        )
                    )
                    items.append(
                        Item(
                            variant="text",
                            quotes=(len(found), len(found)),
                            state={"event": event, "text": text},
                            **common,
                        )
                    )
    return sorted(items, key=lambda i: (i.decision, i.variant, i.url))


def _registration_label(
    url: str, text: str, answers: dict, done: set[str], earlier: dict[str, dict]
) -> tuple[str, dict[str, str]] | None:
    """The basis of a page's label and its registration fields with their quotes; None
    when the page has no label."""
    answer = answers.get(extraction_key(url, text))
    if answer is not None:
        found = {}
        for raw in (answer.get("result") or {}).get("events") or []:
            try:
                event, _ = verify(raw, text)
            except AttributeError:
                continue
            found.update({k: v.quote for k, v in event.fields.items() if k in _REGISTRATION})
        return "exact", found
    if url not in done:
        return None
    flat = _flat(text)
    quotes = earlier.get(url, {})
    still = {k: q for k, q in quotes.items() if q and _flat(q) in flat}
    if still:
        return "earlier", still
    return ("approximate", {}) if not quotes else None


def run(
    dsn: str,
    profile: Profile,
    client: SystemOne,
    out: Path,
    decisions: Iterable[str],
    limit: int | None = None,
    schema: str = "radar",
) -> dict:
    """Ask `client` about every labelled page (the first `limit` of each state, by URL),
    writing `rows.jsonl` as it goes and `summary.json` at the end."""
    asked = {
        questions.IN_SCOPE: questions.in_scope(profile),
        questions.REGISTRATION: questions.registration(),
    }
    wanted = set(decisions)
    items = [i for i in labelled(dsn, profile, schema) if i.decision in wanted]
    if limit:
        items = [
            i
            for _, group in groupby(items, key=lambda i: (i.decision, i.variant))
            for i in islice(group, limit)
        ]
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    with (out / "rows.jsonl").open("w", encoding="utf-8") as file:
        for n, item in enumerate(items, 1):
            rows.append(_ask(client, item, asked[item.decision]))
            file.write(json.dumps(rows[-1], ensure_ascii=False) + "\n")
            file.flush()
            if n % 25 == 0 or n == len(items):
                print(f"asked {n} of {len(items)}", file=sys.stderr, flush=True)
    summary = {"model": client.model, "options": client.options, **summarise(rows)}
    (out / "summary.json").write_text(
        json.dumps(summary, indent=1, ensure_ascii=False), encoding="utf-8"
    )
    return summary


def _ask(client: SystemOne, item: Item, asked: dict) -> dict:
    row = {
        "decision": item.decision,
        "variant": item.variant,
        "url": item.url,
        "lang": item.lang,
        "label": item.label,
        "labels": item.labels,
        "basis": item.basis,
        "d2_page": item.d2_page,
        "quotes": item.quotes,
        "chars": len(json.dumps(item.state, ensure_ascii=False)),
    }
    try:
        answer = client.ask(item.state, asked)
        p = {name: float(answer.answers[name]["noul"]) for name in asked}
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as error:
        return {**row, "error": f"{type(error).__name__}: {str(error)[:200]}"}
    return {
        **row,
        "p": p,
        "score": max(p.values()),
        "tokens": answer.input_tokens,
        "seconds": round(answer.seconds, 3),
    }


def census(items: list[Item]) -> dict:
    """What `labelled` found, without asking a model."""
    counts: Counter = Counter()
    quotes = [0, 0]
    for i in items:
        counts[f"{i.decision}/{i.variant}/{i.basis}/{'yes' if i.label else 'no'}/{i.lang}"] += 1
        if i.variant == "passages" and i.quotes:
            quotes = [quotes[0] + i.quotes[0], quotes[1] + i.quotes[1]]
    return {"items": dict(sorted(counts.items())), "quotes_in_passages": quotes}


def summarise(rows: list[dict]) -> dict:
    groups = {}
    for decision, variant in sorted({(r["decision"], r["variant"]) for r in rows}):
        group = [r for r in rows if (r["decision"], r["variant"]) == (decision, variant)]
        answered = [r for r in group if "score" in r]
        subsets = {
            "all": answered,
            "de": [r for r in answered if r["lang"] == "de"],
            "en": [r for r in answered if r["lang"] == "en"],
        }
        if decision == questions.REGISTRATION:
            subsets["without_approximate"] = [r for r in answered if r["basis"] != "approximate"]
            subsets["d2_pages"] = [r for r in answered if r["d2_page"]]
        groups[f"{decision}/{variant}"] = {
            "errors": len(group) - len(answered),
            "seconds": _spread([r["seconds"] for r in answered]),
            "tokens": _spread([r["tokens"] for r in answered if r["tokens"] is not None]),
            "chars": _spread([r["chars"] for r in answered]),
            "questions": {
                name: auroc(
                    [r["p"][name] for r in answered if r["labels"].get(name, r["label"])],
                    [r["p"][name] for r in answered if not r["labels"].get(name, r["label"])],
                )
                for name in (answered[0]["p"] if answered else {})
            },
            **{name: _measure(subset, TARGETS[decision]) for name, subset in subsets.items()},
        }
    return {"lows": list(LOWS), "targets": TARGETS, "groups": groups}


def _measure(rows: list[dict], target: float) -> dict:
    positives = [r["score"] for r in rows if r["label"]]
    negatives = [r["score"] for r in rows if not r["label"]]
    table = bands(rows, LOWS)
    return {
        "n": len(rows),
        "positives": len(positives),
        "auroc": auroc(positives, negatives),
        "agree_at_half": _share(sum((r["score"] >= 0.5) == r["label"] for r in rows), len(rows)),
        "best": best(table, target),
        "bands": table,
    }


def auroc(positives: list[float], negatives: list[float]) -> float | None:
    """The chance that a positive scores above a negative, a tie counting half; 0.5 is
    chance. None without both."""
    if not positives or not negatives:
        return None
    wins = sum((p > n) + 0.5 * (p == n) for p in positives for n in negatives)
    return round(wins / (len(positives) * len(negatives)), 3)


def bands(rows: list[dict], lows: Iterable[float]) -> list[dict]:
    """For each `lo`: the share of positives scoring at least `lo`, which a skip below it
    keeps; the positives it skips; and every page it skips."""
    positives = [r["score"] for r in rows if r["label"]]
    return [
        {
            "lo": lo,
            "recall": _share(sum(s >= lo for s in positives), len(positives)),
            "false_skips": sum(s < lo for s in positives),
            "skipped": sum(r["score"] < lo for r in rows),
        }
        for lo in lows
    ]


def best(table: list[dict], target: float) -> dict | None:
    """The band that skips most while keeping `target` recall; None when none does."""
    kept = [b for b in table if b["recall"] is not None and b["recall"] >= target]
    return max(kept, key=lambda b: b["skipped"]) if kept else None


def _share(part: int, whole: int) -> float | None:
    return round(part / whole, 3) if whole else None


def _spread(values: list[float]) -> dict | None:
    if not values:
        return None
    p90 = quantiles(values, n=10)[-1] if len(values) > 1 else values[0]
    return {"median": median(values), "p90": round(p90, 3), "total": round(sum(values), 3)}


def _language(flat: str) -> str:
    return "de" if len(_GERMAN.findall(flat)) > len(_ENGLISH.findall(flat)) else "en"
