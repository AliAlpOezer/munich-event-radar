import argparse
import json

from . import config, golden
from .fetch import Fetcher
from .snapshots import SnapshotStore
from .store import Store


def _freeze(settings: config.Settings) -> None:
    store = Store(settings.db_path)
    snapshots = SnapshotStore(settings.snapshot_dir)
    manifest = golden.freeze(settings.repo_dir, Fetcher(store, snapshots, config.user_agent()), snapshots)
    print(json.dumps({url: e.get("file") or e.get("error") for url, e in manifest.items()}, indent=1))


COMMANDS = {"freeze": _freeze}


def main() -> None:
    parser = argparse.ArgumentParser(prog="event-radar")
    parser.add_argument("command", choices=sorted(COMMANDS))
    args = parser.parse_args()
    COMMANDS[args.command](config.load())


if __name__ == "__main__":
    main()
