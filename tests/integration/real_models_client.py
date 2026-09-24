"""Exercise the published TypeSafe-compatible API against actual local model weights."""

from __future__ import annotations

import json
import math
import os
import re
from http import HTTPStatus
from http.client import HTTPConnection, HTTPException
from typing import Any, NoReturn, cast
from urllib.parse import urlsplit

LISTING_TIMEOUT_SECONDS = 30
MODEL_REQUEST_TIMEOUT_SECONDS = 240
PROBABILITY_TOLERANCE = 1e-6
LOOPBACK_HOST = "127.0.0.1"
HTTP_SCHEME = "http"
type JSONValue = str | int | float | bool | None | list[JSONValue] | dict[str, JSONValue]
RELEASE_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TESTED_MODELS = ("laya", "von")
LISTING_FIELDS = {"models"}
MODEL_ENTRY_FIELDS = {"name", "description", "release_date"}
RESPONSE_FIELDS = {"model", "answers", "usage"}
USAGE_FIELDS = {"input_tokens", "output_tokens"}
ANSWER_FIELDS: dict[str, set[str]] = {
    "noul": {"type", "noul"},
    "choice": {"type", "choice", "confidence", "probabilities"},
    "score": {"type", "score", "confidence", "legend", "probabilities"},
}
NATIVE_ONLY_FIELDS = {"routing", "action"}
STATE = "A proposed action would permanently delete protected data."
CHOICE_CRITERIA = {
    "allow": "The operation is reversible and does not affect protected data.",
    "require_review": "The operation is irreversible or affects protected data.",
}
SCORE_LEVELS = [
    "No risk to stored data.",
    "Some recoverable risk to stored data.",
    "Permanent loss of protected data.",
]
SCORE_KEYS = {str(level) for level in range(len(SCORE_LEVELS))}
QUESTIONS: dict[str, dict[str, Any]] = {
    "destructive": {
        "type": "noul",
        "instructions": "Does the proposed action permanently destroy data?",
        "criteria": {
            "true": "The action deletes data that cannot be recovered.",
            "false": "The action keeps all data recoverable.",
        },
    },
    "handling": {
        "type": "choice",
        "instructions": "Which handling is required for the proposed action?",
        "criteria": CHOICE_CRITERIA,
    },
    "risk": {
        "type": "score",
        "instructions": "How much risk does the proposed action pose to stored data?",
        "criteria": SCORE_LEVELS,
    },
}


def fail(message: str) -> NoReturn:
    raise SystemExit(message)


def request_json(
    base_url: str, method: str, path: str, timeout_seconds: int, body: object | None = None
) -> JSONValue:
    parsed_url = urlsplit(base_url)
    if (
        parsed_url.scheme != HTTP_SCHEME
        or parsed_url.hostname != LOOPBACK_HOST
        or parsed_url.username
        or parsed_url.password
        or parsed_url.path
        or parsed_url.query
        or parsed_url.fragment
    ):
        fail(f"DECIDEALOT_BASE_URL must be an HTTP loopback origin: {base_url!r}")
    try:
        port: int | None = parsed_url.port
    except ValueError as error:
        fail(f"DECIDEALOT_BASE_URL has an invalid port: {error}")
    if port is None:
        fail("DECIDEALOT_BASE_URL must include a port")

    data = None if body is None else json.dumps(body).encode()
    connection = HTTPConnection(LOOPBACK_HOST, port, timeout=timeout_seconds)
    status = int(HTTPStatus.INTERNAL_SERVER_ERROR)
    response_body = ""
    try:
        connection.request(method, path, body=data, headers={"Content-Type": "application/json"})
        response = connection.getresponse()
        status = response.status
        response_body = response.read().decode(errors="replace")
    except (HTTPException, OSError) as error:
        fail(f"{method} {path} request failed: {error}")
    finally:
        connection.close()
    try:
        payload = cast(JSONValue, json.loads(response_body))
    except json.JSONDecodeError as error:
        fail(f"{method} {path} returned invalid JSON: {error}")
    if status != HTTPStatus.OK:
        fail(f"{method} {path} returned HTTP {status}: {payload!r}")
    return payload


def require_exact_keys(label: str, value: JSONValue, expected: set[str]) -> dict[str, JSONValue]:
    if not isinstance(value, dict):
        fail(f"{label} is not an object: {value!r}")
    typed_value = value
    if set(typed_value) != expected:
        fail(f"{label} fields are {sorted(typed_value)}, expected {sorted(expected)}")
    return typed_value


def require_number(label: str, value: JSONValue) -> float | int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        fail(f"{label} is not a finite number: {value!r}")
    number = value
    if not math.isfinite(number):
        fail(f"{label} is not a finite number: {value!r}")
    return number


def require_probability(label: str, value: JSONValue) -> None:
    probability = require_number(label, value)
    if not -PROBABILITY_TOLERANCE <= probability <= 1 + PROBABILITY_TOLERANCE:
        fail(f"{label} is outside 0..1: {value!r}")


def require_probabilities(label: str, probabilities: JSONValue, expected_keys: set[str]) -> None:
    values = require_exact_keys(label, probabilities, expected_keys)
    for key, value in values.items():
        require_probability(f"{label}.{key}", value)


