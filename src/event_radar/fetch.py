import time
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx
from protego import Protego

from .snapshots import SnapshotStore
from .store import Store

MAX_BYTES = 5_000_000
WALL_CLOCK_S = 30.0


@dataclass(frozen=True)
class Fetched:
    url: str
    status: int | None
    content_hash: str | None
    content_type: str | None
    changed: bool
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.content_hash is not None and self.error is None


class RobotsDisallowed(Exception):
    pass


class Fetcher:
    def __init__(
        self, store: Store, snapshots: SnapshotStore, user_agent: str,
        min_interval_s: float = 5.0, client: httpx.Client | None = None,
    ):
        self.store = store
        self.snapshots = snapshots
        self.user_agent = user_agent
        self.min_interval_s = min_interval_s
        self.client = client or httpx.Client(
            headers={"User-Agent": user_agent}, follow_redirects=True, timeout=15.0
        )
        self._robots: dict[str, Protego | None] = {}
        self._last_hit: dict[str, float] = {}

    def get(self, url: str) -> Fetched:
        try:
            self._check_robots(url)
            return self._get(url)
        except RobotsDisallowed:
            return self._fail(url, None, "robots_disallowed")
        except (httpx.HTTPError, TimeoutError) as exc:
            return self._fail(url, None, f"{type(exc).__name__}: {exc}"[:300])

    def _get(self, url: str) -> Fetched:
        previous = self.store.latest_snapshot(url)
        headers = {}
        if previous and previous["etag"]:
            headers["If-None-Match"] = previous["etag"]
        if previous and previous["last_modified"]:
            headers["If-Modified-Since"] = previous["last_modified"]
        status, body, response_headers = self._bounded_get(url, headers)
        if status == 304 and previous:
            return Fetched(url, 304, previous["content_hash"], previous["content_type"], False)
        if status >= 400:
            return self._fail(url, status, f"http_{status}")
        digest = self.snapshots.put(body)
        content_type = response_headers.get("content-type")
        self.store.record_snapshot(
            url, content_hash=digest, http_status=status, content_type=content_type,
            etag=response_headers.get("etag"), last_modified=response_headers.get("last-modified"),
        )
        changed = not previous or previous["content_hash"] != digest
        return Fetched(url, status, digest, content_type, changed)

    def _bounded_get(self, url: str, headers: dict) -> tuple[int, bytes, httpx.Headers]:
        self._pace(url)
        deadline = time.monotonic() + WALL_CLOCK_S
        chunks, size = [], 0
        with self.client.stream("GET", url, headers=headers) as response:
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > MAX_BYTES:
                    raise httpx.HTTPError(f"body over {MAX_BYTES} bytes")
                if time.monotonic() > deadline:
                    raise TimeoutError(f"over {WALL_CLOCK_S}s wall clock")
                chunks.append(chunk)
            return response.status_code, b"".join(chunks), response.headers

    def _fail(self, url: str, status: int | None, error: str) -> Fetched:
        self.store.record_snapshot(url, http_status=status, error=error)
        return Fetched(url, status, None, None, False, error)

    def _pace(self, url: str) -> None:
        host = urlsplit(url).netloc
        wait = self._last_hit.get(host, 0.0) + self.min_interval_s - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last_hit[host] = time.monotonic()

    def _check_robots(self, url: str) -> None:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._robots:
            self._robots[origin] = self._load_robots(origin)
        robots = self._robots[origin]
        if robots and not robots.can_fetch(url, self.user_agent):
            raise RobotsDisallowed(url)

    def _load_robots(self, origin: str) -> Protego | None:
        try:
            self._pace(origin)
            response = self.client.get(f"{origin}/robots.txt")
        except httpx.HTTPError:
            return DISALLOW_ALL
        if response.status_code >= 500:
            return DISALLOW_ALL
        if response.status_code >= 400 or "html" in response.headers.get("content-type", ""):
            return None
        return Protego.parse(response.text)


# RFC 9309 2.3.1.4: an unreachable robots.txt means assume complete disallow.
DISALLOW_ALL = Protego.parse("User-agent: *\nDisallow: /")
