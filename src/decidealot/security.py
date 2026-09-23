"""Authentication and HTTP-boundary validation helpers."""

import hmac
import logging
import re
from collections.abc import Awaitable, Callable
from uuid import uuid4

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from starlette.types import ASGIApp

from decidealot.constants import (
    BEARER_PREFIX,
    ERROR_CODE_REQUEST_TOO_LARGE,
    MAX_REQUEST_ID_LENGTH,
    REQUEST_ID_HEADER,
)
from decidealot.errors import UnauthorizedError
from decidealot.logging_config import reset_scope, set_scope

RequestHandler = Callable[[Request], Awaitable[Response]]
logger = logging.getLogger(__name__)
REQUEST_ID_PATTERN = re.compile(
    r"^(?:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|[0-9A-HJKMNP-TV-Z]{26})$",
    re.IGNORECASE,
)


def request_id_from_header(value: str | None) -> str:
    """Keep a safe caller correlation ID or mint a UUID."""

    if value and len(value) <= MAX_REQUEST_ID_LENGTH and REQUEST_ID_PATTERN.fullmatch(value):
        return value
    return str(uuid4())


def require_bearer_token(authorization: str | None, configured_api_key: str | None) -> None:
    """Authenticate an API request when the operator configured a key."""

    if configured_api_key is None:
        return
    if authorization is None or not authorization.startswith(BEARER_PREFIX):
        logger.warning(
            "request authentication failed", extra={"reason": "missing_or_malformed_bearer"}
        )
        raise UnauthorizedError("a valid bearer token is required")
    supplied_token = authorization.removeprefix(BEARER_PREFIX)
    if not hmac.compare_digest(supplied_token.encode(), configured_api_key.encode()):
        logger.warning("request authentication failed", extra={"reason": "wrong_bearer"})
        raise UnauthorizedError("a valid bearer token is required")


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Seed request-scoped structured logging and return the correlation ID."""

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next: RequestHandler) -> Response:
        request_id = request_id_from_header(request.headers.get(REQUEST_ID_HEADER))
        request.state.request_id = request_id
        scope_token = set_scope(request_id=request_id)
        try:
            response = await call_next(request)
        finally:
            reset_scope(scope_token)
        response.headers[REQUEST_ID_HEADER] = request_id
        return response


class RequestSizeMiddleware(BaseHTTPMiddleware):
    """Reject oversized JSON requests before they reach a local model."""

    def __init__(self, app: ASGIApp, max_request_bytes: int) -> None:
        super().__init__(app)
        self._max_request_bytes = max_request_bytes

    async def dispatch(self, request: Request, call_next: RequestHandler) -> Response:
        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                parsed_content_length = int(content_length)
            except ValueError:
                parsed_content_length = self._max_request_bytes + 1
            if parsed_content_length > self._max_request_bytes:
                logger.warning("request rejected", extra={"reason": "body_too_large"})
                return JSONResponse(
                    status_code=413,
                    content={
                        "code": ERROR_CODE_REQUEST_TOO_LARGE,
                        "message": "request body exceeds the configured size limit",
                        "details": {},
                    },
                )
        return await call_next(request)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Set response headers appropriate for a JSON-only local API."""

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next: RequestHandler) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response
