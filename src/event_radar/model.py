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


class UserStatus(StrEnum):
    NONE = "none"
    APPLIED = "applied"
    REGISTERED = "registered"
    SKIPPED = "skipped"


USER_TRANSITIONS: dict[UserStatus, set[UserStatus]] = {
    UserStatus.NONE: {UserStatus.APPLIED, UserStatus.REGISTERED, UserStatus.SKIPPED},
    UserStatus.APPLIED: {UserStatus.REGISTERED, UserStatus.SKIPPED, UserStatus.NONE},
    UserStatus.REGISTERED: {UserStatus.NONE},
    UserStatus.SKIPPED: {UserStatus.NONE},
}

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
)


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
