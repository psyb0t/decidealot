"""The local HTTP boundary preserves provider responses and blocks bad upstream state."""

import json
from collections.abc import Callable

import httpx
import pytest
from pydantic import SecretStr

from decidealot.errors import ProviderUnavailableError
from decidealot.providers import HTTPProviderClient, default_provider_clients
from decidealot.settings import Settings

ProviderFailureFactory = Callable[[httpx.Request], httpx.HTTPError]


def _timeout_failure(request: httpx.Request) -> httpx.HTTPError:
    return httpx.ReadTimeout("provider did not respond", request=request)


def _connection_failure(request: httpx.Request) -> httpx.HTTPError:
    return httpx.ConnectError("provider is unavailable", request=request)


@pytest.mark.asyncio
async def test_http_provider_forwards_request_id_and_provider_payload() -> None:
    observed_request: httpx.Request | None = None

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal observed_request
        observed_request = request
        return httpx.Response(200, json={"model": "laya", "answers": {}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as transport:
        client = HTTPProviderClient("laya", "http://127.0.0.1:8011/v1/systemone", transport)
        response = await client.forward({"model": "laya", "questions": {}}, "request-123")

    assert response.status_code == 200
    assert response.body == {"model": "laya", "answers": {}}
    assert observed_request is not None
    assert observed_request.headers["X-Request-Id"] == "request-123"
    assert json.loads(observed_request.content) == {"model": "laya", "questions": {}}


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [500, 502, 503])
async def test_http_provider_hides_upstream_server_failure(status_code: int) -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json={"detail": "internal model details"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as transport:
        client = HTTPProviderClient("laya", "http://127.0.0.1:8011/v1/systemone", transport)
        with pytest.raises(ProviderUnavailableError):
            await client.forward({"questions": {}}, "request-123")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure", "expected_message"),
    [
        (_timeout_failure, "timed out"),
        (_connection_failure, "unavailable"),
    ],
)
async def test_http_provider_hides_transport_failures(
    failure: ProviderFailureFactory, expected_message: str
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise failure(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as transport:
        client = HTTPProviderClient("laya", "http://127.0.0.1:8011/v1/systemone", transport)
        with pytest.raises(ProviderUnavailableError, match=expected_message):
            await client.forward({"questions": {}}, "request-123")


@pytest.mark.asyncio
async def test_http_provider_hides_non_json_upstream_response() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not-json")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as transport:
        client = HTTPProviderClient("laya", "http://127.0.0.1:8011/v1/systemone", transport)
        with pytest.raises(ProviderUnavailableError, match="invalid response"):
            await client.forward({"questions": {}}, "request-123")


@pytest.mark.asyncio
async def test_http_provider_preserves_upstream_client_validation_failure() -> None:
    expected_body = {"detail": "question schema is invalid"}

    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json=expected_body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as transport:
        client = HTTPProviderClient("von", "http://127.0.0.1:8012/v1/systemone", transport)
        response = await client.forward({"questions": {}}, "request-123")

    assert response.status_code == 422
    assert response.body == expected_body


@pytest.mark.asyncio
async def test_default_provider_clients_use_fixed_loopback_targets() -> None:
    providers, client = default_provider_clients(Settings(request_timeout_seconds=12.5))
    try:
        assert set(providers) == {"laya", "von"}
        assert client.follow_redirects is False
        assert client.timeout.read == 12.5
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_default_provider_clients_only_create_configured_loopback_targets() -> None:
    providers, client = default_provider_clients(
        Settings(
            laya_enabled=False,
            von_enabled=False,
            clm_enabled=True,
            clm_embeddings_url="https://embeddings.example.test/v1/embeddings",
        )
    )
    try:
        assert set(providers) == {"clm"}
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_hosted_jev_client_sends_private_key_only_to_the_fixed_typesafe_endpoint() -> None:
    observed_request: httpx.Request | None = None

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal observed_request
        observed_request = request
        return httpx.Response(200, json={"model": "jev-latest", "answers": {}, "usage": {}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as transport:
        client = HTTPProviderClient(
            "jev",
            "https://api.typesafe.ai/v1/systemone",
            transport,
            SecretStr("upstream-secret"),
        )
        await client.forward({"model": "jev-latest", "state": "test"}, "request-123")

    assert observed_request is not None
    assert str(observed_request.url) == "https://api.typesafe.ai/v1/systemone"
    assert observed_request.headers["Authorization"] == "Bearer upstream-secret"
    assert observed_request.headers["X-Request-Id"] == "request-123"
    assert b"upstream-secret" not in observed_request.content


@pytest.mark.asyncio
async def test_hosted_jev_authentication_failure_does_not_expose_upstream_body() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": "sensitive upstream account detail"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as transport:
        client = HTTPProviderClient(
            "jev", "https://api.typesafe.ai/v1/systemone", transport, SecretStr("upstream-secret")
        )
        result = await client.forward({"model": "jev-latest"}, "request-123")

    assert result.status_code == 401
    assert result.body == {"detail": "sensitive upstream account detail"}


@pytest.mark.asyncio
async def test_hosted_model_discovery_uses_the_fixed_authenticated_endpoint() -> None:
    observed_request: httpx.Request | None = None
    catalog = {
        "models": [{"name": "new-model", "description": "New", "release_date": "2026-10-02"}]
    }

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal observed_request
        observed_request = request
        return httpx.Response(200, json=catalog)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as transport:
        client = HTTPProviderClient(
            "jev", "https://api.typesafe.ai/v1/systemone", transport, SecretStr("upstream-secret")
        )
        response = await client.list_models()

    assert response.body == catalog
    assert observed_request is not None
    assert observed_request.method == "GET"
    assert str(observed_request.url) == "https://api.typesafe.ai/v1/models"
    assert observed_request.headers["Authorization"] == "Bearer upstream-secret"


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [401, 403, 429, 500])
async def test_hosted_model_discovery_hides_upstream_failures(status_code: int) -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json={"detail": "private account data"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as transport:
        client = HTTPProviderClient(
            "jev", "https://api.typesafe.ai/v1/systemone", transport, SecretStr("upstream-secret")
        )
        with pytest.raises(ProviderUnavailableError, match="catalog is unavailable"):
            await client.list_models()
