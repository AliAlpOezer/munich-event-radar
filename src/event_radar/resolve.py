import contextlib
import re
import unicodedata
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from difflib import SequenceMatcher
from zoneinfo import ZoneInfo

from .model import Evidence, Observation, SourceClass
from .platforms import canonical

CLASS_RANK = {SourceClass.TICKET: 0, SourceClass.OWN: 1, SourceClass.AGG: 2}
REGISTRATION_FIELDS = {
    "reg_mode",
    "reg_url",
    "reg_opens_at",
    "reg_closes_at",
    "reg_availability",
    "spots_left",
}
TITLE_SIMILARITY = 0.6
STEM = 6
SAME_START_MINUTES = 60
TITLE_FILLER = frozenset(
    "the for and with from about der die das und fur mit von uber munich munchen"
    " lecture talk meetup event".split()
)
BERLIN = ZoneInfo("Europe/Berlin")


@dataclass
class Resolved:
    aliases: set[str]
    observations: list[Observation]
    merge_basis: str
    fields: dict[str, dict] = field(default_factory=dict)

    @property
    def classes(self) -> set[SourceClass]:
        return {o.source_class for o in self.observations}


def _norm(text: object) -> str:
    folded = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", folded.lower()).strip()


def _local(text: str) -> datetime | None:
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment.astimezone(BERLIN) if moment.tzinfo else moment


def local_date(value: object) -> str | None:
    text = str(value or "")
    if len(text) <= 10:
        return text if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text) else None
    if moment := _local(text):
        return moment.date().isoformat()
    return text[:10] if re.match(r"\d{4}-\d{2}-\d{2}", text) else None


def local_minute(value: object) -> str | None:
    text = str(value or "")
    if len(text) <= 10:
        return local_date(text)
    moment = _local(text)
    return moment.strftime("%Y-%m-%dT%H:%M") if moment else text[:16]


def aliases(url: str, platform_keys: Iterable[str], fields: dict[str, Evidence]) -> set[str]:
    """The keys an event seen on page `url` is known by: its platform keys and event URL,
    else the page, its title and its day."""
    found = set(platform_keys)
    event_url = fields.get("event_url")
    if event_url and event_url.value:
        found.add("url:" + canonical(str(event_url.value)))
    if not found:
        title = fields.get("title")
        start = fields.get("starts_at")
        found.add(
            f"obs:{canonical(url)}|{_norm(title and title.value)}|"
            f"{local_date(start and start.value)}"
        )
    return found


def _latest_only(observations: list[Observation]) -> list[Observation]:
    newest: dict[tuple[str, str], str] = {}
    for obs in observations:
        key = (obs.url, obs.method.split("@")[0])
        newest[key] = max(newest.get(key, ""), obs.observed_at)
    return [o for o in observations if o.observed_at == newest[(o.url, o.method.split("@")[0])]]


class _UnionFind:
    def __init__(self):
        self.parent: dict[str, str] = {}

    def find(self, x: str) -> str:
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: str, b: str) -> None:
        self.parent[self.find(a)] = self.find(b)


def _platforms(aliases: set[str]) -> dict[str, set[str]]:
    by_platform = defaultdict(set)
    for alias in aliases:
        platform, _, ident = alias.partition(":")
        if platform not in {"url", "obs"}:
            by_platform[platform].add(ident)
    return by_platform


def _conflict(a: set[str], b: set[str]) -> bool:
    pa, pb = _platforms(a), _platforms(b)
    return any(pa[p].isdisjoint(pb[p]) for p in pa.keys() & pb.keys())


def _values(group: Resolved, name: str) -> list[str]:
    return [str(o.fields[name].value) for o in group.observations if name in o.fields]


def _stems(title: str) -> set[str]:
    return {
        w[:STEM] for w in title.split() if len(w) > 2 and not w.isdigit() and w not in TITLE_FILLER
    }


def _similar(a: str, b: str) -> bool:
    if SequenceMatcher(None, a, b).ratio() >= TITLE_SIMILARITY or a in b or b in a:
        return True
    short, long = sorted((_stems(a), _stems(b)), key=len)
    return len(short) >= 2 and short <= long


