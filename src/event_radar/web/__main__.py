import argparse

import uvicorn

from .. import config
from ..intake import IntakeService
from ..store import Store
from . import create_app


def main() -> None:
    parser = argparse.ArgumentParser(prog="event-radar-web")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8010)
    args = parser.parse_args()
    settings = config.load()
    store = Store(settings.database_url)
    # Submissions and decisions only; `event-radar intake-worker` works the queue (ADR 0005).
    app = create_app(store, IntakeService(settings, store))
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
