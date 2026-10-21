import argparse
import json
import time
from pathlib import Path

from . import config, golden, jobs, sqlite_import
from .decide import calibrate as decide_calibrate
from .decide import questions
from .decide.systemone import SystemOne
from .extract import Extractor
from .extract.router import Router, providers
from .fetch import Fetcher
from .model import IntakeKind, field_value
from .profile import Profile
from .resolve import local_minute
from .store import Store


def seed(settings: config.Settings, args: argparse.Namespace) -> None:
    print(f"seeded {jobs.seed(settings)} organisers")


def harvest(settings: config.Settings, args: argparse.Namespace) -> None:
    print(json.dumps(jobs.harvest(settings)))


def browse(settings: config.Settings, args: argparse.Namespace) -> None:
    print(json.dumps(jobs.browse(settings)))


def intakes(settings: config.Settings, args: argparse.Namespace) -> None:
    started = time.monotonic()
    worked = jobs.intakes(settings, args.kind)
    print(f"worked {worked} intakes in {time.monotonic() - started:.1f} s")


def queued(settings: config.Settings, args: argparse.Namespace) -> None:
    print(jobs.queued(settings, args.kind))


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
    if found := providers(settings):
        cache = Store(settings.database_url)
        extractor = Extractor(Router(found, cache), cache)
    report = golden.replay(
        settings.repo_dir, settings.fixtures_dir, settings.database_url, extractor
    )
    print(json.dumps(report, indent=1, ensure_ascii=False))
    raise SystemExit(0 if report["pass"] else 1)


def search(settings: config.Settings, args: argparse.Namespace) -> None:
    summary = jobs.search_web(settings, args.dry_run)
    print(json.dumps(summary, indent=1, ensure_ascii=False))
    raise SystemExit(1 if summary.get("failed") else 0)


def calibrate(settings: config.Settings, args: argparse.Namespace) -> None:
    if not settings.database_url:
        raise SystemExit("RADAR_DATABASE_URL is not set")
    profile = Profile.load(settings.repo_dir / "profile.yaml")
    if args.labels_only:
        items = decide_calibrate.labelled(settings.database_url, profile)
        print(json.dumps(decide_calibrate.census(items), indent=1))
        return
    if not args.server or not args.model:
        raise SystemExit("--server and --model are needed unless --labels-only")
    client = SystemOne(
        args.server,
        args.model,
        timeout=args.timeout,
        options={k: json.loads(v) for k, v in (o.split("=", 1) for o in args.option)},
    )
    summary = decide_calibrate.run(
        settings.database_url,
        profile,
        client,
        args.out,
        args.decision or [questions.IN_SCOPE, questions.REGISTRATION],
        args.limit,
    )
    print(json.dumps(summary, indent=1, ensure_ascii=False))


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
        (board, "print the A and B events"),
        (freeze, "save the golden pages to RADAR_FIXTURES_DIR"),
        (replay, "score the golden events from the saved pages"),
    ):
        commands.add_parser(command.__name__, help=help_text).set_defaults(run=command)
    drain = commands.add_parser("intakes", help="work the queued intakes, then exit")
    drain.add_argument("--kind", type=IntakeKind, choices=list(IntakeKind), help="only this kind")
    drain.set_defaults(run=intakes)
    waiting = commands.add_parser(
        "queued", help="print how many intakes of a kind wait for a drain"
    )
    waiting.add_argument("kind", type=IntakeKind, choices=list(IntakeKind))
    waiting.set_defaults(run=queued)
    finder = commands.add_parser(
        "search", help="search the web for events and organisers, for an admin's review"
    )
    finder.add_argument(
        "--dry-run", action="store_true", help="print each hit's verdict and propose nothing"
    )
    finder.set_defaults(run=search)
    measure = commands.add_parser(
        "calibrate",
        help="measure a decision model against the LLM's cached answers (read-only)",
    )
    measure.add_argument("--server", help="the model server, such as http://127.0.0.1:8000")
    measure.add_argument("--model", help="the model name the server is sent")
    measure.add_argument(
        "--option",
        action="append",
        default=[],
        metavar="NAME=JSON",
        help="a field sent in every request, such as max_len=8192 (repeatable)",
    )
    measure.add_argument(
        "--decision",
        action="append",
        choices=[questions.IN_SCOPE, questions.REGISTRATION],
        help="the decisions to measure (repeatable; default all)",
    )
    measure.add_argument("--limit", type=int, help="ask about the first N pages of each state")
    measure.add_argument("--timeout", type=float, default=300.0, help="seconds per request")
    measure.add_argument("--out", type=Path, default=Path("calibration"))
    measure.add_argument(
        "--labels-only", action="store_true", help="count the labelled pages, ask nothing"
    )
    measure.set_defaults(run=calibrate)
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
