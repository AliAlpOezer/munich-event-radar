import json
import re
from dataclasses import dataclass
from datetime import date

import httpx

from ..store import Store

API = "https://openrouter.ai/api/v1"


@dataclass(frozen=True)
class Model:
    id: str
    mode: str
    until: date | None = None


CHAIN = (
    Model("nvidia/nemotron-3-super-120b-a12b:free", "schema"),
    Model("dots-studio/dots-3-note-preview:free", "schema", until=date(2026, 12, 31)),
    Model("nvidia/nemotron-3-ultra-550b-a55b:free", "tool"),
    Model("openrouter/free", "json_object"),
)


class BudgetExhausted(Exception):
    pass


class DailyCapReached(Exception):
    pass


class AllModelsFailed(Exception):
    pass


def _body(model: Model, messages: list[dict], schema: dict, name: str) -> dict:
    body = {"model": model.id, "messages": messages, "temperature": 0, "max_tokens": 6000}
    if model.mode == "schema":
        body["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": name, "strict": True, "schema": schema},
        }
    elif model.mode == "tool":
        body["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": f"record_{name}",
                    "description": f"Record the {name}",
                    "parameters": schema,
                },
            }
        ]
        body["tool_choice"] = {"type": "function", "function": {"name": f"record_{name}"}}
    else:
        body["response_format"] = {"type": "json_object"}
    return body


def _content(response: dict, mode: str) -> dict:
    message = response["choices"][0]["message"]
    calls = message.get("tool_calls") or []
    raw = calls[0]["function"]["arguments"] if mode == "tool" and calls else message.get("content")
    raw = re.sub(r"<think>.*?</think>", "", raw or "", flags=re.S).strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw)
    return json.loads(raw)


class Router:
    def __init__(self, key: str, store: Store, daily_cap: int, client: httpx.Client | None = None):
        self.store = store
        self.daily_cap = daily_cap
        self.client = client or httpx.Client(
            headers={"Authorization": f"Bearer {key}", "X-Title": "munich-event-radar"},
            timeout=httpx.Timeout(180.0, connect=15.0),
        )
        self._live: set[str] | None = None

    def _chain(self) -> list[Model]:
        if self._live is None:
            response = self.client.get(f"{API}/models")
            response.raise_for_status()
            self._live = {m["id"] for m in response.json()["data"]}
        today = date.today()
        return [
            m
            for m in CHAIN
            if (m.id in self._live or m.id == "openrouter/free")
            and (not m.until or today <= m.until)
        ]

    def complete(
        self, messages: list[dict], schema: dict, name: str = "events"
    ) -> tuple[str, dict]:
        failures = []
        for model in self._chain():
            if not self.store.extraction.spend("openrouter", self.daily_cap):
                raise BudgetExhausted
            response = self.client.post(
                f"{API}/chat/completions", json=_body(model, messages, schema, name)
            )
            detail = response.text[:400]
            if response.status_code == 429 and "upstream" not in detail.lower():
                raise DailyCapReached(detail)
            if response.status_code != 200 or '"error"' in detail[:200]:
                failures.append(f"{model.id}: {response.status_code} {detail[:120]}")
                continue
            try:
                return model.id, _content(response.json(), model.mode)
            except (ValueError, KeyError, IndexError, TypeError) as exc:
                failures.append(f"{model.id}: unparseable {exc}")
        raise AllModelsFailed("; ".join(failures))
