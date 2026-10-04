"""Contract tests exercise the public router and middleware with fake providers."""

import asyncio
import json
from collections.abc import Iterator
from typing import Literal

import httpx
import pytest
from pydantic import SecretStr

from decidealot.app import create_embedded_app
from decidealot.constants import (
    CLM_PROVIDER_NAME,
    JEV_PROVIDER_NAME,
    LAYA_PROVIDER_NAME,
    VON_PROVIDER_NAME,
)
from decidealot.decisions import DecisionService
from decidealot.providers import HTTPProviderClient, ModelRouter, ProviderResponse
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
_hosted_catalog = {
    "models": [{"name": "jev-latest", "description": "Hosted model", "release_date": "2026-09-10"}]
}


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


def test_batch_runs_independent_requests_and_preserves_order(
    provider_pair: dict[str, FakeProvider],
) -> None:
    app = create_embedded_app(
        Settings(max_resident_local_providers=2), provider_pair, LifecycleSupervisor()
    )
    first = system_one_request("von")
    first["state"] = "First decision"
    second = system_one_request("laya-english")
    second["state"] = "Second decision"

    with app_client(app) as client:
        response = client.post("/v1/systemone/batch", json={"requests": [first, second]})

    assert response.status_code == 200
    assert response.json() == {
        "results": [
            projected_system_one_response("von-1.1"),
            projected_system_one_response("laya-english"),
        ]
    }
    assert provider_pair[VON_PROVIDER_NAME].calls[0][0]["state"] == "First decision"
    assert provider_pair[LAYA_PROVIDER_NAME].calls[0][0]["state"] == "Second decision"


def test_batch_accepts_repeated_model_with_different_states(
    client: HTTPClient, provider_pair: dict[str, FakeProvider]
) -> None:
    first = system_one_request("laya")
    second = system_one_request("laya")
    second["state"] = "Another decision"

    response = client.post("/v1/systemone/batch", json={"requests": [first, second]})

    assert response.status_code == 200
    assert len(response.json()["results"]) == 2
    assert [call[0]["state"] for call in provider_pair[LAYA_PROVIDER_NAME].calls] == [
        first["state"],
        second["state"],
    ]


def test_batch_runs_local_and_hosted_jev_with_one_local_slot(
    provider_pair: dict[str, FakeProvider],
) -> None:
    settings = Settings(typesafe_api_key=SecretStr("upstream-secret"))
    providers = {
        **provider_pair,
        JEV_PROVIDER_NAME: FakeProvider(native_system_one_response("jev-latest")),
    }
    supervisor = LifecycleSupervisor()
    app = create_embedded_app(settings, providers, supervisor)

    with app_client(app) as client:
        response = client.post(
            "/v1/systemone/batch",
            json={"requests": [system_one_request("laya"), system_one_request("jev-latest")]},
        )

    assert response.status_code == 200
    assert [item["model"] for item in response.json()["results"]] == ["laya", "jev-latest"]
    assert supervisor.acquired_providers == [LAYA_PROVIDER_NAME]
    assert len(providers[JEV_PROVIDER_NAME].calls) == 1


def test_batch_with_one_request_has_one_result(client: HTTPClient) -> None:
    response = client.post("/v1/systemone/batch", json={"requests": [system_one_request("laya")]})

    assert response.status_code == 200
    assert response.json() == {"results": [projected_system_one_response("laya")]}


def test_batch_accepts_more_than_eight_requests_by_default(
    client: HTTPClient, provider_pair: dict[str, FakeProvider]
) -> None:
    requests = [system_one_request("laya") for _ in range(9)]
    response = client.post("/v1/systemone/batch", json={"requests": requests})

    assert response.status_code == 200
    assert len(response.json()["results"]) == len(requests)
    assert len(provider_pair[LAYA_PROVIDER_NAME].calls) == len(requests)


