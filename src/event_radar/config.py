import os
from dataclasses import dataclass
from pathlib import Path


def user_agent() -> str:
    contact = os.environ.get("RADAR_UA_CONTACT")
    suffix = f"; contact {contact}" if contact else ""
    return f"munich-event-radar/0.1 (personal non-commercial event tracker, low rate{suffix})"


@dataclass(frozen=True)
class Settings:
    repo_dir: Path
    fixtures_dir: Path
    database_url: str | None
    openrouter_key: str | None
    llm_daily_cap: int
    groq_key: str | None
    groq_daily_cap: int
    healthcheck_url: str | None


def _dotenv(path: Path) -> None:
    """Local development: a gitignored `.env` in the repo; the real environment wins."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        name, sep, value = line.strip().partition("=")
        if sep and not name.startswith("#"):
            os.environ.setdefault(name.strip(), value.strip().strip("\"'"))


def load() -> Settings:
    repo = Path(__file__).resolve().parents[2]
    _dotenv(repo / ".env")
    return Settings(
        repo_dir=repo,
        fixtures_dir=Path(os.environ.get("RADAR_FIXTURES_DIR") or repo / "eval" / "fixtures"),
        database_url=os.environ.get("RADAR_DATABASE_URL"),
        openrouter_key=os.environ.get("OPENROUTER_API_KEY"),
        llm_daily_cap=int(os.environ.get("RADAR_LLM_DAILY_CAP", "35")),
        groq_key=os.environ.get("GROQ_API_KEY") or None,
        groq_daily_cap=int(os.environ.get("RADAR_GROQ_DAILY_CAP", "30")),
        healthcheck_url=os.environ.get("HEALTHCHECK_URL"),
    )
