"""Black-box checks that the public boundary behaves like the official TypeSafe API."""

from collections.abc import Iterator
from typing import Any, cast

import pytest
from pydantic import SecretStr

from decidealot.app import create_embedded_app
from decidealot.constants import LAYA_PROVIDER_NAME, VON_PROVIDER_NAME
from decidealot.providers import ProviderResponse
from decidealot.settings import Settings
from tests.conftest import (
    FakeProvider,
    HTTPClient,
    HTTPResponse,
    LifecycleSupervisor,
    app_client,
    native_system_one_response,
    system_one_request,
)

_systemone_path = "/v1/systemone"
_models_path = "/v1/models"
_operator_api_key = SecretStr("operator-secret")
_operator_authorization = {"Authorization": "Bearer operator-secret"}
_laya_release_date = "2026-09-23"
_von_release_date = "2026-09-22"
_laya_selectors = (
    "laya",
    "laya-auto",
    "laya-latest",
    "laya-english",
    "laya-multilingual",
    "laya-typed-decisions",
)
_von_selectors = ("von", "von-latest", "von-1.1", "von-1.1.0")
_model_metadata_fields = {"name", "description", "release_date"}


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


@pytest.fixture
def authenticated_client(provider_pair: dict[str, FakeProvider]) -> Iterator[HTTPClient]:
    app = create_embedded_app(Settings(api_key=_operator_api_key), provider_pair)
    with app_client(app) as test_client:
        yield test_client


def forwarded_questions(provider: FakeProvider) -> dict[str, Any]:
    payload = provider.calls[0][0]
    return cast(dict[str, Any], payload["questions"])


def reported_detail(response: HTTPResponse) -> list[dict[str, Any]]:
    body = cast(dict[str, Any], response.json())
    return cast(list[dict[str, Any]], body["detail"])


def test_models_returns_the_official_model_metadata_list(client: HTTPClient) -> None:
    response = client.get(_models_path)

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"models"}
    assert [entry["name"] for entry in body["models"]] == [
        *_laya_selectors,
        *_von_selectors,
    ]
    for entry in body["models"]:
        assert set(entry) == _model_metadata_fields
        assert isinstance(entry["description"], str)
        assert entry["description"]
    release_dates = {entry["name"]: entry["release_date"] for entry in body["models"]}
    assert {release_dates[name] for name in _laya_selectors} == {_laya_release_date}
    assert {release_dates[name] for name in _von_selectors} == {_von_release_date}


def test_every_listed_model_name_is_accepted_by_system_one(client: HTTPClient) -> None:
    listed_names = [entry["name"] for entry in client.get(_models_path).json()["models"]]

    statuses = {
        name: client.post(_systemone_path, json=system_one_request(name)).status_code
        for name in listed_names
    }

    assert statuses == dict.fromkeys(listed_names, 200)


@pytest.mark.parametrize(
    ("authorization", "expected_status"),
    [(None, 401), ("Basic wrong", 401), ("Bearer wrong", 401), ("Bearer operator-secret", 200)],
)
@pytest.mark.parametrize("path", [_systemone_path, _models_path])
def test_both_operations_enforce_the_configured_bearer_token(
    authenticated_client: HTTPClient,
    provider_pair: dict[str, FakeProvider],
    path: str,
    authorization: str | None,
    expected_status: int,
) -> None:
    headers = {} if authorization is None else {"Authorization": authorization}

    if path == _models_path:
        response = authenticated_client.get(path, headers=headers)
    else:
        response = authenticated_client.post(path, headers=headers, json=system_one_request())

    assert response.status_code == expected_status
    if expected_status == 401:
        assert response.headers["WWW-Authenticate"] == "Bearer"
        assert provider_pair[LAYA_PROVIDER_NAME].calls == []


def test_unauthenticated_system_one_is_rejected_before_body_validation(
    authenticated_client: HTTPClient,
) -> None:
    response = authenticated_client.post(_systemone_path, json={"questions": {}})

    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHORIZED"


def test_both_operations_are_open_when_no_api_key_is_configured(client: HTTPClient) -> None:
    models_response = client.get(_models_path)
    system_one_response = client.post(_systemone_path, json=system_one_request())

    assert models_response.status_code == 200
    assert system_one_response.status_code == 200


