"""Model selection, native request adaptation, and fixed-endpoint forwarding."""

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError

from decidealot.constants import (
    CLM_PROVIDER_NAME,
    CLM_SYSTEMONE_URL,
    JEV_MODELS_URL,
    JEV_PROVIDER_NAME,
    JEV_SYSTEMONE_URL,
    LAYA_PROVIDER_NAME,
    LAYA_SYSTEMONE_URL,
    REQUEST_ID_HEADER,
    VON_PROVIDER_NAME,
    VON_SYSTEMONE_URL,
)
from decidealot.errors import ProviderUnavailableError, TypeSafeValidationError, UnknownModelError
from decidealot.settings import Settings
from decidealot.typesafe import (
    ChoiceQuestion,
    JSONValue,
    ModelMetadata,
    NoulCriteria,
    NoulQuestion,
    OptionalJSONValue,
    Question,
    SystemOneRequest,
    validation_detail,
)

logger = logging.getLogger(__name__)

# Release dates of the pinned builds: the Laya v0.3.7 release commit and the von-sdk
# 1.1.1 PyPI upload.
_laya_release_date = "2026-09-23"
_von_release_date = "2026-09-22"
_clm_release_date = "2026-09-24"
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
_clm_catalog_entries: tuple[tuple[str, str], ...] = (
    ("clm", "Contrastive Language Model decisions backed by a configured embeddings endpoint."),
    ("clm-latest", "Alias for the current CLM checkpoint."),
    ("clm-0.1", "Alias for CLM v0.1."),
    ("clm-0.1-8b", "CLM v0.1 decision head paired with Qwen3-8B embeddings."),
)
_laya_public_model = "laya"
_von_public_model = "von-1.1"
_von_upstream_model = "von-1.1"
_clm_public_model = "clm-0.1-8b"
# Laya auto-routes by script and language whenever the request names no checkpoint.
_laya_auto_selector = ""
_laya_auto_aliases = frozenset({"laya", "laya-auto", "laya-latest"})
_laya_checkpoint_selectors: Mapping[str, str] = {
    "laya-english": "english",
    "laya-multilingual": "multilingual",
    "laya-typed-decisions": "typed-decisions",
}
_von_aliases = frozenset(name for name, _ in _von_catalog_entries)
_clm_aliases = frozenset(name for name, _ in _clm_catalog_entries)
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
        """Forward one native request to the provider's fixed endpoint."""

        ...


@runtime_checkable
class HostedCatalogClient(ProviderClient, Protocol):
    """The hosted provider's authenticated model discovery boundary."""

    async def list_models(self) -> ProviderResponse: ...


@dataclass(frozen=True)
class ModelRoute:
    """The provider, the public model name, and the native selector for one request."""

    provider_name: str
    public_model: str
    upstream_model: str
    is_local: bool = True