def test_batch_rejects_configured_item_limit_before_forwarding(
    provider_pair: dict[str, FakeProvider],
) -> None:
    app = create_embedded_app(Settings(max_batch_requests=2), provider_pair)
    with app_client(app) as client:
        accepted = client.post(
            "/v1/systemone/batch",
            json={"requests": [system_one_request("laya") for _ in range(2)]},
        )
        rejected = client.post(
            "/v1/systemone/batch",
            json={"requests": [system_one_request("laya") for _ in range(3)]},
        )

    assert accepted.status_code == 200
    assert len(accepted.json()["results"]) == 2
    assert rejected.status_code == 422
    assert rejected.json()["detail"][0]["loc"] == ["body", "requests"]
    assert len(provider_pair[LAYA_PROVIDER_NAME].calls) == 2


def test_batch_forwards_to_distinct_providers_concurrently() -> None:
    started = 0
    both_started = asyncio.Event()

    class ConcurrentProvider:
        async def forward(self, payload: object, request_id: str) -> ProviderResponse:
            nonlocal started
            del payload, request_id
            started += 1
            if started == 2:
                both_started.set()
            await asyncio.wait_for(both_started.wait(), timeout=1)
            return native_system_one_response("fixture")

    providers = {
        LAYA_PROVIDER_NAME: ConcurrentProvider(),
        VON_PROVIDER_NAME: ConcurrentProvider(),
    }
    app = create_embedded_app(
        Settings(max_resident_local_providers=2), providers, LifecycleSupervisor()
    )

    with app_client(app) as client:
        response = client.post(
            "/v1/systemone/batch",
            json={"requests": [system_one_request("laya"), system_one_request("von")]},
        )

    assert response.status_code == 200
    assert started == 2
    assert [item["model"] for item in response.json()["results"]] == ["laya", "von-1.1"]


@pytest.mark.parametrize(
    ("device", "first_model", "second_model", "clm_parallel", "expected_peak"),
    [
        ("cpu", "laya", "laya-english", False, 1),
        ("cpu", "laya", "von", False, 2),
        ("cuda", "laya", "von", False, 1),
        ("cpu", "laya", "clm", False, 1),
        ("cuda", "laya", "clm", False, 1),
        ("cuda", "laya", "clm", True, 2),
        ("cuda", "laya", "jev-latest", False, 2),
        ("cpu", "jev-latest", "jev-latest", False, 1),
    ],
)
def test_batch_schedules_provider_calls_by_model_and_resource(
    device: Literal["cpu", "cuda"],
    first_model: str,
    second_model: str,
    clm_parallel: bool,
    expected_peak: int,
) -> None:
    active = 0
    peak = 0

    class TrackingProvider:
        async def list_models(self) -> ProviderResponse:
            return ProviderResponse(200, _hosted_catalog)

        async def forward(self, payload: object, request_id: str) -> ProviderResponse:
            nonlocal active, peak
            del payload, request_id
            active += 1
            peak = max(peak, active)
            try:
                await asyncio.sleep(0.02)
                return native_system_one_response("fixture")
            finally:
                active -= 1

    settings = Settings(
        device=device,
        image_variant=device,
        max_resident_local_providers=2,
        clm_embeddings_url="https://embeddings.example.test/v1/embeddings",
        clm_parallel_with_local_models=clm_parallel,
        typesafe_api_key=SecretStr("upstream-secret"),
    )
    provider_names = (LAYA_PROVIDER_NAME, VON_PROVIDER_NAME, CLM_PROVIDER_NAME, JEV_PROVIDER_NAME)
    providers = {provider_name: TrackingProvider() for provider_name in provider_names}
    app = create_embedded_app(settings, providers, LifecycleSupervisor())

    with app_client(app) as client:
        response = client.post(
            "/v1/systemone/batch",
            json={"requests": [system_one_request(first_model), system_one_request(second_model)]},
        )

    assert response.status_code == 200
    assert len(response.json()["results"]) == 2
    assert peak == expected_peak


