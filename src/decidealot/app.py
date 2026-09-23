"""The public TypeSafe-compatible HTTP boundary."""

import logging
from collections.abc import AsyncGenerator, Mapping
from contextlib import asynccontextmanager
from typing import Any, Protocol

import httpx
from fastapi import Body, FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from decidealot import __version__
from decidealot.constants import (
    ERROR_CODE_INVALID_REQUEST,
    ERROR_CODE_PROVIDER_BUSY,
    ERROR_CODE_PROVIDER_UNAVAILABLE,
    ERROR_CODE_UNAUTHORIZED,
    HEALTH_PATH,
    MODEL_UNLOAD_PATH,
    MODELS_PATH,
    MODELS_UNLOAD_PATH,
    SYSTEMONE_PATH,
)
from decidealot.errors import (
    InvalidRequestError,
    ProviderBusyError,
    ProviderUnavailableError,
    TypeSafeValidationError,
    UnauthorizedError,
    UnknownModelError,
)
from decidealot.providers import (
    ModelRoute,
    ModelRouter,
    ProviderClient,
    ProviderResponse,
    default_provider_clients,
    native_provider_payload,
)
from decidealot.security import (
    RequestIDMiddleware,
    RequestSizeMiddleware,
    SecurityHeadersMiddleware,
    require_bearer_token,
)
from decidealot.settings import Settings
from decidealot.supervisor import (
    DisabledProviderSupervisor,
    ProviderSupervisor,
    ProviderUnloadResult,
)
from decidealot.typesafe import (
    ModelMetadataList,
    parse_system_one_request,
    project_system_one_response,
    provider_validation_content,
    unknown_model_detail,
    validation_detail,
    validation_error_content,
)

logger = logging.getLogger(__name__)
_error_details_empty: dict[str, object] = {}


class Supervisor(Protocol):
    """The lifecycle surface needed by the application service."""

    @property
    def ready(self) -> bool: ...

    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    def acquire(self, provider_name: str) -> Any: ...

    async def unload_provider(self, provider_name: str) -> ProviderUnloadResult: ...

    async def unload_all(self) -> tuple[ProviderUnloadResult, ...]: ...


