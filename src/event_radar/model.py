from dataclasses import dataclass, field
from enum import StrEnum


class SourceClass(StrEnum):
    TICKET = "TICKET"
    OWN = "OWN"
    AGG = "AGG"


class Kind(StrEnum):
    FEED = "feed"
    LISTING = "listing"
    EVENT_PAGE = "event_page"


class Tier(StrEnum):
    A = "A"
    B = "B"
    C = "C"


class OrganiserStatus(StrEnum):
    """An organiser in the registry. A removed one is a tombstone that is never re-added."""

    ACTIVE = "active"
    DORMANT = "dormant"
    DEMOTED = "demoted"
    REMOVED = "removed"


class IntakeState(StrEnum):
    """An intake's progress: an organisation's (ADR 0003) or an event's, which skips
    profiling, review and finding (ADR 0014). Working states are requeued when a job is cut
    off mid-step."""

    REVIEW = "review"
    QUEUED = "queued"
    CHECKING = "checking"
    PROFILING = "profiling"
    FINDING = "finding"
    HARVESTING = "harvesting"
    DONE = "done"
    REJECTED = "rejected"
    FAILED = "failed"
    REMOVED = "removed"  # an added event an admin took off the board


class IntakeKind(StrEnum):
    ORGANISATION = "organisation"
    EVENT = "event"


WORKING_INTAKE_STATES = (
    IntakeState.CHECKING,
    IntakeState.PROFILING,
    IntakeState.FINDING,
    IntakeState.HARVESTING,
)


class JobState(StrEnum):
    """A page waiting for, or done with, the LLM."""

    PENDING = "pending"
    DONE = "done"
    NEEDS_LOOK = "needs_look"
    SKIPPED = "skipped"  # no call owed: the judge drops the event, or it is not about AI


class RunOutcome(StrEnum):
    OK = "ok"
    FAILED = "failed"


EVENT_FIELDS = (
    "title",
    "starts_at",
    "ends_at",
    "venue",
    "address",
    "city",
    "attendance",
    "organiser",
    "description",
    "event_url",
    "image",
    "reg_mode",
    "reg_url",
    "reg_opens_at",
    "reg_closes_at",
    "reg_availability",
    "spots_left",
    "price",
    "prerequisites",
    "kind",
    "ai_relevance",
    "audience",
)


def field_value(fields: dict, name: str) -> object:
    """The value of one field of a stored event, or None."""
    return (fields.get(name) or {}).get("value")


@dataclass(frozen=True)
class Evidence:
    value: object
    quote: str | None = None


@dataclass(frozen=True)
class ParsedEvent:
    fields: dict[str, Evidence]
    platform_keys: tuple[str, ...] = ()
    links: tuple[str, ...] = ()


@dataclass(frozen=True)
class ParseResult:
    method: str
    events: tuple[ParsedEvent, ...] = ()
    follow: tuple[str, ...] = ()
    needs_llm: bool = False


# Outside the radar's scope (ADR 0011), or aimed at an audience other than adults (ADR
# 0012): such an event is not stored.
OFF_TOPIC = "off_topic"
AUDIENCE = "audience"
NOT_STORED = frozenset({OFF_TOPIC, AUDIENCE})


@dataclass(frozen=True)
class Verdict:
    """The judge's tier for an event, or why it was dropped. Every drop is a fact about the
    event except `OFF_TOPIC`, which the LLM's reading of the page can still change."""

    tier: str | None
    reasons: tuple[str, ...]
    drop_reason: str | None = None


@dataclass(frozen=True)
class JudgedEvent:
    """One resolved event with its verdict, as the store keeps it."""

    aliases: set[str]
    fields: dict[str, dict]
    merge_basis: str
    verdict: Verdict
    sources: list[str]


@dataclass
class Observation:
    id: int
    snapshot_hash: str
    url: str
    source_class: SourceClass
    method: str
    platform_keys: list[str]
    fields: dict[str, Evidence]
    observed_at: str
    links: list[str] = field(default_factory=list)
