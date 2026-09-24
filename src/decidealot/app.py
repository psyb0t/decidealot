"""The public TypeSafe-compatible HTTP boundary."""

import logging
from collections.abc import AsyncGenerator, Mapping
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import Body, FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from mcp.server.transport_security import TransportSecuritySettings

from decidealot import __version__
from decidealot.constants import (
    ERROR_CODE_INVALID_REQUEST,
    ERROR_CODE_PROVIDER_BUSY,
    ERROR_CODE_PROVIDER_UNAVAILABLE,
    ERROR_CODE_UNAUTHORIZED,
    HEALTH_PATH,
    MCP_PATH,
    MODELS_PATH,
    MODELS_UNLOAD_PATH,
    SYSTEMONE_PATH,
)
from decidealot.decisions import DecisionService, Supervisor
from decidealot.errors import (
    InvalidRequestError,
    ProviderBusyError,
    ProviderUnavailableError,
    TypeSafeValidationError,
    UnauthorizedError,
    UnknownModelError,
)
from decidealot.mcp_server import create_mcp_server
from decidealot.providers import (
    ModelRouter,
    ProviderClient,
    default_provider_clients,
)
from decidealot.security import (
    BearerASGI,
    RequestIDMiddleware,
    RequestSizeMiddleware,
    SecurityHeadersMiddleware,
    require_bearer_token,
)
from decidealot.settings import Settings
from decidealot.supervisor import (
    DisabledProviderSupervisor,
    ProviderSupervisor,
)
from decidealot.typesafe import (
    validation_detail,
    validation_error_content,
)

logger = logging.getLogger(__name__)
_error_details_empty: dict[str, object] = {}


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
    model_router = ModelRouter()
    decisions = DecisionService(resolved_providers, resolved_supervisor, model_router)
    mcp_server = create_mcp_server(decisions)
    configured_api_key = (
        resolved_settings.api_key.get_secret_value()
        if resolved_settings.api_key is not None
        else None
    )
    mcp_app = BearerASGI(
        mcp_server.streamable_http_app(
            streamable_http_path=MCP_PATH,
            json_response=True,
            max_request_body_size=resolved_settings.max_request_bytes,
            transport_security=TransportSecuritySettings(
                enable_dns_rebinding_protection=True,
                allowed_hosts=list(resolved_settings.mcp_allowed_host_values),
                allowed_origins=list(resolved_settings.mcp_allowed_origin_values),
            ),
            host=resolved_settings.listen_host,
        ),
        configured_api_key,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None, None]:
        async with mcp_server.session_manager.run():
            await resolved_supervisor.start()
            logger.info(
                "decidealot started",
                extra={
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
        return decisions.model_catalog()

    @app.post(MODELS_UNLOAD_PATH, status_code=status.HTTP_200_OK)
    async def unload_all_models(request: Request) -> JSONResponse:
        _require_api_authentication(request, resolved_settings)
        return JSONResponse(status_code=status.HTTP_200_OK, content=await decisions.unload_all())

    @app.post(SYSTEMONE_PATH)
    async def system_one(request: Request, body: Any = Body(...)) -> JSONResponse:
        _require_api_authentication(request, resolved_settings)
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content=await decisions.system_one(body, request.state.request_id),
        )

    app.mount("/", mcp_app)

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
