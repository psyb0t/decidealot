"""Model selection, native request adaptation, and safe loopback forwarding."""

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from decidealot.constants import (
    LAYA_PROVIDER_NAME,
    LAYA_SYSTEMONE_URL,
    REQUEST_ID_HEADER,
    VON_PROVIDER_NAME,
    VON_SYSTEMONE_URL,
)
from decidealot.errors import ProviderUnavailableError, UnknownModelError
from decidealot.settings import ModelName
from decidealot.typesafe import (
    ChoiceQuestion,
    JSONValue,
    ModelMetadata,
    NoulCriteria,
    NoulQuestion,
    OptionalJSONValue,
    Question,
    SystemOneRequest,
)

logger = logging.getLogger(__name__)

# Release dates of the pinned builds: the Laya v0.3.7 release commit and the von-sdk
# 1.1.1 PyPI upload.
_laya_release_date = "2026-09-23"
_von_release_date = "2026-09-22"
_laya_catalog_entries: tuple[tuple[str, str], ...] = (
    ("laya", "Laya System One decisions with automatic checkpoint routing."),
    ("laya-auto", "Alias for laya with automatic checkpoint routing."),
    ("laya-latest", "Alias for the current Laya release."),
    ("laya-english", "Laya English checkpoint for English Latin-script content."),
    ("laya-multilingual", "Laya multilingual checkpoint for other languages and scripts."),
    ("laya-typed-decisions", "Laya checkpoint tuned for structured workflow decisions."),
)
_von_catalog_entries: tuple[tuple[str, str], ...] = (
    ("von", "Von System One decisions served by the local Von 1.1 model."),
    ("von-latest", "Alias for the current Von release."),
    ("von-1.1", "Von 1.1 System One decision model."),
    ("von-1.1.0", "Alias for the Von 1.1 point release."),
)
_jev_alias_names: tuple[str, ...] = ("jev", "jev-1", "jev-1.0", "jev-latest")
_jev_description_template = "TypeSafe compatibility alias for the configured default model {model}."
_laya_public_model = "laya"
_von_public_model = "von-1.1"
_von_upstream_model = "von-1.1"
# Laya auto-routes by script and language whenever the request names no checkpoint.
_laya_auto_selector = ""
_laya_auto_aliases = frozenset({"laya", "laya-auto", "laya-latest"})
_laya_checkpoint_selectors: Mapping[str, str] = {
    "laya-english": "english",
    "laya-multilingual": "multilingual",
    "laya-typed-decisions": "typed-decisions",
}
_von_aliases = frozenset(name for name, _ in _von_catalog_entries)
_jev_aliases = frozenset(_jev_alias_names)
_absent_instructions = ""
_noul_outcomes = ("true", "false")


@dataclass(frozen=True)
class ProviderResponse:
    """An upstream model's HTTP status and JSON payload."""

    status_code: int
    body: Any


class ProviderClient(Protocol):
    """The narrow external-model boundary used by the application service."""

    async def forward(self, payload: Mapping[str, Any], request_id: str) -> ProviderResponse:
        """Forward one native request to the provider's fixed loopback URL."""

        ...


@dataclass(frozen=True)
class ModelRoute:
    """The provider, the public model name, and the native selector for one request."""

    provider_name: str
    public_model: str
    upstream_model: str


class HTTPProviderClient:
    """A non-redirecting client for one fixed local model endpoint."""

    def __init__(self, provider_name: str, endpoint_url: str, client: httpx.AsyncClient) -> None:
        self._provider_name = provider_name
        self._endpoint_url = endpoint_url
        self._client = client

    async def forward(self, payload: Mapping[str, Any], request_id: str) -> ProviderResponse:
        try:
            response = await self._client.post(
                self._endpoint_url,
                headers={REQUEST_ID_HEADER: request_id},
                json=dict(payload),
            )
        except httpx.TimeoutException as error:
            logger.warning(
                "local provider timed out",
                extra={"provider": self._provider_name, "error": str(error)},
            )
            raise ProviderUnavailableError("the selected local provider timed out") from error
        except httpx.HTTPError as error:
            logger.warning(
                "local provider request failed",
                extra={"provider": self._provider_name, "error": str(error)},
            )
            raise ProviderUnavailableError("the selected local provider is unavailable") from error

        if response.status_code >= 500:
            logger.warning(
                "local provider returned server error",
                extra={"provider": self._provider_name, "status_code": response.status_code},
            )
            raise ProviderUnavailableError("the selected local provider is unavailable")

        try:
            body = response.json()
        except ValueError as error:
            logger.warning(
                "local provider returned non-json response",
                extra={"provider": self._provider_name, "status_code": response.status_code},
            )
            raise ProviderUnavailableError(
                "the selected local provider returned an invalid response"
            ) from error

        return ProviderResponse(status_code=response.status_code, body=body)


