import argparse
import json
from pathlib import Path

from . import config, golden, runner
from .fetch import Fetcher
from .resolve import local_minute
from .snapshots import SnapshotStore
from .store import Store


def _freeze(settings: config.Settings) -> None:
    store = Store(settings.db_path)
    snapshots = SnapshotStore(settings.snapshot_dir)
    fetcher = Fetcher(store, snapshots, config.user_agent())
    manifest = golden.freeze(settings.repo_dir, fetcher, snapshots)
    print(json.dumps({u: e.get("file") or e.get("error") for u, e in manifest.items()}, indent=1))


def _replay(settings: config.Settings) -> None:
    extractor = None
    if settings.openrouter_key:
        from .extract import Extractor
        from .extract.router import Router

        cache = Store(settings.db_path)
        extractor = Extractor(Router(settings.openrouter_key, cache, settings.llm_daily_cap), cache)
    report = golden.replay(Path(settings.repo_dir), extractor)
    print(json.dumps(report, indent=1, ensure_ascii=False))
    raise SystemExit(0 if report["pass"] else 1)


def _value(fields: dict, name: str) -> object:
    return (fields.get(name) or {}).get("value")


def _board(settings: config.Settings) -> None:
    rows = []
    for event in Store(settings.db_path).events():
        if event["tier"] not in {"A", "B"}:
            continue
        fields = json.loads(event["fields"])
        rows.append(
            (
                local_minute(_value(fields, "starts_at")) or "?",
                event["tier"],
                _value(fields, "title"),
                _value(fields, "reg_availability"),
                _value(fields, "reg_closes_at"),
                _value(fields, "reg_url"),
                event["user_status"],
            )
        )
    for row in sorted(rows):
        print(" | ".join(str(c) if c is not None else "-" for c in row))


COMMANDS = {
    "freeze": _freeze,
    "replay": _replay,
    "seed": lambda s: print(f"seeded {runner.seed(s)} organisers"),
    "run": lambda s: print(json.dumps(runner.run(s))),
    "board": _board,
}


def main() -> None:
    parser = argparse.ArgumentParser(prog="event-radar")
    parser.add_argument("command", choices=sorted(COMMANDS))
    args = parser.parse_args()
    COMMANDS[args.command](config.load())


if __name__ == "__main__":
    main()