@pytest.mark.parametrize(
    ("configured_concurrency", "expected_peak"),
    [(0, 3), (1, 1), (2, 2)],
)
def test_batch_concurrent_call_limit_controls_provider_overlap(
    configured_concurrency: int, expected_peak: int
) -> None:
    active = 0
    peak = 0

    class TrackingProvider:
        async def list_models(self) -> ProviderResponse:
            return ProviderResponse(200, _hosted_catalog)

        async def forward(self, payload: object, request_id: str) -> ProviderResponse:
            nonlocal active, peak
            del payload, request_id
            active += 1
            peak = max(peak, active)
            try:
                await asyncio.sleep(0.02)
                return native_system_one_response("fixture")
            finally:
                active -= 1

    settings = Settings(
        max_resident_local_providers=2,
        max_batch_concurrency=configured_concurrency,
        typesafe_api_key=SecretStr("upstream-secret"),
    )
    providers = {
        name: TrackingProvider()
        for name in (LAYA_PROVIDER_NAME, VON_PROVIDER_NAME, JEV_PROVIDER_NAME)
    }
    app = create_embedded_app(settings, providers, LifecycleSupervisor())

    with app_client(app) as client:
        response = client.post(
            "/v1/systemone/batch",
            json={
                "requests": [
                    system_one_request("laya"),
                    system_one_request("von"),
                    system_one_request("jev-latest"),
                ]
            },
        )

    assert response.status_code == 200
    assert len(response.json()["results"]) == 3
    assert peak == expected_peak


def test_batch_fails_safely_when_one_provider_is_unavailable(
    provider_pair: dict[str, FakeProvider],
) -> None:
    provider_pair[VON_PROVIDER_NAME].unavailable = True
    app = create_embedded_app(
        Settings(max_resident_local_providers=2), provider_pair, LifecycleSupervisor()
    )

    with app_client(app) as client:
        response = client.post(
            "/v1/systemone/batch",
            json={"requests": [system_one_request("laya"), system_one_request("von")]},
        )

    assert response.status_code == 503
    assert response.json()["code"] == "PROVIDER_UNAVAILABLE"
    assert len(provider_pair[LAYA_PROVIDER_NAME].calls) == 1
    assert len(provider_pair[VON_PROVIDER_NAME].calls) == 1


@pytest.mark.parametrize(
    "requests",
    [[], [system_one_request(), {"model": "von", "questions": {}}]],
)
def test_batch_rejects_invalid_items_before_any_provider_call(
    client: HTTPClient, provider_pair: dict[str, FakeProvider], requests: list[dict[str, object]]
) -> None:
    response = client.post("/v1/systemone/batch", json={"requests": requests})

    assert response.status_code == 422
    assert response.json()["detail"]
    assert all(provider.calls == [] for provider in provider_pair.values())


def test_batch_rejects_unknown_model_before_any_provider_call(
    client: HTTPClient, provider_pair: dict[str, FakeProvider]
) -> None:
    response = client.post(
        "/v1/systemone/batch",
        json={"requests": [system_one_request("laya"), system_one_request("missing")]},
    )

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "requests", 1, "model"]
    assert all(provider.calls == [] for provider in provider_pair.values())


def test_batch_switches_local_providers_with_one_resident_slot(
    client: HTTPClient, provider_pair: dict[str, FakeProvider]
) -> None:
    response = client.post(
        "/v1/systemone/batch",
        json={"requests": [system_one_request("laya"), system_one_request("von")]},
    )

    assert response.status_code == 200
    assert [item["model"] for item in response.json()["results"]] == ["laya", "von-1.1"]
    assert all(len(provider.calls) == 1 for provider in provider_pair.values())


def test_batch_authentication_precedes_provider_calls() -> None:
    provider = FakeProvider(native_system_one_response("laya"))
    app = create_embedded_app(Settings(api_key=_operator_api_key), {LAYA_PROVIDER_NAME: provider})

    with app_client(app) as client:
        response = client.post("/v1/systemone/batch", json={"requests": [system_one_request()]})

    assert response.status_code == 401
    assert provider.calls == []


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


