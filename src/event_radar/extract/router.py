"""The LLM providers in order (ADR 0010): OpenRouter's free models, then Groq's. Each has its
own key, models and daily budget; one that is spent, down or slow hands over to the next."""

import json
import re
from dataclasses import dataclass
from datetime import date

import httpx

from ..config import Settings
from ..store import Store

OPENROUTER = "https://openrouter.ai/api/v1"
GROQ = "https://api.groq.com/openai/v1"


@dataclass(frozen=True)
class Model:
    id: str
    mode: str
    until: date | None = None


OPENROUTER_MODELS = (
    Model("nvidia/nemotron-3-super-120b-a12b:free", "schema"),
    Model("dots-studio/dots-3-note-preview:free", "schema", until=date(2026, 12, 31)),
    Model("nvidia/nemotron-3-ultra-550b-a55b:free", "tool"),
    Model("openrouter/free", "json_object"),
)
GROQ_MODELS = (
    Model("openai/gpt-oss-120b", "schema"),
    Model("openai/gpt-oss-20b", "schema"),
)


@dataclass(frozen=True)
class Provider:
    name: str  # its row in the daily budget
    api: str
    key: str
    daily_cap: int
    models: tuple[Model, ...]


def providers(settings: Settings) -> list[Provider]:
    """The providers whose key is set, in the order they are asked."""
    found = []
    if settings.openrouter_key:
        found.append(
            Provider(
                "openrouter",
                OPENROUTER,
                settings.openrouter_key,
                settings.llm_daily_cap,
                OPENROUTER_MODELS,
            )
        )
    if settings.groq_key:
        found.append(
            Provider("groq", GROQ, settings.groq_key, settings.groq_daily_cap, GROQ_MODELS)
        )
    return found


class BudgetExhausted(Exception):
    pass


class DailyCapReached(Exception):
    pass


class AllModelsFailed(Exception):
    pass


class _Exhausted(Exception):
    """Every model of one provider failed."""


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


class _Spent(Exception):
    def __init__(self, by_provider: bool):
        self.by_provider = by_provider


class Router:
    def __init__(
        self,
        providers: list[Provider],
        store: Store,
        transport: httpx.BaseTransport | None = None,
    ):
        self.providers = providers
        self.store = store
        self.clients = {
            p.name: httpx.Client(
                headers={"Authorization": f"Bearer {p.key}", "X-Title": "munich-event-radar"},
                timeout=httpx.Timeout(180.0, connect=15.0),
                transport=transport,
            )
            for p in providers
        }
        self._live: dict[str, set[str]] = {}
        self._spent: dict[str, bool] = {}  # provider: refused by its own cap, for this run
        self._down: set[str] = set()  # providers that timed out or could not be reached

    @classmethod
    def from_settings(cls, settings: Settings, store: Store) -> "Router | None":
        found = providers(settings)
        return cls(found, store) if found else None

    def _chain(self, provider: Provider) -> list[Model]:
        if provider.name not in self._live:
            response = self.clients[provider.name].get(f"{provider.api}/models")
            response.raise_for_status()
            self._live[provider.name] = {m["id"] for m in response.json()["data"]}
        today = date.today()
        return [
            m
            for m in provider.models
            if (m.id in self._live[provider.name] or m.id == "openrouter/free")
            and (not m.until or today <= m.until)
        ]

    def complete(
        self, messages: list[dict], schema: dict, name: str = "events"
    ) -> tuple[str, dict]:
        """The first answer any provider's models give. Raises DailyCapReached or
        BudgetExhausted when every provider is spent, AllModelsFailed otherwise."""
        failures = []
        for provider in self.providers:
            if provider.name in self._down or provider.name in self._spent:
                continue
            try:
                return self._ask(provider, messages, schema, name, failures)
            except _Exhausted:
                continue
            except _Spent as spent:
                self._spent[provider.name] = spent.by_provider
            except httpx.HTTPError as exc:
                self._down.add(provider.name)
                failures.append(f"{provider.name}: {type(exc).__name__}")
        if self.providers and all(p.name in self._spent for p in self.providers):
            if any(self._spent.values()):
                raise DailyCapReached("every provider's daily limit is reached")
            raise BudgetExhausted
        raise AllModelsFailed("; ".join(failures) or "no provider left")

    def _ask(
        self, provider: Provider, messages: list[dict], schema: dict, name: str, failures: list
    ) -> tuple[str, dict]:
        for model in self._chain(provider):
            if not self.store.extraction.spend(provider.name, provider.daily_cap):
                raise _Spent(by_provider=False)
            response = self.clients[provider.name].post(
                f"{provider.api}/chat/completions", json=_body(model, messages, schema, name)
            )
            detail = response.text[:400]
            if response.status_code == 429 and "upstream" not in detail.lower():
                raise _Spent(by_provider=True)
            if response.status_code != 200 or '"error"' in detail[:200]:
                failures.append(f"{model.id}: {response.status_code} {detail[:120]}")
                continue
            try:
                return model.id, _content(response.json(), model.mode)
            except (ValueError, KeyError, IndexError, TypeError) as exc:
                failures.append(f"{model.id}: unparseable {exc}")
        raise _Exhausted
