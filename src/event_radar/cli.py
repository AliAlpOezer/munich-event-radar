import argparse
import json
from pathlib import Path

from . import config, golden, jobs, sqlite_import
from .extract import Extractor
from .extract.router import Router
from .fetch import Fetcher
from .model import field_value
from .resolve import local_minute
from .store import Store


def seed(settings: config.Settings, args: argparse.Namespace) -> None:
    print(f"seeded {jobs.seed(settings)} organisers")


def harvest(settings: config.Settings, args: argparse.Namespace) -> None:
    print(json.dumps(jobs.harvest(settings)))


def browse(settings: config.Settings, args: argparse.Namespace) -> None:
    print(json.dumps(jobs.browse(settings)))


def intakes(settings: config.Settings, args: argparse.Namespace) -> None:
    print(f"worked {jobs.intakes(settings)} intakes")


def board(settings: config.Settings, args: argparse.Namespace) -> None:
    rows = []
    for event in Store(settings.database_url).events.all():
        if event["tier"] not in {"A", "B"}:
            continue
        fields = event["fields"]
        rows.append(
            (
                local_minute(field_value(fields, "starts_at")) or "?",
                event["tier"],
                field_value(fields, "title"),
                field_value(fields, "reg_availability"),
                local_minute(field_value(fields, "reg_closes_at")),
                field_value(fields, "reg_url"),
                event["user_status"],
            )
        )
    for row in sorted(rows):
        print(" | ".join(str(c) if c is not None else "-" for c in row))


def freeze(settings: config.Settings, args: argparse.Namespace) -> None:
    fetcher = Fetcher(Store(settings.database_url), config.user_agent())
    manifest = golden.freeze(settings.repo_dir, settings.fixtures_dir, fetcher)
    print(json.dumps({u: e.get("file") or e.get("error") for u, e in manifest.items()}, indent=1))


def replay(settings: config.Settings, args: argparse.Namespace) -> None:
    extractor = None
    if settings.openrouter_key:
        cache = Store(settings.database_url)
        extractor = Extractor(Router(settings.openrouter_key, cache, settings.llm_daily_cap), cache)
    report = golden.replay(
        settings.repo_dir, settings.fixtures_dir, settings.database_url, extractor
    )
    print(json.dumps(report, indent=1, ensure_ascii=False))
    raise SystemExit(0 if report["pass"] else 1)


def import_sqlite(settings: config.Settings, args: argparse.Namespace) -> None:
    print(json.dumps(sqlite_import.run(Store(settings.database_url), args.path)))


def grant_admin(settings: config.Settings, args: argparse.Namespace) -> None:
    store = Store(settings.database_url)
    print(json.dumps(store.users.grant_admin(args.email, args.owner)))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="event-radar")
    commands = parser.add_subparsers(dest="command", required=True)
    for command, help_text in (
        (seed, "add the organisers and sources of seed/organisers.yaml"),
        (harvest, "fetch the due sources, extract with the LLM, judge"),
        (browse, "render the due JavaScript-only sources"),
        (intakes, "work the queued intakes, then exit"),
        (board, "print the A and B events"),
        (freeze, "save the golden pages to RADAR_FIXTURES_DIR"),
        (replay, "score the golden events from the saved pages"),
    ):
        commands.add_parser(command.__name__, help=help_text).set_defaults(run=command)
    move = commands.add_parser("import-sqlite", help="copy a SQLite store into an empty one")
    move.add_argument("path", type=Path)
    move.set_defaults(run=import_sqlite)
    admin = commands.add_parser("grant-admin", help="make a user who has logged in an admin")
    admin.add_argument("email", help="the email the user logged in with")
    admin.add_argument(
        "--owner",
        action="store_true",
        help="the owner, who also receives the statuses kept before logins",
    )
    admin.set_defaults(run=grant_admin)
    return parser


def main() -> None:
    args = _parser().parse_args()
    args.run(config.load(), args)


if __name__ == "__main__":
    main()