def test_clm_only_configuration_exposes_only_clm_over_the_public_router() -> None:
    settings = Settings(
        laya_enabled=False,
        von_enabled=False,
        clm_enabled=True,
        clm_embeddings_url="https://embeddings.example.test/v1/embeddings",
    )
    providers = {CLM_PROVIDER_NAME: FakeProvider(native_system_one_response("clm-0.1-8b"))}
    supervisor = LifecycleSupervisor(provider_names=(CLM_PROVIDER_NAME,))
    app = create_embedded_app(settings, providers, supervisor)

    with app_client(app) as client:
        health_response = client.get("/health")
        models_response = client.get("/v1/models")
        decision_response = client.post("/v1/systemone", json=system_one_request("clm"))
        unload_response = client.post("/v1/models/unload")

    assert health_response.json() == {"status": "ok", "providers": ["clm"]}
    assert [model["name"] for model in models_response.json()["models"]] == [
        "clm",
        "clm-latest",
        "clm-0.1",
        "clm-0.1-8b",
    ]
    assert decision_response.status_code == 200
    assert decision_response.json()["model"] == "clm-0.1-8b"
    assert supervisor.acquired_providers == [CLM_PROVIDER_NAME]
    assert unload_response.json() == {
        "status": "unloaded",
        "providers": [{"name": "clm", "wasLoaded": True}],
    }


def test_jev_only_configuration_forwards_original_json_without_local_lifecycle() -> None:
    settings = Settings(
        laya_enabled=False,
        von_enabled=False,
        typesafe_api_key=SecretStr("upstream-secret"),
    )
    provider = FakeProvider(native_system_one_response("jev-latest"))
    supervisor = LifecycleSupervisor(provider_names=())
    app = create_embedded_app(settings, {JEV_PROVIDER_NAME: provider}, supervisor)
    request = system_one_request("jev-latest")
    request["questions"]["route"]["instructions"] = {"task": "Select a queue", "lang": "français"}
    request["questions"]["route"]["criteria"]["allow"] = {"label": "Permitted"}

    with app_client(app) as client:
        health_response = client.get("/health")
        models_response = client.get("/v1/models")
        decision_response = client.post("/v1/systemone", json=request)
        unload_response = client.post("/v1/models/unload")

    assert health_response.json() == {"status": "ok", "providers": ["jev"]}
    assert [model["name"] for model in models_response.json()["models"]] == [
        "jev-latest",
        "jev-preview",
    ]
    assert decision_response.status_code == 200
    assert decision_response.json() == projected_system_one_response("jev-latest")
    assert provider.calls[0][0] == request
    assert supervisor.acquired_providers == []
    assert unload_response.json() == {"status": "unloaded", "providers": []}


def test_hosted_catalog_discovers_and_routes_a_new_upstream_model() -> None:
    settings = Settings(laya_enabled=False, von_enabled=False, typesafe_api_key=SecretStr("key"))
    provider = FakeProvider(native_system_one_response("actual-model"))
    provider.model_catalog = ProviderResponse(
        200,
        {
            "models": [
                {
                    "name": "new-model",
                    "description": "New upstream model",
                    "release_date": "2026-10-02",
                }
            ]
        },
    )
    app = create_embedded_app(
        settings, {JEV_PROVIDER_NAME: provider}, LifecycleSupervisor(provider_names=())
    )

    with app_client(app) as client:
        models = client.get("/v1/models")
        decision = client.post("/v1/systemone", json=system_one_request("new-model"))
        rejected = client.post("/v1/systemone", json=system_one_request("jev-latest"))

    assert models.status_code == 200
    assert models.json() == provider.model_catalog.body
    assert decision.status_code == 200
    assert decision.json()["model"] == "actual-model"
    assert provider.calls[0][0]["model"] == "new-model"
    assert rejected.status_code == 422
    assert len(provider.calls) == 1
    assert provider.catalog_calls == 1