@pytest.mark.parametrize(
    ("body", "expected_location"),
    [
        ({}, ["body", "model"]),
        ({"model": "laya", "questions": {"a": {"type": "noul"}}}, ["body", "state"]),
        ({"model": "laya", "state": "s"}, ["body", "questions"]),
        ({"model": "laya", "state": "s", "questions": {}}, ["body", "questions"]),
        (
            {"model": "laya", "state": "s", "questions": {"a": {"type": "bogus"}}},
            ["body", "questions", "a"],
        ),
        (
            {
                "model": "laya",
                "state": "s",
                "questions": {"a": {"type": "score", "criteria": []}},
            },
            ["body", "questions", "a", "score", "criteria"],
        ),
        (
            {"model": "laya", "state": "s", "questions": {"a": {"type": "choice"}}},
            ["body", "questions", "a", "choice", "criteria"],
        ),
        ({"model": "not-a-model", "state": "s", "questions": {"a": {"type": "noul"}}}, None),
        ({"model": "jev", "state": "s", "questions": {"a": {"type": "noul"}}}, None),
        (
            {"model": "jev-latest", "state": "s", "questions": {"a": {"type": "noul"}}},
            None,
        ),
    ],
)
def test_malformed_bodies_return_the_official_validation_envelope(
    client: HTTPClient,
    provider_pair: dict[str, FakeProvider],
    body: dict[str, object],
    expected_location: list[str] | None,
) -> None:
    response = client.post(_systemone_path, json=body)

    assert response.status_code == 422
    detail = reported_detail(response)
    assert detail
    for entry in detail:
        assert isinstance(entry["loc"], list)
        assert isinstance(entry["msg"], str)
        assert isinstance(entry["type"], str)
    if expected_location is not None:
        assert expected_location in [entry["loc"] for entry in detail]
    assert provider_pair[LAYA_PROVIDER_NAME].calls == []


def test_unknown_model_reports_the_rejected_selector(
    client: HTTPClient,
    provider_pair: dict[str, FakeProvider],
) -> None:
    response = client.post(_systemone_path, json=system_one_request("gpt-9"))

    assert response.status_code == 422
    entry = reported_detail(response)[0]
    assert entry["loc"] == ["body", "model"]
    assert entry["input"] == "gpt-9"
    assert "gpt-9" in entry["msg"]
    assert provider_pair[LAYA_PROVIDER_NAME].calls == []
    assert provider_pair[VON_PROVIDER_NAME].calls == []


@pytest.mark.parametrize("body", ["a bare string", [1, 2, 3], 7, None])
def test_non_object_bodies_return_the_official_validation_envelope(
    client: HTTPClient, body: object
) -> None:
    response = client.post(_systemone_path, json=body)

    assert response.status_code == 422
    assert reported_detail(response)


def test_malformed_json_returns_the_official_validation_envelope(client: HTTPClient) -> None:
    response = client.post(
        _systemone_path, content="{not json", headers={"Content-Type": "application/json"}
    )

    assert response.status_code == 422
    assert reported_detail(response)[0]["type"] == "json_invalid"


def test_missing_body_returns_the_official_validation_envelope(client: HTTPClient) -> None:
    response = client.post(_systemone_path)

    assert response.status_code == 422
    assert reported_detail(response)[0]["loc"] == ["body"]


@pytest.mark.parametrize("state", ["plain text", {"subject": "x", "body": "y"}, ["turn", "turn"]])
def test_every_documented_state_shape_reaches_the_provider(
    client: HTTPClient,
    provider_pair: dict[str, FakeProvider],
    state: object,
) -> None:
    body = {**system_one_request(), "state": state}

    response = client.post(_systemone_path, json=body)

    assert response.status_code == 200
    assert provider_pair[LAYA_PROVIDER_NAME].calls[0][0]["state"] == state


def test_noul_questions_are_adapted_for_the_local_providers(
    client: HTTPClient,
    provider_pair: dict[str, FakeProvider],
) -> None:
    body = {
        "model": "laya",
        "state": "s",
        "questions": {
            "bare": {"type": "noul"},
            "described": {
                "type": "noul",
                "instructions": {"task": "Identify unsolicited advertising."},
                "criteria": {"true": {"rule": "unsolicited"}, "false": None},
            },
        },
    }

    response = client.post(_systemone_path, json=body)

    assert response.status_code == 200
    assert forwarded_questions(provider_pair[LAYA_PROVIDER_NAME]) == {
        "bare": {"type": "noul", "instructions": "", "criteria": None},
        "described": {
            "type": "noul",
            "instructions": '{"task": "Identify unsolicited advertising."}',
            "criteria": {"true": '{"rule": "unsolicited"}'},
        },
    }


