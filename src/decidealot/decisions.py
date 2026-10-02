"""Shared decision operations for REST and MCP callers."""

import asyncio
import logging
from collections.abc import AsyncGenerator, Mapping
from contextlib import asynccontextmanager
from typing import Any, Protocol, cast

from fastapi import status

from decidealot.constants import (
    CLM_PROVIDER_NAME,
    HOSTED_MODEL_CATALOG_TTL_SECONDS,
    JEV_PROVIDER_NAME,
)
from decidealot.errors import (
    ProviderUnavailableError,
    TypeSafeValidationError,
    UnknownModelError,
)
from decidealot.providers import (
    HostedCatalogClient,
    ModelRoute,
    ModelRouter,
    ProviderClient,
    native_provider_payload,
)
from decidealot.supervisor import ProviderUnloadResult
from decidealot.typesafe import (
    ModelMetadata,
    ModelMetadataList,
    SystemOneBatchResponse,
    SystemOneRequest,
    parse_system_one_batch_request,
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
    """Select a model, forward one decision, and project its public result."""

    def __init__(
        self,
        providers: Mapping[str, ProviderClient],
        supervisor: Supervisor,
        model_router: ModelRouter,
        max_batch_requests: int,
        max_batch_concurrency: int,
        device: str,
        clm_parallel_with_local_models: bool,
    ) -> None:
        self._providers = dict(providers)
        self._supervisor = supervisor
        self._model_router = model_router
        self._max_batch_requests = max_batch_requests
        self._batch_semaphore = (
            asyncio.Semaphore(max_batch_concurrency) if max_batch_concurrency else None
        )
        self._device = device
        self._clm_parallel_with_local_models = clm_parallel_with_local_models
        self._model_locks: dict[str, asyncio.Lock] = {}
        self._local_condition = asyncio.Condition()
        self._active_local_models = 0
        self._clm_active = False
        self._hosted_catalog_lock = asyncio.Lock()
        self._hosted_catalog_expires_at = 0.0

    async def model_catalog(self) -> dict[str, Any]:
        """Return every supported public model selector."""

        await self._refresh_hosted_catalog()
        catalog = ModelMetadataList(models=list(self._model_router.catalog()))
        return catalog.model_dump(mode="json")

    async def _refresh_hosted_catalog(self) -> None:
        if not self._model_router.hosted_enabled:
            return
        now = asyncio.get_running_loop().time()
        if now < self._hosted_catalog_expires_at:
            return
        async with self._hosted_catalog_lock:
            now = asyncio.get_running_loop().time()
            if now < self._hosted_catalog_expires_at:
                return
            provider = self._providers.get(JEV_PROVIDER_NAME)
            if not isinstance(provider, HostedCatalogClient):
                raise ProviderUnavailableError("the hosted model catalog is unavailable")
            response = await provider.list_models()
            if response.status_code != status.HTTP_200_OK:
                raise ProviderUnavailableError("the hosted model catalog is unavailable")
            try:
                catalog = ModelMetadataList.model_validate(response.body)
            except ValueError as error:
                raise ProviderUnavailableError("the hosted model catalog is invalid") from error
            local_names = {model.name for model in self._model_router.local_catalog()}
            hosted_models: tuple[ModelMetadata, ...] = tuple(
                model for model in catalog.models if model.name not in local_names
            )
            self._model_router.set_hosted_models(hosted_models)
            self._hosted_catalog_expires_at = now + HOSTED_MODEL_CATALOG_TTL_SECONDS

    async def _resolve_route(self, requested_model: str) -> ModelRoute:
        try:
            route = self._model_router.resolve(requested_model)
        except UnknownModelError:
            if not self._model_router.hosted_enabled:
                return _resolve_route(self._model_router, requested_model)
            await self._refresh_hosted_catalog()
            return _resolve_route(self._model_router, requested_model)
        if route.is_local:
            return route
        await self._refresh_hosted_catalog()
        return _resolve_route(self._model_router, requested_model)

    async def unload_all(self) -> dict[str, Any]:
        """Unload every idle local model and report each outcome."""

        results = await self._supervisor.unload_all()
        logger.info("all local providers unloaded")
        return {
            "status": "unloaded",
            "providers": [_unload_result_to_json(result) for result in results],
        }

    async def system_one(self, body: object, request_id: str) -> dict[str, Any]:
        """Run one TypeSafe-compatible decision through its selected provider."""

        request = parse_system_one_request(body)
        route = await self._resolve_route(request.model)
        return await self._run_request(request, route, request_id)

    async def system_one_batch(self, body: object, request_id: str) -> dict[str, Any]:
        """Run model groups concurrently and return results in request order."""

        batch = parse_system_one_batch_request(body, self._max_batch_requests)
        routes: list[ModelRoute] = []
        for index, request in enumerate(batch.requests):
            try:
                routes.append(await self._resolve_route(request.model))
            except TypeSafeValidationError as error:
                detail = [
                    {**entry, "loc": ["body", "requests", index, "model"]}
                    for entry in error.detail
                ]
                raise TypeSafeValidationError(detail) from error
        if not self._supervisor.ready:
            raise ProviderUnavailableError("local providers are not ready")

        groups: dict[str, list[tuple[int, SystemOneRequest, ModelRoute]]] = {}
        for index, (request, route) in enumerate(zip(batch.requests, routes, strict=True)):
            key = self._model_key(route)
            groups.setdefault(key, []).append((index, request, route))
        results: list[dict[str, Any] | None] = [None] * len(batch.requests)
        outcomes = await asyncio.gather(
            *(self._run_group(group, results, request_id) for group in groups.values()),
            return_exceptions=True,
        )
        for outcome in outcomes:
            if isinstance(outcome, BaseException):
                raise outcome
        return SystemOneBatchResponse.model_validate(
            {"results": cast(list[dict[str, Any]], results)}
        ).model_dump(mode="json")

    async def _run_group(
        self,
        group: list[tuple[int, SystemOneRequest, ModelRoute]],
        results: list[dict[str, Any] | None],
        request_id: str,
    ) -> None:
        for index, request, route in group:
            results[index] = await self._run_request(request, route, request_id, is_batch=True)

    @staticmethod
    def _model_key(route: ModelRoute) -> str:
        return route.provider_name if route.is_local else route.upstream_model

    @asynccontextmanager
    async def _batch_slot(self, is_batch: bool) -> AsyncGenerator[None]:
        if not is_batch or self._batch_semaphore is None:
            yield
            return
        async with self._batch_semaphore:
            yield

    @asynccontextmanager
    async def _local_lane(self, route: ModelRoute) -> AsyncGenerator[None]:
        if not route.is_local or (
            route.provider_name == CLM_PROVIDER_NAME and self._clm_parallel_with_local_models
        ):
            yield
            return

        is_clm = route.provider_name == CLM_PROVIDER_NAME
        async with self._local_condition:
            if is_clm:
                await self._local_condition.wait_for(lambda: self._active_local_models == 0)
                self._clm_active = True
            else:
                await self._local_condition.wait_for(
                    lambda: not self._clm_active
                    and (self._device != "cuda" or self._active_local_models == 0)
                )
                self._active_local_models += 1
        try:
            yield
        finally:
            async with self._local_condition:
                if is_clm:
                    self._clm_active = False
                else:
                    self._active_local_models -= 1
                self._local_condition.notify_all()

    async def _run_request(
        self,
        request: SystemOneRequest,
        route: ModelRoute,
        request_id: str,
        is_batch: bool = False,
    ) -> dict[str, Any]:
        if not self._supervisor.ready:
            raise ProviderUnavailableError("local providers are not ready")

        provider = self._providers.get(route.provider_name)
        if provider is None:
            logger.error(
                "configured provider client is missing", extra={"provider": route.provider_name}
            )
            raise ProviderUnavailableError("the selected provider is not configured")

        logger.info("system one request started", extra={"provider": route.provider_name})
        model_lock = self._model_locks.setdefault(self._model_key(route), asyncio.Lock())
        async with model_lock:
            async with self._local_lane(route):
                if route.is_local:
                    async with self._supervisor.acquire(route.provider_name):
                        async with self._batch_slot(is_batch):
                            result = await provider.forward(
                                native_provider_payload(route, request), request_id
                            )
                else:
                    async with self._batch_slot(is_batch):
                        result = await provider.forward(
                            native_provider_payload(route, request), request_id
                        )
        logger.info(
            "system one request completed",
            extra={"provider": route.provider_name, "status_code": result.status_code},
        )
        if result.status_code == status.HTTP_200_OK:
            return project_system_one_response(
                result.body,
                route.public_model if route.is_local else None,
            )
        if result.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT:
            content = provider_validation_content(result.body)
            raise TypeSafeValidationError(cast(list[dict[str, Any]], content["detail"]))
        logger.warning(
            "provider returned an unexpected status",
            extra={"provider": route.provider_name, "status_code": result.status_code},
        )
        raise ProviderUnavailableError("the selected provider returned an invalid response")


def _resolve_route(model_router: ModelRouter, requested_model: str) -> ModelRoute:
    try:
        return model_router.resolve(requested_model)
    except UnknownModelError as error:
        raise TypeSafeValidationError(unknown_model_detail(requested_model)) from error


def _unload_result_to_json(result: ProviderUnloadResult) -> dict[str, object]:
    return {"name": result.provider_name, "wasLoaded": result.was_loaded}