def test_hosted_catalog_and_decision_use_one_fixed_http_client() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "models": [
                        {
                            "name": "new-model",
                            "description": "New hosted model",
                            "release_date": "2026-10-02",
                        }
                    ]
                },
            )
        return httpx.Response(200, json=native_system_one_response("new-model").body)

    settings = Settings(laya_enabled=False, von_enabled=False, typesafe_api_key=SecretStr("key"))
    upstream = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)
    provider = HTTPProviderClient(
        JEV_PROVIDER_NAME, "https://api.typesafe.ai/v1/systemone", upstream, SecretStr("key")
    )
    app = create_embedded_app(
        settings, {JEV_PROVIDER_NAME: provider}, LifecycleSupervisor(provider_names=())
    )
    try:
        with app_client(app) as client:
            models = client.get("/v1/models")
            decision = client.post("/v1/systemone", json=system_one_request("new-model"))
    finally:
        asyncio.run(upstream.aclose())

    assert models.status_code == 200
    assert [model["name"] for model in models.json()["models"]] == ["new-model"]
    assert decision.status_code == 200
    assert [request.method for request in requests] == ["GET", "POST"]
    assert [str(request.url) for request in requests] == [
        "https://api.typesafe.ai/v1/models",
        "https://api.typesafe.ai/v1/systemone",
    ]
    assert all(request.headers["Authorization"] == "Bearer key" for request in requests)


def test_hosted_catalog_failure_does_not_block_local_decisions() -> None:
    settings = Settings(typesafe_api_key=SecretStr("key"))
    local_provider = FakeProvider(native_system_one_response("laya"))
    hosted_provider = FakeProvider(native_system_one_response("jev-latest"))
    hosted_provider.unavailable = True
    app = create_embedded_app(
        settings,
        {LAYA_PROVIDER_NAME: local_provider, JEV_PROVIDER_NAME: hosted_provider},
        LifecycleSupervisor(),
    )

    with app_client(app) as client:
        local = client.post("/v1/systemone", json=system_one_request("laya"))
        models = client.get("/v1/models")
        hosted = client.post("/v1/systemone", json=system_one_request("jev-latest"))

    assert local.status_code == 200
    assert models.status_code == 503
    assert hosted.status_code == 503
    assert len(local_provider.calls) == 1
    assert hosted_provider.calls == []


def test_hosted_catalog_rejects_invalid_metadata_without_forwarding() -> None:
    settings = Settings(laya_enabled=False, von_enabled=False, typesafe_api_key=SecretStr("key"))
    provider = FakeProvider(native_system_one_response("jev-latest"))
    provider.model_catalog = ProviderResponse(200, {"models": [{"name": "jev-latest"}]})
    app = create_embedded_app(
        settings, {JEV_PROVIDER_NAME: provider}, LifecycleSupervisor(provider_names=())
    )

    with app_client(app) as client:
        models = client.get("/v1/models")
        decision = client.post("/v1/systemone", json=system_one_request("jev-latest"))

    assert models.status_code == 503
    assert decision.status_code == 503
    assert provider.calls == []


@pytest.mark.asyncio
async def test_concurrent_hosted_catalog_reads_share_one_refresh() -> None:
    settings = Settings(laya_enabled=False, von_enabled=False, typesafe_api_key=SecretStr("key"))
    provider = FakeProvider(native_system_one_response("jev-latest"))
    decisions = DecisionService(
        {JEV_PROVIDER_NAME: provider},
        LifecycleSupervisor(provider_names=()),
        ModelRouter(settings),
        settings.max_batch_requests,
        settings.max_batch_concurrency,
        settings.device,
        settings.clm_parallel_with_local_models,
    )

    catalogs = await asyncio.gather(*(decisions.model_catalog() for _ in range(5)))

    assert all(catalog == catalogs[0] for catalog in catalogs)
    assert provider.catalog_calls == 1


