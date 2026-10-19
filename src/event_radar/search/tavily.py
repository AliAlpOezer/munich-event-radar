"""Tavily's search API (ADR 0016), as documented on 2026-10-19:
https://docs.tavily.com/documentation/api-reference/endpoint/search. A basic search costs
one credit."""

import httpx

from .gate import Hit

API = "https://api.tavily.com/search"
# A wrong key, or the plan's or the pay-as-you-go limit reached.
FATAL = {401, 432, 433}


class SearchStopped(Exception):
    """No more searches this run. `fatal`: it will not pass by itself, so the run fails."""

    def __init__(self, detail: str, fatal: bool):
        super().__init__(detail)
        self.fatal = fatal


class Tavily:
    def __init__(
        self,
        key: str,
        exclude_domains: tuple[str, ...],
        transport: httpx.BaseTransport | None = None,
    ):
        self.client = httpx.Client(
            headers={"Authorization": f"Bearer {key}"},
            timeout=httpx.Timeout(30.0, connect=10.0),
            transport=transport,
        )
        self.exclude_domains = list(exclude_domains)

    def search(self, query: str) -> list[Hit]:
        """The results of one basic search. Raises SearchStopped when Tavily refuses more,
        httpx.HTTPError when this one failed."""
        response = self.client.post(
            API,
            json={
                "query": query,
                "search_depth": "basic",
                "max_results": 10,
                "exclude_domains": self.exclude_domains,
            },
        )
        if response.status_code in FATAL or response.status_code == 429:
            detail = f"{response.status_code} {response.text[:200]}"
            raise SearchStopped(detail, fatal=response.status_code in FATAL)
        response.raise_for_status()
        return [
            Hit(r["url"], str(r.get("title") or ""), str(r.get("content") or ""), _score(r))
            for r in response.json().get("results") or []
            if isinstance(r, dict) and isinstance(r.get("url"), str)
        ]


def _score(result: dict) -> float:
    score = result.get("score")
    return float(score) if isinstance(score, int | float) else 0.0
