"""The System One protocol, `POST /v1/systemone` (https://docs.typesafe.ai/api, checked
2026-10-19): one state, many typed questions, and an answer with probabilities to each.
Jev serves it, and so do `laya.serve` and `kev.serve`, so one client reaches all three
(ADR 0017 D3)."""

import time
from dataclasses import dataclass

import httpx


@dataclass(frozen=True)
class Answer:
    answers: dict
    input_tokens: int | None
    seconds: float


class SystemOne:
    def __init__(
        self,
        base_url: str,
        model: str,
        key: str | None = None,
        timeout: float = 5.0,
        options: dict | None = None,
        transport: httpx.BaseTransport | None = None,
    ):
        """`options` go into every request beside the state, such as Laya's `max_len`."""
        self.model = model
        self.options = options or {}
        self.client = httpx.Client(
            base_url=base_url,
            headers={"Authorization": f"Bearer {key}"} if key else {},
            timeout=httpx.Timeout(timeout, connect=10.0),
            transport=transport,
        )

    def ask(self, state: dict | str, questions: dict) -> Answer:
        """Raises httpx.HTTPError when the request fails or is refused."""
        started = time.monotonic()
        response = self.client.post(
            "/v1/systemone",
            json={"model": self.model, "state": state, "questions": questions, **self.options},
        )
        response.raise_for_status()
        body = response.json()
        return Answer(
            body["answers"],
            (body.get("usage") or {}).get("input_tokens"),
            time.monotonic() - started,
        )