@pytest.mark.asyncio
async def test_catalog_refresh_replaces_removed_names(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("decidealot.decisions.HOSTED_MODEL_CATALOG_TTL_SECONDS", 0.0)
    settings = Settings(laya_enabled=False, von_enabled=False, typesafe_api_key=SecretStr("key"))
    provider = FakeProvider(native_system_one_response("new-model"))
    decisions = DecisionService(
        {JEV_PROVIDER_NAME: provider},
        LifecycleSupervisor(provider_names=()),
        ModelRouter(settings),
        settings.max_batch_requests,
        settings.max_batch_concurrency,
        settings.device,
        settings.clm_parallel_with_local_models,
    )

    first = await decisions.model_catalog()
    provider.model_catalog = ProviderResponse(
        200,
        {"models": [{"name": "new-model", "description": "New", "release_date": "2026-10-02"}]},
    )
    second = await decisions.model_catalog()

    assert [model["name"] for model in first["models"]] == ["jev-latest", "jev-preview"]
    assert [model["name"] for model in second["models"]] == ["new-model"]
    assert provider.catalog_calls == 2


def test_hosted_jev_call_does_not_switch_a_loaded_local_provider(
    provider_pair: dict[str, FakeProvider],
) -> None:
    settings = Settings(typesafe_api_key=SecretStr("upstream-secret"))
    supervisor = LifecycleSupervisor()
    providers = {
        **provider_pair,
        JEV_PROVIDER_NAME: FakeProvider(native_system_one_response("jev-latest")),
    }
    app = create_embedded_app(settings, providers, supervisor)

    with app_client(app) as client:
        local_response = client.post("/v1/systemone", json=system_one_request("laya"))
        hosted_response = client.post("/v1/systemone", json=system_one_request("jev-latest"))

    assert local_response.status_code == 200
    assert hosted_response.status_code == 200
    assert supervisor.acquired_providers == [LAYA_PROVIDER_NAME]
    assert supervisor.loaded_providers == {LAYA_PROVIDER_NAME}
    assert len(providers[JEV_PROVIDER_NAME].calls) == 1


def test_hosted_jev_never_reaches_typesafe_without_valid_caller_authentication() -> None:
    settings = Settings(
        laya_enabled=False,
        von_enabled=False,
        api_key=_operator_api_key,
        typesafe_api_key=SecretStr("upstream-secret"),
    )
    provider = FakeProvider(native_system_one_response("jev-latest"))
    app = create_embedded_app(
        settings, {JEV_PROVIDER_NAME: provider}, LifecycleSupervisor(provider_names=())
    )

    with app_client(app) as client:
        missing = client.post("/v1/systemone", json=system_one_request("jev-latest"))
        wrong = client.post(
            "/v1/systemone",
            json=system_one_request("jev-latest"),
            headers={"Authorization": "Bearer wrong-token"},
        )
        assert provider.calls == []
        valid = client.post(
            "/v1/systemone",
            json=system_one_request("jev-latest"),
            headers=_operator_authorization,
        )

    assert missing.status_code == 401
    assert wrong.status_code == 401
    assert valid.status_code == 200
    assert len(provider.calls) == 1


@pytest.mark.parametrize("status_code", [401, 403, 429, 500])
def test_jev_upstream_failures_return_safe_unavailable_envelope(status_code: int) -> None:
    settings = Settings(typesafe_api_key=SecretStr("upstream-secret"))
    provider = FakeProvider(
        ProviderResponse(status_code, {"detail": "private upstream account information"})
    )
    app = create_embedded_app(settings, {JEV_PROVIDER_NAME: provider}, LifecycleSupervisor())

    with app_client(app) as client:
        response = client.post("/v1/systemone", json=system_one_request("jev-latest"))

    assert response.status_code == 503
    assert response.json()["code"] == "PROVIDER_UNAVAILABLE"
    response_body = json.dumps(response.json())
    assert "private upstream" not in response_body
    assert "upstream-secret" not in response_body


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
