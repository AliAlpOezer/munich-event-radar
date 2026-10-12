import argparse
import json
from pathlib import Path

from . import config, golden, runner, sqlite_import
from .fetch import Fetcher
from .resolve import local_minute
from .store import Store


def _freeze(settings: config.Settings) -> None:
    fetcher = Fetcher(Store(settings.database_url), config.user_agent())
    manifest = golden.freeze(settings.repo_dir, fetcher)
    print(json.dumps({u: e.get("file") or e.get("error") for u, e in manifest.items()}, indent=1))


def _replay(settings: config.Settings) -> None:
    extractor = None
    if settings.openrouter_key:
        from .extract import Extractor
        from .extract.router import Router

        cache = Store(settings.database_url)
        extractor = Extractor(Router(settings.openrouter_key, cache, settings.llm_daily_cap), cache)
    report = golden.replay(Path(settings.repo_dir), settings.database_url, extractor)
    print(json.dumps(report, indent=1, ensure_ascii=False))
    raise SystemExit(0 if report["pass"] else 1)


def _value(fields: dict, name: str) -> object:
    return (fields.get(name) or {}).get("value")


def _board(settings: config.Settings) -> None:
    rows = []
    for event in Store(settings.database_url).events():
        if event["tier"] not in {"A", "B"}:
            continue
        fields = event["fields"]
        rows.append(
            (
                local_minute(_value(fields, "starts_at")) or "?",
                event["tier"],
                _value(fields, "title"),
                _value(fields, "reg_availability"),
                local_minute(_value(fields, "reg_closes_at")),
                _value(fields, "reg_url"),
                event["user_status"],
            )
        )
    for row in sorted(rows):
        print(" | ".join(str(c) if c is not None else "-" for c in row))


def _intakes(settings: config.Settings) -> None:
    from .intake import IntakeService

    worked = IntakeService(settings, Store(settings.database_url)).drain()
    print(f"worked {worked} intakes")


COMMANDS = {
    "freeze": _freeze,
    "replay": _replay,
    "seed": lambda s: print(f"seeded {runner.seed(s)} organisers"),
    "run": lambda s: print(json.dumps(runner.run(s))),
    "browse": lambda s: print(json.dumps(runner.browse(s))),
    "board": _board,
    "intakes": _intakes,
}


def main() -> None:
    parser = argparse.ArgumentParser(prog="event-radar")
    parser.add_argument("command", choices=sorted([*COMMANDS, "import-sqlite", "grant-admin"]))
    parser.add_argument(
        "target", nargs="?", help="import-sqlite: the SQLite file; grant-admin: the user's email"
    )
    parser.add_argument(
        "--owner",
        action="store_true",
        help="grant-admin: the owner, who also receives the statuses kept before logins",
    )
    args = parser.parse_args()
    settings = config.load()
    if args.command == "import-sqlite":
        if not args.target:
            parser.error("import-sqlite needs the path of the SQLite file")
        print(json.dumps(sqlite_import.run(Store(settings.database_url), Path(args.target))))
        return
    if args.command == "grant-admin":
        if not args.target:
            parser.error("grant-admin needs the email the user logged in with")
        print(json.dumps(Store(settings.database_url).grant_admin(args.target, args.owner)))
        return
    COMMANDS[args.command](settings)


if __name__ == "__main__":
    main()