def native_field_paths(value: JSONValue, path: str) -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        typed_mapping = value
        for key, child in typed_mapping.items():
            if key in NATIVE_ONLY_FIELDS:
                found.append(f"{path}.{key}")
            found.extend(native_field_paths(child, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(native_field_paths(child, f"{path}[{index}]"))
    return found


def check_model_listing(base_url: str) -> None:
    listing = require_exact_keys(
        "GET /v1/models",
        request_json(base_url, "GET", "/v1/models", LISTING_TIMEOUT_SECONDS),
        LISTING_FIELDS,
    )
    entries = listing["models"]
    if not isinstance(entries, list) or not entries:
        fail(f"GET /v1/models returned no model entries: {listing!r}")
    names: list[str] = []
    for index, entry in enumerate(entries):
        label = f"models[{index}]"
        model_entry = require_exact_keys(label, entry, MODEL_ENTRY_FIELDS)
        for field in sorted(MODEL_ENTRY_FIELDS):
            value = model_entry[field]
            if not isinstance(value, str) or not value:
                fail(f"{label}.{field} is not a non-empty string: {entry!r}")
        release_date = model_entry["release_date"]
        if not isinstance(release_date, str):
            fail(f"{label}.release_date is not a string: {entry!r}")
        if not RELEASE_DATE_PATTERN.fullmatch(release_date):
            fail(f"{label}.release_date is not YYYY-MM-DD: {entry!r}")
        name = model_entry["name"]
        if not isinstance(name, str):
            fail(f"{label}.name is not a string: {entry!r}")
        names.append(name)
    if len(names) != len(set(names)):
        fail(f"GET /v1/models repeats a model name: {names}")
    missing_names = sorted(set(TESTED_MODELS) - set(names))
    if missing_names:
        fail(f"GET /v1/models does not list {missing_names}")
    print(json.dumps({"check": "models", "count": len(names)}), flush=True)


def check_answers(model: str, answers: JSONValue) -> None:
    answer_values = require_exact_keys(f"{model} answers", answers, set(QUESTIONS))
    for name, question in QUESTIONS.items():
        answer = require_exact_keys(
            f"{model} answers.{name}",
            answer_values[name],
            ANSWER_FIELDS[question["type"]],
        )
        if answer["type"] != question["type"]:
            fail(
                f"{model} answers.{name}.type is {answer['type']!r}, expected {question['type']!r}"
            )

    destructive = require_exact_keys(
        f"{model} answers.destructive",
        answer_values["destructive"],
        ANSWER_FIELDS["noul"],
    )
    require_probability(f"{model} answers.destructive.noul", destructive["noul"])

    handling = require_exact_keys(
        f"{model} answers.handling",
        answer_values["handling"],
        ANSWER_FIELDS["choice"],
    )
    if handling["choice"] not in CHOICE_CRITERIA:
        fail(f"{model} answers.handling.choice is unknown: {handling!r}")
    require_number(f"{model} answers.handling.confidence", handling["confidence"])
    require_probabilities(
        f"{model} answers.handling.probabilities", handling["probabilities"], set(CHOICE_CRITERIA)
    )

    risk = require_exact_keys(
        f"{model} answers.risk",
        answer_values["risk"],
        ANSWER_FIELDS["score"],
    )
    highest_level = len(SCORE_LEVELS) - 1
    score = require_number(f"{model} answers.risk.score", risk["score"])
    if not -PROBABILITY_TOLERANCE <= score <= highest_level + PROBABILITY_TOLERANCE:
        fail(f"{model} answers.risk.score is outside 0..{highest_level}: {risk!r}")
    require_number(f"{model} answers.risk.confidence", risk["confidence"])
    require_exact_keys(f"{model} answers.risk.legend", risk["legend"], SCORE_KEYS)
    require_probabilities(f"{model} answers.risk.probabilities", risk["probabilities"], SCORE_KEYS)


def check_system_one(base_url: str, model: str) -> None:
    body = {"model": model, "state": STATE, "questions": QUESTIONS}
    payload = require_exact_keys(
        f"{model} response",
        request_json(base_url, "POST", "/v1/systemone", MODEL_REQUEST_TIMEOUT_SECONDS, body),
        RESPONSE_FIELDS,
    )
    leaked_fields = native_field_paths(payload, model)
    if leaked_fields:
        fail(f"{model} response leaks native fields: {leaked_fields}")
    if not isinstance(payload["model"], str) or not payload["model"]:
        fail(f"{model} response model is not a non-empty string: {payload!r}")
    usage = require_exact_keys(f"{model} usage", payload["usage"], USAGE_FIELDS)
    for field in sorted(USAGE_FIELDS):
        value = usage[field]
        if not isinstance(value, int) or isinstance(value, bool):
            fail(f"{model} usage.{field} is not an integer: {usage!r}")
    check_answers(model, payload["answers"])
    print(json.dumps({"check": "systemone", "requested_model": model, **payload}), flush=True)


def main() -> None:
    base_url = os.environ.get("DECIDEALOT_BASE_URL")
    if not base_url:
        fail("DECIDEALOT_BASE_URL is required")
    check_model_listing(base_url)
    for model in TESTED_MODELS:
        check_system_one(base_url, model)


if __name__ == "__main__":
    main()
