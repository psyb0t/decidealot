"""Opt-in live TypeSafe contract check through Decidealot's public HTTP boundary."""

import os

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from decidealot.app import create_app
from decidealot.settings import Settings


@pytest.mark.real
def test_hosted_jev_lists_models_and_answers_one_decision() -> None:
    key = os.environ.get("DECIDEALOT_TYPESAFE_API_KEY")
    assert key, "DECIDEALOT_TYPESAFE_API_KEY is required"
    settings = Settings(
        laya_enabled=False,
        von_enabled=False,
        typesafe_api_key=SecretStr(key),
    )
    app = create_app(settings)

    with TestClient(app) as client:
        health = client.get("/health")
        models = client.get("/v1/models")
        assert models.status_code == 200
        available_models = models.json()["models"]
        assert available_models, "TypeSafe returned no models for this account"
        selected_model = available_models[0]["name"]
        decision = client.post(
            "/v1/systemone",
            json={
                "model": selected_model,
                "state": "A customer cannot sign in to their account.",
                "questions": {
                    "team": {
                        "type": "choice",
                        "instructions": "Which team should handle this request?",
                        "criteria": {
                            "account": "Login and account access",
                            "billing": "Invoices and payment issues",
                        },
                    }
                },
            },
        )

    assert health.status_code == 200
    assert health.json() == {"status": "ok", "providers": ["jev"]}
    assert models.status_code == 200
    assert decision.status_code == 200
    body = decision.json()
    assert isinstance(body["model"], str) and body["model"]
    assert body["answers"]["team"]["type"] == "choice"
    assert body["answers"]["team"]["choice"] in {"account", "billing"}
    assert 0 <= body["answers"]["team"]["confidence"] <= 1
    assert body["usage"]["input_tokens"] > 0
