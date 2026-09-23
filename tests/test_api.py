"""Contract tests exercise the public router and middleware with fake providers."""

from collections.abc import Iterator

import pytest
from pydantic import SecretStr

from decidealot.app import create_embedded_app
from decidealot.constants import LAYA_PROVIDER_NAME, VON_PROVIDER_NAME
from decidealot.providers import ProviderResponse
from decidealot.settings import Settings
from tests.conftest import (
    FakeProvider,
    HTTPClient,
    LifecycleSupervisor,
    app_client,
    native_system_one_response,
    projected_system_one_response,
    system_one_request,
)

_operator_api_key = SecretStr("operator-secret")
_operator_authorization = {"Authorization": "Bearer operator-secret"}


@pytest.fixture
def provider_pair() -> dict[str, FakeProvider]:
    return {
        LAYA_PROVIDER_NAME: FakeProvider(native_system_one_response("laya-rl-agent")),
        VON_PROVIDER_NAME: FakeProvider(native_system_one_response("von-1.1.0")),
    }


@pytest.fixture
def client(provider_pair: dict[str, FakeProvider]) -> Iterator[HTTPClient]:
    app = create_embedded_app(Settings(), provider_pair)
    with app_client(app) as test_client:
        yield test_client


def test_system_one_forwards_a_native_request_to_the_selected_provider(
    client: HTTPClient,
    provider_pair: dict[str, FakeProvider],
) -> None:
    response = client.post("/v1/systemone", json=system_one_request("laya-english"))

    assert response.status_code == 200
    assert response.json() == projected_system_one_response("laya-english")
    forwarded_payload, forwarded_request_id = provider_pair[LAYA_PROVIDER_NAME].calls[0]
    assert forwarded_payload["model"] == "english"
    assert forwarded_request_id == response.headers["X-Request-Id"]
    assert provider_pair[VON_PROVIDER_NAME].calls == []


def test_system_one_acquires_the_selected_provider_before_forwarding(
    provider_pair: dict[str, FakeProvider],
) -> None:
    supervisor = LifecycleSupervisor()
    app = create_embedded_app(Settings(), provider_pair, supervisor)

    with app_client(app) as client:
        response = client.post("/v1/systemone", json=system_one_request("von"))

    assert response.status_code == 200
    assert supervisor.acquired_providers == [VON_PROVIDER_NAME]
    assert supervisor.stopped


def test_system_one_returns_unavailable_when_selected_provider_fails(
    client: HTTPClient,
    provider_pair: dict[str, FakeProvider],
) -> None:
    provider_pair[LAYA_PROVIDER_NAME].unavailable = True

    response = client.post("/v1/systemone", json=system_one_request())

    assert response.status_code == 503
    assert response.json()["code"] == "PROVIDER_UNAVAILABLE"


def test_system_one_rejects_an_unexpected_provider_status(
    client: HTTPClient,
    provider_pair: dict[str, FakeProvider],
) -> None:
    provider_pair[LAYA_PROVIDER_NAME].response = ProviderResponse(
        status_code=401, body={"detail": "invalid or missing bearer token"}
    )

    response = client.post("/v1/systemone", json=system_one_request())

    assert response.status_code == 503
    assert response.json()["code"] == "PROVIDER_UNAVAILABLE"


def test_health_reports_lifecycle_readiness(client: HTTPClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "providers": ["laya", "von"]}


def test_all_model_unload_is_atomic_when_a_provider_is_busy(
    provider_pair: dict[str, FakeProvider],
) -> None:
    supervisor = LifecycleSupervisor()
    supervisor.loaded_providers = {LAYA_PROVIDER_NAME, VON_PROVIDER_NAME}
    supervisor.busy = True
    app = create_embedded_app(Settings(), provider_pair, supervisor)

    with app_client(app) as client:
        response = client.post("/v1/models/unload")

    assert response.status_code == 409
    assert response.json()["code"] == "PROVIDER_BUSY"
    assert supervisor.loaded_providers == {LAYA_PROVIDER_NAME, VON_PROVIDER_NAME}


def test_all_model_unload_reports_each_provider(
    provider_pair: dict[str, FakeProvider],
) -> None:
    supervisor = LifecycleSupervisor()
    app = create_embedded_app(Settings(), provider_pair, supervisor)

    with app_client(app) as client:
        loaded_response = client.post("/v1/systemone", json=system_one_request("laya"))
        response = client.post("/v1/models/unload")

    assert loaded_response.status_code == 200
    assert response.status_code == 200
    assert response.json() == {
        "status": "unloaded",
        "providers": [
            {"name": "laya", "wasLoaded": True},
            {"name": "von", "wasLoaded": False},
        ],
    }


def test_request_id_is_validated_and_forwarded(
    client: HTTPClient,
    provider_pair: dict[str, FakeProvider],
) -> None:
    request_id = "1d3fb045-4d61-4ecc-b169-cf012a10ea57"

    response = client.post(
        "/v1/systemone", headers={"X-Request-Id": request_id}, json=system_one_request()
    )

    assert response.status_code == 200
    assert response.headers["X-Request-Id"] == request_id
    assert provider_pair[LAYA_PROVIDER_NAME].calls[0][1] == request_id


def test_request_size_limit_blocks_provider_calls(provider_pair: dict[str, FakeProvider]) -> None:
    app = create_embedded_app(Settings(max_request_bytes=1024), provider_pair)

    with app_client(app) as client:
        response = client.post(
            "/v1/systemone", content="x" * 1025, headers={"Content-Type": "application/json"}
        )

    assert response.status_code == 413
    assert provider_pair[LAYA_PROVIDER_NAME].calls == []