def test_choice_questions_keep_option_names_and_render_nested_values(
    client: HTTPClient,
    provider_pair: dict[str, FakeProvider],
) -> None:
    body = {
        "model": "von",
        "state": "s",
        "questions": {
            "tone": {
                "type": "choice",
                "instructions": ["What is the tone?", "Pick one."],
                "criteria": {
                    "angry": {"when": "hostile", "examples": ["shouting"]},
                    "calm": "A neutral message.",
                    "unknown": None,
                },
            }
        },
    }

    response = client.post(_systemone_path, json=body)

    assert response.status_code == 200
    assert forwarded_questions(provider_pair[VON_PROVIDER_NAME]) == {
        "tone": {
            "type": "choice",
            "instructions": '["What is the tone?", "Pick one."]',
            "criteria": {
                "angry": '{"when": "hostile", "examples": ["shouting"]}',
                "calm": "A neutral message.",
                "unknown": None,
            },
        }
    }


def test_score_questions_keep_level_order_and_render_nested_levels(
    client: HTTPClient,
    provider_pair: dict[str, FakeProvider],
) -> None:
    body = {
        "model": "laya",
        "state": "s",
        "questions": {
            "urgency": {
                "type": "score",
                "criteria": ["Can wait", {"level": "today"}, ["escalate", "page"]],
            }
        },
    }

    response = client.post(_systemone_path, json=body)

    assert response.status_code == 200
    assert forwarded_questions(provider_pair[LAYA_PROVIDER_NAME]) == {
        "urgency": {
            "type": "score",
            "instructions": "",
            "criteria": ["Can wait", '{"level": "today"}', '["escalate", "page"]'],
        }
    }


def test_nested_option_values_keep_non_ascii_characters(
    client: HTTPClient,
    provider_pair: dict[str, FakeProvider],
) -> None:
    body = {
        "model": "laya",
        "state": "Mein Konto wurde zweimal belastet",
        "questions": {"billing": {"type": "noul", "criteria": {"true": {"de": "Rückerstattung"}}}},
    }

    response = client.post(_systemone_path, json=body)

    assert response.status_code == 200
    criteria = forwarded_questions(provider_pair[LAYA_PROVIDER_NAME])["billing"]["criteria"]
    assert criteria == {"true": '{"de": "Rückerstattung"}'}


def test_successful_responses_are_projected_onto_the_official_schema(
    client: HTTPClient,
    provider_pair: dict[str, FakeProvider],
) -> None:
    provider_pair[LAYA_PROVIDER_NAME].response = ProviderResponse(
        status_code=200,
        body={
            "model": "laya-rl-agent",
            "answers": {
                "spam": {"type": "noul", "noul": 0.98, "confidence": 0.98, "action": {"p": 0.1}},
                "tone": {
                    "type": "choice",
                    "choice": "angry",
                    "probabilities": {"angry": 0.8, "calm": 0.2},
                    "confidence": 0.9,
                    "action": {"p": 0.2},
                },
                "urgency": {
                    "type": "score",
                    "score": 1.7,
                    "legend": {"0": "Can wait", "1": "Today"},
                    "probabilities": {"0": 0.3, "1": 0.7},
                    "confidence": 0.85,
                    "action": {"p": 0.3},
                },
            },
            "usage": {"input_tokens": 120, "output_tokens": 3},
            "routing": {"model": "english", "reason": "English Latin text"},
        },
    )

    response = client.post(_systemone_path, json=system_one_request("laya"))

    assert response.status_code == 200
    assert response.json() == {
        "model": "laya",
        "answers": {
            "spam": {"type": "noul", "noul": 0.98},
            "tone": {
                "type": "choice",
                "choice": "angry",
                "confidence": 0.9,
                "probabilities": {"angry": 0.8, "calm": 0.2},
            },
            "urgency": {
                "type": "score",
                "score": 1.7,
                "confidence": 0.85,
                "legend": {"0": "Can wait", "1": "Today"},
                "probabilities": {"0": 0.3, "1": 0.7},
            },
        },
        "usage": {"input_tokens": 120, "output_tokens": 3},
    }


def test_a_provider_result_that_is_not_official_becomes_unavailable(
    client: HTTPClient,
    provider_pair: dict[str, FakeProvider],
) -> None:
    provider_pair[LAYA_PROVIDER_NAME].response = ProviderResponse(
        status_code=200, body={"answers": {"route": {"value": "allow"}}}
    )

    response = client.post(_systemone_path, json=system_one_request("laya"))

    assert response.status_code == 503
    assert response.json()["code"] == "PROVIDER_UNAVAILABLE"


