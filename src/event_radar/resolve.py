import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from difflib import SequenceMatcher
from zoneinfo import ZoneInfo

from .model import Observation, SourceClass
from .platforms import canonical

CLASS_RANK = {SourceClass.TICKET: 0, SourceClass.OWN: 1, SourceClass.AGG: 2}
REGISTRATION_FIELDS = {"reg_mode", "reg_url", "reg_opens_at", "reg_closes_at",
                       "reg_availability", "spots_left"}
TITLE_SIMILARITY = 0.6
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


def local_date(value: object) -> str | None:
    text = str(value or "")
    if len(text) <= 10:
        return text if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text) else None
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text[:10] if re.match(r"\d{4}-\d{2}-\d{2}", text) else None
    if moment.tzinfo:
        moment = moment.astimezone(BERLIN)
    return moment.date().isoformat()


def _aliases(obs: Observation) -> set[str]:
    aliases = set(obs.platform_keys)
    url = obs.fields.get("event_url")
    if url and url.value:
        aliases.add("url:" + canonical(str(url.value)))
    if not aliases:
        title = obs.fields.get("title")
        start = obs.fields.get("starts_at")
        aliases.add(f"obs:{canonical(obs.url)}|{_norm(title and title.value)}|"
                    f"{local_date(start and start.value)}")
    return aliases


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


def _first(obs_list: list[Observation], name: str) -> str:
    for obs in obs_list:
        if name in obs.fields:
            return _norm(obs.fields[name].value)
    return ""


def _fuzzy_match(a: Resolved, b: Resolved) -> bool:
    dates_a = {local_date(o.fields["starts_at"].value) for o in a.observations if "starts_at" in o.fields}
    dates_b = {local_date(o.fields["starts_at"].value) for o in b.observations if "starts_at" in o.fields}
    if not (dates_a & dates_b) - {None} or _conflict(a.aliases, b.aliases):
        return False
    title_a, title_b = _first(a.observations, "title"), _first(b.observations, "title")
    return bool(title_a and title_b) and (
        SequenceMatcher(None, title_a, title_b).ratio() >= TITLE_SIMILARITY
        or title_a in title_b or title_b in title_a
    )


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
            chosen[name] = {"value": evidence.value, "quote": evidence.quote,
                            "observation": obs.id, "class": obs.source_class.value}
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
            if candidate and str(candidate.value).startswith(str(current["value"])) \
                    and len(str(candidate.value)) > 10:
                chosen[name] = {"value": candidate.value, "quote": candidate.quote,
                                "observation": obs.id, "class": obs.source_class.value}
                break


def resolve(observations: list[Observation]) -> list[Resolved]:
    current = _latest_only(observations)
    uf = _UnionFind()
    alias_of = {}
    for obs in current:
        aliases = _aliases(obs)
        alias_of[obs.id] = aliases
        first, *rest = sorted(aliases)
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