def create_app(
    settings: Settings | None = None,
    providers: Mapping[str, ProviderClient] | None = None,
    supervisor: Supervisor | None = None,
) -> FastAPI:
    """Build a public router around fixed local Laya and Von endpoints."""

    resolved_settings = settings or Settings()
    owns_http_client = providers is None
    http_client: httpx.AsyncClient | None = None
    if providers is None:
        resolved_providers, http_client = default_provider_clients(
            resolved_settings.request_timeout_seconds
        )
    else:
        resolved_providers = dict(providers)
    resolved_supervisor = supervisor or ProviderSupervisor(resolved_settings)
    model_router = ModelRouter(resolved_settings.default_model)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None, None]:
        await resolved_supervisor.start()
        logger.info(
            "decidealot started",
            extra={
                "default_model": resolved_settings.default_model,
                "device": resolved_settings.device,
            },
        )
        try:
            yield
        finally:
            await resolved_supervisor.stop()
            if owns_http_client and http_client is not None:
                await http_client.aclose()
            logger.info("decidealot stopped")

    app = FastAPI(title="Decidealot", version=__version__, lifespan=lifespan)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RequestSizeMiddleware, max_request_bytes=resolved_settings.max_request_bytes)
    app.add_middleware(RequestIDMiddleware)

    @app.exception_handler(TypeSafeValidationError)
    async def typesafe_validation_error_handler(
        _: Request, error: TypeSafeValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content=validation_error_content(error.detail),
        )

    @app.exception_handler(RequestValidationError)
    async def request_validation_error_handler(
        _: Request, error: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content=validation_error_content(validation_detail(error.errors())),
        )

    @app.exception_handler(InvalidRequestError)
    async def invalid_request_error_handler(_: Request, error: InvalidRequestError) -> JSONResponse:
        details: dict[str, object] = {}
        if isinstance(error, UnknownModelError):
            details["supportedModels"] = list(model_router.supported_models)
        return _error_response(
            status.HTTP_400_BAD_REQUEST, ERROR_CODE_INVALID_REQUEST, str(error), details
        )

    @app.exception_handler(UnauthorizedError)
    async def unauthorized_error_handler(_: Request, error: UnauthorizedError) -> JSONResponse:
        return _error_response(
            status.HTTP_401_UNAUTHORIZED,
            ERROR_CODE_UNAUTHORIZED,
            str(error),
            headers={"WWW-Authenticate": "Bearer"},
        )

    @app.exception_handler(ProviderUnavailableError)
    async def unavailable_error_handler(
        _: Request, error: ProviderUnavailableError
    ) -> JSONResponse:
        return _error_response(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            ERROR_CODE_PROVIDER_UNAVAILABLE,
            str(error),
        )

    @app.exception_handler(ProviderBusyError)
    async def busy_provider_error_handler(_: Request, error: ProviderBusyError) -> JSONResponse:
        return _error_response(
            status.HTTP_409_CONFLICT,
            ERROR_CODE_PROVIDER_BUSY,
            str(error),
        )

    @app.get(HEALTH_PATH, status_code=status.HTTP_200_OK)
    async def health() -> JSONResponse:
        if not resolved_supervisor.ready:
            return _error_response(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                ERROR_CODE_PROVIDER_UNAVAILABLE,
                "local providers are not ready",
            )
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={"status": "ok", "providers": ["laya", "von"]},
        )

    @app.get(MODELS_PATH, status_code=status.HTTP_200_OK)
    async def models(request: Request) -> dict[str, Any]:
        _require_api_authentication(request, resolved_settings)
        catalog = ModelMetadataList(models=list(model_router.catalog()))
        return catalog.model_dump(mode="json")

    @app.post(MODELS_UNLOAD_PATH, status_code=status.HTTP_200_OK)
    async def unload_all_models(request: Request) -> JSONResponse:
        _require_api_authentication(request, resolved_settings)
        results = await resolved_supervisor.unload_all()
        logger.info("all local providers unloaded")
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={"status": "unloaded", "providers": _unload_results_to_json(results)},
        )

    @app.post(MODEL_UNLOAD_PATH, status_code=status.HTTP_200_OK)
    async def unload_model(model: str, request: Request) -> JSONResponse:
        _require_api_authentication(request, resolved_settings)
        route = model_router.resolve(model)
        result = await resolved_supervisor.unload_provider(route.provider_name)
        logger.info("local provider unloaded", extra={"provider": route.provider_name})
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={
                "status": "unloaded",
                "model": model,
                "provider": _unload_result_to_json(result),
            },
        )

    @app.post(SYSTEMONE_PATH)
    async def system_one(request: Request, body: Any = Body(...)) -> JSONResponse:
        _require_api_authentication(request, resolved_settings)
        system_one_request = parse_system_one_request(body)
        route = _resolve_route(model_router, system_one_request.model)
        if not resolved_supervisor.ready:
            raise ProviderUnavailableError("local providers are not ready")

        provider = resolved_providers.get(route.provider_name)
        if provider is None:
            logger.error(
                "configured provider client is missing", extra={"provider": route.provider_name}
            )
            raise ProviderUnavailableError("the selected local provider is not configured")

        request_id = request.state.request_id
        logger.info("system one request started", extra={"provider": route.provider_name})
        async with resolved_supervisor.acquire(route.provider_name):
            result = await provider.forward(
                native_provider_payload(route, system_one_request), request_id
            )
        logger.info(
            "system one request completed",
            extra={"provider": route.provider_name, "status_code": result.status_code},
        )
        return _system_one_response(result, route)

    return app


def create_embedded_app(
    settings: Settings,
    providers: Mapping[str, ProviderClient],
    supervisor: Supervisor | None = None,
) -> FastAPI:
    """Build an app for callers that own the local provider lifecycle themselves."""

    return create_app(
        settings=settings,
        providers=providers,
        supervisor=supervisor or DisabledProviderSupervisor(),
    )


def _resolve_route(model_router: ModelRouter, requested_model: str) -> ModelRoute:
    """Select the local backend, reporting an unknown selector the official way."""

    try:
        return model_router.resolve(requested_model)
    except UnknownModelError as error:
        raise TypeSafeValidationError(unknown_model_detail(requested_model)) from error


def _system_one_response(result: ProviderResponse, route: ModelRoute) -> JSONResponse:
    """Answer with the official success or validation shape for a provider result."""

    if result.status_code == status.HTTP_200_OK:
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content=project_system_one_response(result.body, route.public_model),
        )
    if result.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content=provider_validation_content(result.body),
        )
    logger.warning(
        "local provider returned an unexpected status",
        extra={"provider": route.provider_name, "status_code": result.status_code},
    )
    raise ProviderUnavailableError("the selected local provider returned an invalid response")


def _error_response(
    status_code: int,
    code: str,
    message: str,
    details: Mapping[str, object] | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"code": code, "message": message, "details": details or _error_details_empty},
        headers=headers,
    )


def _require_api_authentication(request: Request, settings: Settings) -> None:
    configured_api_key = (
        settings.api_key.get_secret_value() if settings.api_key is not None else None
    )
    require_bearer_token(request.headers.get("Authorization"), configured_api_key)


def _unload_results_to_json(results: tuple[ProviderUnloadResult, ...]) -> list[dict[str, object]]:
    return [_unload_result_to_json(result) for result in results]


def _unload_result_to_json(result: ProviderUnloadResult) -> dict[str, object]:
    return {"name": result.provider_name, "wasLoaded": result.was_loaded}
