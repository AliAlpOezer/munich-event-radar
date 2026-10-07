import os
from dataclasses import dataclass
from pathlib import Path

def user_agent() -> str:
    contact = os.environ.get("RADAR_UA_CONTACT")
    suffix = f"; contact {contact}" if contact else ""
    return f"munich-event-radar/0.1 (personal non-commercial event tracker, low rate{suffix})"


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    repo_dir: Path
    openrouter_key: str | None
    llm_daily_cap: int
    tavily_key: str | None
    telegram_token: str | None
    telegram_chat_id: str | None
    google_key_file: Path | None
    calendar_id: str | None
    healthcheck_url: str | None
    eventbrite_token: str | None

    @property
    def db_path(self) -> Path:
        return self.data_dir / "radar.db"

    @property
    def snapshot_dir(self) -> Path:
        return self.data_dir / "snapshots"


def _path(name: str) -> Path | None:
    value = os.environ.get(name)
    return Path(value) if value else None


def load() -> Settings:
    repo = Path(__file__).resolve().parents[2]
    return Settings(
        data_dir=_path("RADAR_DATA_DIR") or repo / "data",
        repo_dir=repo,
        openrouter_key=os.environ.get("OPENROUTER_API_KEY"),
        llm_daily_cap=int(os.environ.get("RADAR_LLM_DAILY_CAP", "35")),
        tavily_key=os.environ.get("TAVILY_API_KEY"),
        telegram_token=os.environ.get("TELEGRAM_BOT_TOKEN"),
        telegram_chat_id=os.environ.get("TELEGRAM_CHAT_ID"),
        google_key_file=_path("GOOGLE_SERVICE_ACCOUNT_FILE"),
        calendar_id=os.environ.get("RADAR_CALENDAR_ID"),
        healthcheck_url=os.environ.get("HEALTHCHECK_URL"),
        eventbrite_token=os.environ.get("EVENTBRITE_TOKEN"),
    )
