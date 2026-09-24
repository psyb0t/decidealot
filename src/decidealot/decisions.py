"""Shared decision operations for REST and MCP callers."""

import logging
from collections.abc import Mapping
from typing import Any, Protocol, cast

from fastapi import status

from decidealot.errors import (
    ProviderUnavailableError,
    TypeSafeValidationError,
    UnknownModelError,
)
from decidealot.providers import (
    ModelRoute,
    ModelRouter,
    ProviderClient,
    native_provider_payload,
)
from decidealot.supervisor import ProviderUnloadResult
from decidealot.typesafe import (
    ModelMetadataList,
    parse_system_one_request,
    project_system_one_response,
    provider_validation_content,
    unknown_model_detail,
)

logger = logging.getLogger(__name__)


class Supervisor(Protocol):
    """The provider lifecycle surface needed by the decision service."""

    @property
    def ready(self) -> bool: ...

    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    def acquire(self, provider_name: str) -> Any: ...

    async def unload_all(self) -> tuple[ProviderUnloadResult, ...]: ...


class DecisionService:
    """Select a local model, forward one decision, and project its public result."""

    def __init__(
        self,
        providers: Mapping[str, ProviderClient],
        supervisor: Supervisor,
        model_router: ModelRouter,
    ) -> None:
        self._providers = dict(providers)
        self._supervisor = supervisor
        self._model_router = model_router

    def model_catalog(self) -> dict[str, Any]:
        """Return every supported public model selector."""

        catalog = ModelMetadataList(models=list(self._model_router.catalog()))
        return catalog.model_dump(mode="json")

    async def unload_all(self) -> dict[str, Any]:
        """Unload every idle local model and report each outcome."""

        results = await self._supervisor.unload_all()
        logger.info("all local providers unloaded")
        return {
            "status": "unloaded",
            "providers": [_unload_result_to_json(result) for result in results],
        }

    async def system_one(self, body: object, request_id: str) -> dict[str, Any]:
        """Run one TypeSafe-compatible decision through its selected local provider."""

        request = parse_system_one_request(body)
        route = _resolve_route(self._model_router, request.model)
        if not self._supervisor.ready:
            raise ProviderUnavailableError("local providers are not ready")

        provider = self._providers.get(route.provider_name)
        if provider is None:
            logger.error(
                "configured provider client is missing", extra={"provider": route.provider_name}
            )
            raise ProviderUnavailableError("the selected local provider is not configured")

        logger.info("system one request started", extra={"provider": route.provider_name})
        async with self._supervisor.acquire(route.provider_name):
            result = await provider.forward(native_provider_payload(route, request), request_id)
        logger.info(
            "system one request completed",
            extra={"provider": route.provider_name, "status_code": result.status_code},
        )
        if result.status_code == status.HTTP_200_OK:
            return project_system_one_response(result.body, route.public_model)
        if result.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT:
            content = provider_validation_content(result.body)
            raise TypeSafeValidationError(cast(list[dict[str, Any]], content["detail"]))
        logger.warning(
            "local provider returned an unexpected status",
            extra={"provider": route.provider_name, "status_code": result.status_code},
        )
        raise ProviderUnavailableError("the selected local provider returned an invalid response")


def _resolve_route(model_router: ModelRouter, requested_model: str) -> ModelRoute:
    try:
        return model_router.resolve(requested_model)
    except UnknownModelError as error:
        raise TypeSafeValidationError(unknown_model_detail(requested_model)) from error


def _unload_result_to_json(result: ProviderUnloadResult) -> dict[str, object]:
    return {"name": result.provider_name, "wasLoaded": result.was_loaded}
