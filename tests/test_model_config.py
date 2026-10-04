"""Model configuration is validated before any provider executes."""

from typing import Any

import pytest
from pydantic import SecretStr

from decidealot.app import create_embedded_app
from decidealot.settings import Settings
from tests.conftest import (
    FakeProvider,
    LifecycleSupervisor,
    app_client,
    native_system_one_response,
    system_one_request,
)


def _settings() -> Settings:
    return Settings(
        clm_enabled=True, clm_embeddings_url="https://encoder.example.test/v1/embeddings"
    )


@pytest.mark.parametrize("model", ["clm", "clm-latest", "clm-0.1-8b"])
@pytest.mark.parametrize("temperature", [0.8, 1, 100, 1e-300])
def test_clm_temperature_reaches_the_selected_provider(model: str, temperature: float) -> None:
    provider = FakeProvider(native_system_one_response("clm"))
    body = system_one_request(model)
    body["config"] = {"temperature": temperature}
    with app_client(
        create_embedded_app(_settings(), {"clm": provider}, LifecycleSupervisor())
    ) as client:
        response = client.post("/v1/systemone", json=body)
    assert response.status_code == 200
    assert provider.calls[0][0]["temperature"] == temperature
    assert "config" not in provider.calls[0][0]


@pytest.mark.parametrize(
    "config",
    [
        None,
        [],
        {"temperature": 0},
        {"temperature": -1},
        {"temperature": 101},
        {"temperature": True},
        {"temperature": "0.8"},
        {"url": "https://attacker.example.test"},
        {"temprature": 1},
    ],
)
def test_invalid_clm_config_is_rejected_without_provider_calls(config: Any) -> None:
    provider = FakeProvider(native_system_one_response("clm"))
    body = system_one_request("clm")
    body["config"] = config
    with app_client(
        create_embedded_app(_settings(), {"clm": provider}, LifecycleSupervisor())
    ) as client:
        response = client.post("/v1/systemone", json=body)
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"][:2] == ["body", "config"]
    assert provider.calls == []


def test_batch_validates_all_config_before_any_work() -> None:
    provider = FakeProvider(native_system_one_response("clm"))
    valid = system_one_request("clm")
    invalid = {**valid, "config": {"temperature": 0}}
    with app_client(
        create_embedded_app(_settings(), {"clm": provider}, LifecycleSupervisor())
    ) as client:
        response = client.post("/v1/systemone/batch", json={"requests": [valid, invalid]})
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"][:4] == ["body", "requests", 1, "config"]
    assert provider.calls == []


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity"])
def test_nonfinite_temperature_returns_validation_error_not_server_failure(value: str) -> None:
    import json

    provider = FakeProvider(native_system_one_response("clm"))
    body = (
        json.dumps(system_one_request("clm"))[:-1] + ', "config": {"temperature": ' + value + "}}"
    )
    with app_client(
        create_embedded_app(_settings(), {"clm": provider}, LifecycleSupervisor())
    ) as client:
        response = client.post(
            "/v1/systemone", content=body, headers={"Content-Type": "application/json"}
        )
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "config", "temperature"]
    assert provider.calls == []


@pytest.mark.parametrize("model", ["laya", "von"])
def test_other_providers_reject_unsupported_config(model: str) -> None:
    provider = FakeProvider(native_system_one_response(model))
    with app_client(
        create_embedded_app(Settings(), {model: provider}, LifecycleSupervisor())
    ) as client:
        response = client.post(
            "/v1/systemone", json={**system_one_request(model), "config": {"temperature": 1}}
        )
    assert response.status_code == 422
    assert provider.calls == []


def test_clm_keeps_nested_instruction_and_criterion_shapes() -> None:
    provider = FakeProvider(native_system_one_response("clm"))
    body = system_one_request("clm")
    question = {
        "type": "choice",
        "instructions": {"policy": ["Classify the subject"]},
        "criteria": {"yes": {"rule": True}, "no": None},
    }
    body["questions"] = {"review": question}
    with app_client(
        create_embedded_app(_settings(), {"clm": provider}, LifecycleSupervisor())
    ) as client:
        response = client.post("/v1/systemone", json=body)
    assert response.status_code == 200
    assert provider.calls[0][0]["questions"]["review"] == question


def test_batch_keeps_each_items_temperature() -> None:
    provider = FakeProvider(native_system_one_response("clm"))
    requests = [
        {**system_one_request("clm"), "config": {"temperature": value}} for value in (0.5, 2)
    ]
    with app_client(
        create_embedded_app(_settings(), {"clm": provider}, LifecycleSupervisor())
    ) as client:
        response = client.post("/v1/systemone/batch", json={"requests": requests})
    assert response.status_code == 200
    assert [call[0]["temperature"] for call in provider.calls] == [0.5, 2]


@pytest.mark.parametrize("config", [{}, {"temperature": 1}])
def test_hosted_config_is_not_forwarded(config: dict[str, Any]) -> None:
    provider = FakeProvider(native_system_one_response("jev-latest"))
    settings = Settings(
        laya_enabled=False, von_enabled=False, typesafe_api_key=SecretStr("EXAMPLE-DO-NOT-USE")
    )
    with app_client(
        create_embedded_app(settings, {"jev": provider}, LifecycleSupervisor(provider_names=()))
    ) as client:
        response = client.post(
            "/v1/systemone", json={**system_one_request("jev-latest"), "config": config}
        )
    assert response.status_code == (422 if config else 200)
    if config:
        assert provider.calls == []
    else:
        assert set(provider.calls[0][0]) == {"model", "state", "questions"}