class HTTPProviderClient:
    """A non-redirecting client for one fixed model endpoint."""

    def __init__(
        self,
        provider_name: str,
        endpoint_url: str,
        client: httpx.AsyncClient,
        api_key: SecretStr | None = None,
    ) -> None:
        self._provider_name = provider_name
        self._endpoint_url = endpoint_url
        self._client = client
        self._api_key = api_key

    async def forward(self, payload: Mapping[str, Any], request_id: str) -> ProviderResponse:
        try:
            headers = {REQUEST_ID_HEADER: request_id}
            if self._api_key is not None:
                headers["Authorization"] = f"Bearer {self._api_key.get_secret_value()}"
            response = await self._client.post(
                self._endpoint_url,
                headers=headers,
                json=dict(payload),
            )
        except httpx.TimeoutException as error:
            logger.warning(
                "provider timed out",
                extra={"provider": self._provider_name, "error_type": type(error).__name__},
            )
            raise ProviderUnavailableError("the selected provider timed out") from error
        except httpx.HTTPError as error:
            logger.warning(
                "provider request failed",
                extra={"provider": self._provider_name, "error_type": type(error).__name__},
            )
            raise ProviderUnavailableError("the selected provider is unavailable") from error

        if response.status_code >= 500:
            logger.warning(
                "provider returned server error",
                extra={"provider": self._provider_name, "status_code": response.status_code},
            )
            raise ProviderUnavailableError("the selected provider is unavailable")

        try:
            body = response.json()
        except ValueError as error:
            logger.warning(
                "provider returned non-json response",
                extra={"provider": self._provider_name, "status_code": response.status_code},
            )
            raise ProviderUnavailableError(
                "the selected provider returned an invalid response"
            ) from error

        return ProviderResponse(status_code=response.status_code, body=body)

    async def list_models(self) -> ProviderResponse:
        """Read the authenticated TypeSafe catalog from its fixed endpoint."""

        if self._provider_name != JEV_PROVIDER_NAME:
            raise ProviderUnavailableError("model discovery is unavailable for this provider")
        if self._api_key is None:
            raise ProviderUnavailableError("the selected provider is not configured")
        try:
            response = await self._client.get(
                JEV_MODELS_URL,
                headers={"Authorization": f"Bearer {self._api_key.get_secret_value()}"},
            )
            if response.status_code != 200:
                raise ProviderUnavailableError("the hosted model catalog is unavailable")
            return ProviderResponse(status_code=200, body=response.json())
        except (httpx.HTTPError, ValueError) as error:
            logger.warning(
                "hosted model catalog request failed",
                extra={"provider": self._provider_name, "error_type": type(error).__name__},
            )
            raise ProviderUnavailableError("the hosted model catalog is unavailable") from error


class ModelRouter:
    """Resolve public aliases without allowing callers to choose a network target."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._enabled_provider_names = frozenset((settings or Settings()).enabled_provider_names)
        self._hosted_models: tuple[ModelMetadata, ...] = ()

    @property
    def hosted_enabled(self) -> bool:
        return JEV_PROVIDER_NAME in self._enabled_provider_names

    def set_hosted_models(self, models: tuple[ModelMetadata, ...]) -> None:
        """Replace the authenticated upstream catalog after complete validation."""

        self._hosted_models = models

    @property
    def supported_models(self) -> tuple[str, ...]:
        return tuple(entry.name for entry in self.catalog())

    def catalog(self) -> tuple[ModelMetadata, ...]:
        """Describe every accepted selector and its advertised release date."""

        return (*self.local_catalog(), *self._hosted_models)

    def local_catalog(self) -> tuple[ModelMetadata, ...]:
        """Describe selectors served by this deployment's local providers."""

        return (
            *self._enabled_metadata(
                LAYA_PROVIDER_NAME,
                _laya_catalog_entries,
                _laya_release_date,
            ),
            *self._enabled_metadata(
                VON_PROVIDER_NAME,
                _von_catalog_entries,
                _von_release_date,
            ),
            *self._enabled_metadata(
                CLM_PROVIDER_NAME,
                _clm_catalog_entries,
                _clm_release_date,
            ),
        )

    def resolve(self, requested_model: str) -> ModelRoute:
        """Resolve a public model selector to a fixed backend."""

        is_laya_enabled = LAYA_PROVIDER_NAME in self._enabled_provider_names
        if is_laya_enabled and requested_model in _laya_auto_aliases:
            return self._laya_auto_route()
        checkpoint = _laya_checkpoint_selectors.get(requested_model)
        if is_laya_enabled and checkpoint is not None:
            return ModelRoute(
                provider_name=LAYA_PROVIDER_NAME,
                public_model=requested_model,
                upstream_model=checkpoint,
            )
        if VON_PROVIDER_NAME in self._enabled_provider_names and requested_model in _von_aliases:
            return self._von_route()
        if CLM_PROVIDER_NAME in self._enabled_provider_names and requested_model in _clm_aliases:
            return self._clm_route()
        if self.hosted_enabled and requested_model in {model.name for model in self._hosted_models}:
            return ModelRoute(
                provider_name=JEV_PROVIDER_NAME,
                public_model=requested_model,
                upstream_model=requested_model,
                is_local=False,
            )
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

    def _enabled_metadata(
        self,
        provider_name: str,
        entries: tuple[tuple[str, str], ...],
        release_date: str,
    ) -> tuple[ModelMetadata, ...]:
        if provider_name not in self._enabled_provider_names:
            return ()
        return self._metadata(entries, release_date)

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

    @staticmethod
    def _clm_route() -> ModelRoute:
        return ModelRoute(
            provider_name=CLM_PROVIDER_NAME,
            public_model=_clm_public_model,
            upstream_model=_clm_public_model,
        )