def _starts(group: Resolved) -> list[datetime]:
    moments = []
    for value in _values(group, "starts_at"):
        if len(value) > 10:
            with contextlib.suppress(ValueError):
                moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
                moments.append(moment if moment.tzinfo else moment.replace(tzinfo=BERLIN))
    return moments


def _same_start(a: Resolved, b: Resolved) -> bool:
    starts_a, starts_b = _starts(a), _starts(b)
    if not (starts_a and starts_b):
        return True
    return any(
        abs(x - y).total_seconds() <= SAME_START_MINUTES * 60 for x in starts_a for y in starts_b
    )


def _fuzzy_match(a: Resolved, b: Resolved) -> bool:
    dates_a = {local_date(v) for v in _values(a, "starts_at")}
    dates_b = {local_date(v) for v in _values(b, "starts_at")}
    if not (dates_a & dates_b) - {None} or _conflict(a.aliases, b.aliases):
        return False
    titles_a = {_norm(t) for t in _values(a, "title")} - {""}
    titles_b = {_norm(t) for t in _values(b, "title")} - {""}
    return _same_start(a, b) and any(_similar(x, y) for x in titles_a for y in titles_b)


def _rank(obs: Observation) -> tuple:
    return (CLASS_RANK[obs.source_class], obs.method.startswith("llm"), _neg(obs.observed_at))


def _neg(stamp: str) -> tuple:
    return tuple(-ord(c) for c in stamp)


def _pick(group: Resolved) -> dict[str, dict]:
    chosen: dict[str, dict] = {}
    for obs in sorted(group.observations, key=_rank):
        for name, evidence in obs.fields.items():
            if name in chosen or evidence.value in (None, ""):
                continue
            if name in REGISTRATION_FIELDS and obs.source_class is SourceClass.AGG:
                continue
            chosen[name] = {
                "value": evidence.value,
                "quote": evidence.quote,
                "observation": obs.id,
                "class": obs.source_class.value,
            }
    _refine_time(group, chosen)
    if not group.classes - {SourceClass.AGG}:
        chosen["reg_availability"] = {"value": "unchecked", "observation": None, "class": None}
    chosen.setdefault("reg_availability", {"value": "unknown", "observation": None, "class": None})
    return chosen


def _refine_time(group: Resolved, chosen: dict[str, dict]) -> None:
    for name in ("starts_at", "ends_at"):
        current = chosen.get(name)
        if not current or len(str(current["value"])) > 10:
            continue
        for obs in sorted(group.observations, key=_rank):
            candidate = obs.fields.get(name)
            if (
                candidate
                and str(candidate.value).startswith(str(current["value"]))
                and len(str(candidate.value)) > 10
            ):
                chosen[name] = {
                    "value": candidate.value,
                    "quote": candidate.quote,
                    "observation": obs.id,
                    "class": obs.source_class.value,
                }
                break


def resolve(observations: list[Observation]) -> list[Resolved]:
    current = _latest_only(observations)
    uf = _UnionFind()
    alias_of = {}
    for obs in current:
        keys = aliases(obs.url, obs.platform_keys, obs.fields)
        alias_of[obs.id] = keys
        first, *rest = sorted(keys)
        for alias in rest:
            uf.union(alias, first)
    clusters: dict[str, Resolved] = {}
    for obs in current:
        root = uf.find(next(iter(alias_of[obs.id])))
        group = clusters.setdefault(root, Resolved(set(), [], "platform"))
        group.aliases |= alias_of[obs.id]
        group.observations.append(obs)
    groups = list(clusters.values())
    merged: list[Resolved] = []
    for group in groups:
        target = next((m for m in merged if _fuzzy_match(m, group)), None)
        if target:
            target.aliases |= group.aliases
            target.observations += group.observations
            target.merge_basis = "fuzzy"
        else:
            merged.append(group)
    for group in merged:
        if group.merge_basis != "fuzzy":
            group.merge_basis = "platform" if _platforms(group.aliases) else "url"
        group.fields = _pick(group)
    return merged
