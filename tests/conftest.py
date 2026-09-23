"""Shared behavioral doubles for the fixed local-model boundary."""

from collections.abc import AsyncGenerator, Generator, Mapping
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from typing import Any, Protocol, cast

from fastapi import FastAPI
from fastapi.testclient import TestClient

from decidealot.constants import LAYA_PROVIDER_NAME, VON_PROVIDER_NAME
from decidealot.errors import ProviderBusyError, ProviderUnavailableError
from decidealot.providers import ProviderResponse
from decidealot.supervisor import ProviderUnloadResult


class HTTPResponse(Protocol):
    """The public response shape asserted by the HTTP contract tests."""

    status_code: int
    headers: Mapping[str, str]

    def json(self) -> Any: ...


class HTTPClient(Protocol):
    """The small synchronous HTTP surface used by the public contract tests."""

    def get(self, path: str, **kwargs: object) -> HTTPResponse: ...

    def post(self, path: str, **kwargs: object) -> HTTPResponse: ...


@contextmanager
def app_client(application: FastAPI) -> Generator[HTTPClient, None, None]:
    """Expose the typed public HTTP surface around Starlette's runtime client."""

    with TestClient(application) as raw_client:
        yield cast(HTTPClient, raw_client)


class LifecycleSupervisor:
    """A lifecycle double that proves router behavior without starting models."""

    def __init__(self, ready_when_started: bool = True) -> None:
        self.acquired_providers: list[str] = []
        self.loaded_providers: set[str] = set()
        self.busy = False
        self.started = False
        self.stopped = False
        self.ready_when_started = ready_when_started

    @property
    def ready(self) -> bool:
        return self.started and self.ready_when_started

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True

    @asynccontextmanager
    async def acquire(self, provider_name: str) -> AsyncGenerator[None, None]:
        self.acquired_providers.append(provider_name)
        self.loaded_providers = {provider_name}
        yield

    async def unload_provider(self, provider_name: str) -> ProviderUnloadResult:
        if self.busy:
            raise ProviderBusyError("provider is processing a request")
        was_loaded = provider_name in self.loaded_providers
        self.loaded_providers.discard(provider_name)
        return ProviderUnloadResult(provider_name=provider_name, was_loaded=was_loaded)

    async def unload_all(self) -> tuple[ProviderUnloadResult, ...]:
        if self.busy:
            raise ProviderBusyError("provider is processing a request")
        results = tuple(
            ProviderUnloadResult(
                provider_name=provider_name,
                was_loaded=provider_name in self.loaded_providers,
            )
            for provider_name in (LAYA_PROVIDER_NAME, VON_PROVIDER_NAME)
        )
        self.loaded_providers.clear()
        return results


def _empty_provider_calls() -> list[tuple[dict[str, Any], str]]:
    return []


@dataclass
class FakeProvider:
    """A narrow local-provider double that records observable forwarded requests."""

    response: ProviderResponse
    unavailable: bool = False
    calls: list[tuple[dict[str, Any], str]] = field(default_factory=_empty_provider_calls)

    async def forward(self, payload: Mapping[str, Any], request_id: str) -> ProviderResponse:
        self.calls.append((dict(payload), request_id))
        if self.unavailable:
            raise ProviderUnavailableError("fake provider unavailable")
        return self.response


def system_one_request(model: str = "laya") -> dict[str, Any]:
    """Return the smallest request the official System One schema accepts."""

    return {
        "model": model,
        "state": "Review this action.",
        "questions": {
            "route": {
                "type": "choice",
                "instructions": "Which handling is required?",
                "criteria": {"allow": "Reversible.", "deny": "Irreversible."},
            }
        },
    }


def native_system_one_response(model: str) -> ProviderResponse:
    """Return a native provider result that also carries provider-only fields."""

    return ProviderResponse(
        status_code=200,
        body={
            "model": model,
            "answers": {
                "route": {
                    "type": "choice",
                    "choice": "allow",
                    "probabilities": {"allow": 0.98, "deny": 0.02},
                    "confidence": 0.96,
                    "action": {"act_probability": 0.41},
                }
            },
            "usage": {"input_tokens": 4, "output_tokens": 0},
            "routing": {"model": "english", "reason": "English Latin text"},
        },
    )


def projected_system_one_response(model: str) -> dict[str, Any]:
    """Return the official projection of the matching native provider result."""

    return {
        "model": model,
        "answers": {
            "route": {
                "type": "choice",
                "choice": "allow",
                "confidence": 0.96,
                "probabilities": {"allow": 0.98, "deny": 0.02},
            }
        },
        "usage": {"input_tokens": 4, "output_tokens": 0},
    }