class CLMRequestConfig(BaseModel):
    """Optional probability scaling without changing the highest-scoring choice."""

    model_config = ConfigDict(extra="forbid")
    temperature: float = Field(default=1.0, strict=True, gt=0, le=100, allow_inf_nan=False)


def validate_provider_config(route: ModelRoute, request: SystemOneRequest) -> None:
    """Reject unsupported options before acquiring a provider or executing a batch."""

    if route.provider_name == CLM_PROVIDER_NAME:
        try:
            CLMRequestConfig.model_validate(request.config)
        except ValidationError as error:
            raise TypeSafeValidationError(
                validation_detail(error.errors(), ("body", "config"))
            ) from error
        return
    if request.config:
        raise TypeSafeValidationError(
            [
                {
                    "loc": ["body", "config", name],
                    "type": "extra_forbidden",
                    "msg": "The selected provider does not support this setting",
                }
                for name in request.config
            ]
        )


def native_provider_payload(route: ModelRoute, request: SystemOneRequest) -> dict[str, Any]:
    """Build the native body for local models or preserve the official Jev request.

    Laya requires an instructions field on every question and Von requires it to be a
    string, while the official schema makes it optional and allows nested JSON. Nested
    option values are rendered as compact JSON text for the same reason, so a request
    the official API accepts is never rejected by a local model over its own shape.
    """

    logger.debug(
        "model request routed",
        extra={"provider": route.provider_name, "model": route.upstream_model},
    )
    if not route.is_local:
        return {
            **request.model_dump(mode="json", exclude={"config"}),
            "model": route.upstream_model,
        }
    payload: dict[str, Any] = {
        "model": route.upstream_model,
        "state": request.state,
        "questions": {
            name: (
                question.model_dump(mode="json")
                if route.provider_name == CLM_PROVIDER_NAME
                else _native_question(question)
            )
            for name, question in request.questions.items()
        },
    }
    if route.provider_name == CLM_PROVIDER_NAME and request.config:
        payload["temperature"] = CLMRequestConfig.model_validate(request.config).temperature
    return payload


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
    settings: Settings,
) -> tuple[dict[str, ProviderClient], httpx.AsyncClient]:
    """Create the app-owned HTTP client and fixed provider clients."""

    client = httpx.AsyncClient(
        timeout=httpx.Timeout(settings.request_timeout_seconds),
        follow_redirects=False,
        trust_env=False,
    )
    provider_urls = {
        LAYA_PROVIDER_NAME: LAYA_SYSTEMONE_URL,
        VON_PROVIDER_NAME: VON_SYSTEMONE_URL,
        CLM_PROVIDER_NAME: CLM_SYSTEMONE_URL,
        JEV_PROVIDER_NAME: JEV_SYSTEMONE_URL,
    }
    providers: dict[str, ProviderClient] = {
        provider_name: HTTPProviderClient(
            provider_name,
            provider_urls[provider_name],
            client,
            settings.typesafe_api_key if provider_name == JEV_PROVIDER_NAME else None,
        )
        for provider_name in settings.enabled_provider_names
    }
    return providers, client