class ModelRouter:
    """Resolve public aliases without allowing callers to choose a network target."""

    def __init__(self, default_model: ModelName) -> None:
        self._default_model = default_model

    @property
    def supported_models(self) -> tuple[str, ...]:
        return tuple(entry.name for entry in self.catalog())

    def catalog(self) -> tuple[ModelMetadata, ...]:
        """Describe every accepted selector with the release date of its local model."""

        default_route = self._default_route()
        jev_description = _jev_description_template.format(model=default_route.public_model)
        return (
            *self._metadata(_laya_catalog_entries, _laya_release_date),
            *self._metadata(_von_catalog_entries, _von_release_date),
            *self._metadata(
                tuple((name, jev_description) for name in _jev_alias_names),
                self._default_release_date(),
            ),
        )

    def resolve(self, requested_model: str) -> ModelRoute:
        """Resolve a public model selector to a fixed local backend."""

        if requested_model in _jev_aliases:
            return self._default_route()
        if requested_model in _laya_auto_aliases:
            return self._laya_auto_route()
        checkpoint = _laya_checkpoint_selectors.get(requested_model)
        if checkpoint is not None:
            return ModelRoute(
                provider_name=LAYA_PROVIDER_NAME,
                public_model=requested_model,
                upstream_model=checkpoint,
            )
        if requested_model in _von_aliases:
            return self._von_route()
        raise UnknownModelError(f"unsupported model {requested_model!r}")

    @staticmethod
    def _metadata(
        entries: tuple[tuple[str, str], ...],
        release_date: str,
    ) -> tuple[ModelMetadata, ...]:
        return tuple(
            ModelMetadata(name=name, description=description, release_date=release_date)
            for name, description in entries
        )

    def _default_route(self) -> ModelRoute:
        if self._default_model == LAYA_PROVIDER_NAME:
            return self._laya_auto_route()
        return self._von_route()

    def _default_release_date(self) -> str:
        if self._default_model == LAYA_PROVIDER_NAME:
            return _laya_release_date
        return _von_release_date

    @staticmethod
    def _laya_auto_route() -> ModelRoute:
        return ModelRoute(
            provider_name=LAYA_PROVIDER_NAME,
            public_model=_laya_public_model,
            upstream_model=_laya_auto_selector,
        )

    @staticmethod
    def _von_route() -> ModelRoute:
        return ModelRoute(
            provider_name=VON_PROVIDER_NAME,
            public_model=_von_public_model,
            upstream_model=_von_upstream_model,
        )


def native_provider_payload(route: ModelRoute, request: SystemOneRequest) -> dict[str, Any]:
    """Build the native body that both local providers accept for a valid request.

    Laya requires an instructions field on every question and Von requires it to be a
    string, while the official schema makes it optional and allows nested JSON. Nested
    option values are rendered as compact JSON text for the same reason, so a request
    the official API accepts is never rejected by a local model over its own shape.
    """

    logger.debug(
        "model request routed",
        extra={"provider": route.provider_name, "model": route.upstream_model},
    )
    return {
        "model": route.upstream_model,
        "state": request.state,
        "questions": {
            name: _native_question(question) for name, question in request.questions.items()
        },
    }


def _native_question(question: Question) -> dict[str, Any]:
    instructions = _native_instructions(question.instructions)
    if isinstance(question, NoulQuestion):
        return {
            "type": question.type,
            "instructions": instructions,
            "criteria": _native_noul_criteria(question.criteria),
        }
    if isinstance(question, ChoiceQuestion):
        return {
            "type": question.type,
            "instructions": instructions,
            "criteria": {
                choice: None if value is None else _native_text(value)
                for choice, value in question.criteria.items()
            },
        }
    return {
        "type": question.type,
        "instructions": instructions,
        "criteria": [_native_text(level) for level in question.criteria],
    }


def _native_noul_criteria(criteria: NoulCriteria | None) -> dict[str, str] | None:
    if criteria is None:
        return None
    described = {
        outcome: _native_text(value)
        for outcome, value in zip(_noul_outcomes, (criteria.true, criteria.false), strict=True)
        if value is not None
    }
    return described or None


def _native_instructions(instructions: OptionalJSONValue) -> str:
    if instructions is None:
        return _absent_instructions
    return _native_text(instructions)


def _native_text(value: JSONValue) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def default_provider_clients(
    timeout_seconds: float,
) -> tuple[dict[str, ProviderClient], httpx.AsyncClient]:
    """Create the app-owned HTTP client and fixed local provider clients."""

    client = httpx.AsyncClient(
        timeout=httpx.Timeout(timeout_seconds),
        follow_redirects=False,
        trust_env=False,
    )
    providers: dict[str, ProviderClient] = {
        LAYA_PROVIDER_NAME: HTTPProviderClient(LAYA_PROVIDER_NAME, LAYA_SYSTEMONE_URL, client),
        VON_PROVIDER_NAME: HTTPProviderClient(VON_PROVIDER_NAME, VON_SYSTEMONE_URL, client),
    }
    return providers, client