def test_official_provider_validation_bodies_are_preserved(
    client: HTTPClient,
    provider_pair: dict[str, FakeProvider],
) -> None:
    provider_body = {
        "detail": [
            {
                "loc": ["body", "questions", "urgency", "score", "criteria"],
                "msg": "List should have at least 1 item after validation, not 0",
                "type": "too_short",
                "input": [],
                "ctx": {"min_length": 1},
            }
        ]
    }
    provider_pair[LAYA_PROVIDER_NAME].response = ProviderResponse(
        status_code=422, body=provider_body
    )

    response = client.post(_systemone_path, json=system_one_request("laya"))

    assert response.status_code == 422
    assert response.json() == provider_body


def test_bare_provider_validation_messages_are_restated_officially(
    client: HTTPClient,
    provider_pair: dict[str, FakeProvider],
) -> None:
    provider_pair[LAYA_PROVIDER_NAME].response = ProviderResponse(
        status_code=422,
        body={"detail": "question 'urgency' options exceed head_max_len=192"},
    )

    response = client.post(_systemone_path, json=system_one_request("laya"))

    assert response.status_code == 422
    detail = reported_detail(response)
    assert detail[0]["loc"] == ["body", "questions"]
    assert detail[0]["type"] == "value_error"
    assert "head_max_len" in detail[0]["msg"]


def test_unreadable_provider_validation_bodies_are_restated_officially(
    client: HTTPClient,
    provider_pair: dict[str, FakeProvider],
) -> None:
    provider_pair[LAYA_PROVIDER_NAME].response = ProviderResponse(status_code=422, body=["nope"])

    response = client.post(_systemone_path, json=system_one_request("laya"))

    assert response.status_code == 422
    detail = reported_detail(response)
    assert detail[0]["loc"] == ["body", "questions"]
    assert detail[0]["type"] == "value_error"


def test_unknown_request_fields_never_reach_a_provider(
    client: HTTPClient,
    provider_pair: dict[str, FakeProvider],
) -> None:
    body = {**system_one_request("laya"), "temperature": 0.9, "endpoint": "http://evil.test"}

    response = client.post(_systemone_path, json=body)

    assert response.status_code == 200
    assert set(provider_pair[LAYA_PROVIDER_NAME].calls[0][0]) == {"model", "state", "questions"}


def test_a_non_object_provider_result_becomes_unavailable(
    client: HTTPClient,
    provider_pair: dict[str, FakeProvider],
) -> None:
    provider_pair[LAYA_PROVIDER_NAME].response = ProviderResponse(status_code=200, body="ok")

    response = client.post(_systemone_path, json=system_one_request("laya"))

    assert response.status_code == 503
    assert response.json()["code"] == "PROVIDER_UNAVAILABLE"


def test_structured_provider_validation_details_are_restated_officially(
    client: HTTPClient,
    provider_pair: dict[str, FakeProvider],
) -> None:
    provider_pair[LAYA_PROVIDER_NAME].response = ProviderResponse(
        status_code=422, body={"detail": {"message": "unsupported question"}}
    )

    response = client.post(_systemone_path, json=system_one_request("laya"))

    assert response.status_code == 422
    detail = reported_detail(response)
    assert detail[0]["loc"] == ["body", "questions"]
    assert detail[0]["type"] == "value_error"
    assert "unsupported question" not in detail[0]["msg"]


def test_system_one_reports_unavailable_before_the_lifecycle_is_ready(
    provider_pair: dict[str, FakeProvider],
) -> None:
    supervisor = LifecycleSupervisor(ready_when_started=False)
    app = create_embedded_app(Settings(), provider_pair, supervisor)

    with app_client(app) as client:
        response = client.post(_systemone_path, json=system_one_request("laya"))

    assert response.status_code == 503
    assert response.json()["code"] == "PROVIDER_UNAVAILABLE"
    assert provider_pair[LAYA_PROVIDER_NAME].calls == []


def test_an_unknown_model_is_rejected_before_the_lifecycle_is_ready(
    provider_pair: dict[str, FakeProvider],
) -> None:
    supervisor = LifecycleSupervisor(ready_when_started=False)
    app = create_embedded_app(Settings(), provider_pair, supervisor)

    with app_client(app) as client:
        response = client.post(_systemone_path, json=system_one_request("gpt-9"))

    assert response.status_code == 422
    assert reported_detail(response)[0]["loc"] == ["body", "model"]
