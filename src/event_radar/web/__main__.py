import argparse

import uvicorn

from .. import config
from ..store import Store
from . import create_app


def main() -> None:
    parser = argparse.ArgumentParser(prog="event-radar-web")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8010)
    args = parser.parse_args()
    app = create_app(Store(config.load().db_path))
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
