"""Headless rendering for JavaScript-only pages (ADR 0002 D11, ADR 0003 D3).

Playwright is the optional `browser` extra. The renderer follows the same politeness as
the plain fetcher and records each rendered DOM as a snapshot like any fetch.
"""

import importlib.util
from collections.abc import Callable
from urllib.parse import urlsplit

from .fetch import Fetched, Politeness, RobotsDisallowed

TIMEOUT_MS = 30000


def available() -> bool:
    return importlib.util.find_spec("playwright") is not None


class Renderer:
    def __init__(
        self,
        politeness: Politeness,
        guard: Callable[[str | None], None] | None = None,
    ):
        self.politeness = politeness
        self.guard = guard
        self._safe: dict[str, bool] = {}
        self._playwright = None
        self._browser = None

    def __enter__(self) -> "Renderer":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _page(self):
        if self._browser is None:
            from playwright.sync_api import sync_playwright

            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch()
        page = self._browser.new_page(user_agent=self.politeness.user_agent)
        if self.guard:
            page.route("**/*", self._route)
        return page

    def _route(self, route) -> None:
        host = urlsplit(route.request.url).hostname
        if host not in self._safe:
            try:
                self.guard(host)
                self._safe[host] = True
            except Exception:
                self._safe[host] = False
        if self._safe[host]:
            route.continue_()
        else:
            route.abort()

    def fetch(self, url: str) -> Fetched:
        try:
            self.politeness.admit(url)
            page = self._page()
            try:
                response = page.goto(url, wait_until="networkidle", timeout=TIMEOUT_MS)
                status = response.status if response else None
                body = page.content().encode("utf-8")
                final = page.url
            finally:
                page.close()
        except RobotsDisallowed:
            return self.politeness.fail(url, None, "robots_disallowed")
        except Exception as exc:  # Playwright raises its own error types
            return self.politeness.fail(url, None, f"render: {type(exc).__name__}: {exc}"[:300])
        if status and status >= 400:
            return self.politeness.fail(url, status, f"http_{status}")
        digest = self.politeness.store.pages.record(
            url, body, http_status=status, content_type="text/html"
        )
        return Fetched(url, status, digest, "text/html", True, final_url=final)

    def close(self) -> None:
        if self._browser is not None:
            self._browser.close()
            self._playwright.stop()
            self._browser = self._playwright = None
