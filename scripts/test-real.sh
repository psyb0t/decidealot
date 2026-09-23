#!/bin/bash
set -euo pipefail
trap 'log ERROR "command failed exit=$?"' ERR

LOG_FILE="${LOG_FILE:-/tmp/decidealot-real.log}"
exec > >(tee -a "$LOG_FILE") 2>&1

log() {
	local level="$1"
	shift
	local timestamp file line function_name message
	timestamp=$(date -u '+%Y-%m-%dT%H:%M:%SZ')
	file="${BASH_SOURCE[1]##*/}"
	line="${BASH_LINENO[0]}"
	function_name="${FUNCNAME[1]:-main}"
	message="$*"
	printf '{"time":"%s","level":"%s","file":"%s","line":%s,"func":"%s","msg":"%s"}\n' \
		"$timestamp" "$level" "$file" "$line" "$function_name" "$message" >&2
}

image="psyb0t/decidealot:local"
device="cpu"
volume_cleanup_attempts=15
volume_cleanup_poll_seconds=1

usage() {
	printf 'usage: %s [--cuda]\n' "${0##*/}" >&2
}

case "$#:${1:-}" in
0:) ;;
1:--cuda)
	image="psyb0t/decidealot:local-cuda"
	device="cuda"
	;;
*)
	usage
	exit 2
	;;
esac

container_name="decidealot-real-$device-$RANDOM-$RANDOM"
volume_name="decidealot-real-$device-$RANDOM-$RANDOM"

cleanup() {
	local exit_code=$?
	local volume_removed=0
	trap - EXIT
	if [[ -n "${started_container:-}" ]]; then
		docker stop "$container_name" >/dev/null || log WARN "could not stop test container"
	fi
	if [[ -n "${created_volume:-}" ]]; then
		for _ in $(seq 1 "$volume_cleanup_attempts"); do
			# Docker releases an --rm container asynchronously after docker stop.
			if docker volume rm "$volume_name" >/dev/null 2>&1; then
				volume_removed=1
				break
			fi
			sleep "$volume_cleanup_poll_seconds"
		done
		if [[ "$volume_removed" != "1" ]]; then
			log WARN "could not remove test model volume"
		fi
	fi
	exit "$exit_code"
}
trap cleanup EXIT

docker volume create "$volume_name" >/dev/null
created_volume=1
log INFO "starting actual local model service image=$image device=$device"
if [[ "$device" == "cuda" ]]; then
	docker run --detach --rm --init --name "$container_name" --gpus all \
		--mount "type=volume,source=$volume_name,target=/models" \
		"$image" >/dev/null
else
	docker run --detach --rm --init --name "$container_name" \
		--mount "type=volume,source=$volume_name,target=/models" \
		"$image" >/dev/null
fi
started_container=1

for _ in $(seq 1 180); do
	health_status=$(docker inspect --format '{{.State.Health.Status}}' "$container_name")
	if [[ "$health_status" == "healthy" ]]; then
		break
	fi
	if [[ "$health_status" == "unhealthy" ]]; then
		docker logs "$container_name" >&2
		log ERROR "actual local model service became unhealthy"
		exit 1
	fi
	sleep 2
done

if [[ "${health_status:-}" != "healthy" ]]; then
	docker logs "$container_name" >&2
	log ERROR "actual local model service did not become healthy"
	exit 1
fi

log INFO "checking the official model list and one mixed decision per local model"
docker exec "$container_name" python -c '
import json
import math
import re
import urllib.error
import urllib.request
from http import HTTPStatus

base_url = "http://127.0.0.1:8080"
listing_timeout_seconds = 30
model_request_timeout_seconds = 240
probability_tolerance = 1e-6
release_date_pattern = re.compile(r"^\d{4}-\d{2}-\d{2}$")
tested_models = ("laya", "von")
listing_fields = {"models"}
model_entry_fields = {"name", "description", "release_date"}
response_fields = {"model", "answers", "usage"}
usage_fields = {"input_tokens", "output_tokens"}
answer_fields = {
    "noul": {"type", "noul"},
    "choice": {"type", "choice", "confidence", "probabilities"},
    "score": {"type", "score", "confidence", "legend", "probabilities"},
}
native_only_fields = {"routing", "action"}
state = "A proposed action would permanently delete protected data."
choice_criteria = {
    "allow": "The operation is reversible and does not affect protected data.",
    "require_review": "The operation is irreversible or affects protected data.",
}
score_levels = [
    "No risk to stored data.",
    "Some recoverable risk to stored data.",
    "Permanent loss of protected data.",
]
score_keys = {str(level) for level in range(len(score_levels))}
questions = {
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
        "criteria": choice_criteria,
    },
    "risk": {
        "type": "score",
        "instructions": "How much risk does the proposed action pose to stored data?",
        "criteria": score_levels,
    },
}


def fail(message):
    raise SystemExit(message)


def request_json(method, path, timeout_seconds, body=None):
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(
        base_url + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            status = response.status
            payload = json.load(response)
    except urllib.error.HTTPError as error:
        detail = error.read().decode(errors="replace")
        fail(f"{method} {path} returned HTTP {error.code}: {detail}")
    if status != HTTPStatus.OK:
        fail(f"{method} {path} returned HTTP {status}: {payload!r}")
    return payload


def require_exact_keys(label, value, expected):
    if not isinstance(value, dict):
        fail(f"{label} is not an object: {value!r}")
    if set(value) != expected:
        fail(f"{label} fields are {sorted(value)}, expected {sorted(expected)}")


def require_number(label, value):
    is_number = isinstance(value, (int, float)) and not isinstance(value, bool)
    if not is_number or not math.isfinite(value):
        fail(f"{label} is not a finite number: {value!r}")


def require_probability(label, value):
    require_number(label, value)
    if not -probability_tolerance <= value <= 1 + probability_tolerance:
        fail(f"{label} is outside 0..1: {value!r}")


def require_probabilities(label, probabilities, expected_keys):
    require_exact_keys(label, probabilities, expected_keys)
    for key, value in probabilities.items():
        require_probability(f"{label}.{key}", value)


def native_field_paths(value, path):
    found = []
    if isinstance(value, dict):
        for key, child in value.items():
            if key in native_only_fields:
                found.append(f"{path}.{key}")
            found.extend(native_field_paths(child, f"{path}.{key}"))
    if isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(native_field_paths(child, f"{path}[{index}]"))
    return found


def check_model_listing():
    listing = request_json("GET", "/v1/models", listing_timeout_seconds)
    require_exact_keys("GET /v1/models", listing, listing_fields)
    entries = listing["models"]
    if not isinstance(entries, list) or not entries:
        fail(f"GET /v1/models returned no model entries: {listing!r}")
    for index, entry in enumerate(entries):
        label = f"models[{index}]"
        require_exact_keys(label, entry, model_entry_fields)
        for field in sorted(model_entry_fields):
            if not isinstance(entry[field], str) or not entry[field]:
                fail(f"{label}.{field} is not a non-empty string: {entry!r}")
        if not release_date_pattern.fullmatch(entry["release_date"]):
            fail(f"{label}.release_date is not YYYY-MM-DD: {entry!r}")
    names = [entry["name"] for entry in entries]
    if len(names) != len(set(names)):
        fail(f"GET /v1/models repeats a model name: {names}")
    missing_names = sorted(set(tested_models) - set(names))
    if missing_names:
        fail(f"GET /v1/models does not list {missing_names}")
    print(json.dumps({"check": "models", "count": len(names)}), flush=True)


def check_answers(model, answers):
    require_exact_keys(f"{model} answers", answers, set(questions))
    for name, question in questions.items():
        answer = answers[name]
        label = f"{model} answers.{name}"
        expected_type = question["type"]
        require_exact_keys(label, answer, answer_fields[expected_type])
        actual_type = answer["type"]
        if actual_type != expected_type:
            fail(f"{label}.type is {actual_type!r}, expected {expected_type!r}")

    require_probability(f"{model} answers.destructive.noul", answers["destructive"]["noul"])

    handling = answers["handling"]
    if handling["choice"] not in choice_criteria:
        fail(f"{model} answers.handling.choice is unknown: {handling!r}")
    require_number(f"{model} answers.handling.confidence", handling["confidence"])
    require_probabilities(
        f"{model} answers.handling.probabilities",
        handling["probabilities"],
        set(choice_criteria),
    )

    risk = answers["risk"]
    highest_level = len(score_levels) - 1
    require_number(f"{model} answers.risk.score", risk["score"])
    if not -probability_tolerance <= risk["score"] <= highest_level + probability_tolerance:
        fail(f"{model} answers.risk.score is outside 0..{highest_level}: {risk!r}")
    require_number(f"{model} answers.risk.confidence", risk["confidence"])
    require_exact_keys(f"{model} answers.risk.legend", risk["legend"], score_keys)
    require_probabilities(f"{model} answers.risk.probabilities", risk["probabilities"], score_keys)


def check_system_one(model):
    body = {"model": model, "state": state, "questions": questions}
    payload = request_json("POST", "/v1/systemone", model_request_timeout_seconds, body)
    require_exact_keys(f"{model} response", payload, response_fields)
    leaked_fields = native_field_paths(payload, model)
    if leaked_fields:
        fail(f"{model} response leaks native fields: {leaked_fields}")
    if not isinstance(payload["model"], str) or not payload["model"]:
        fail(f"{model} response model is not a non-empty string: {payload!r}")
    usage = payload["usage"]
    require_exact_keys(f"{model} usage", usage, usage_fields)
    for field in sorted(usage_fields):
        if not isinstance(usage[field], int) or isinstance(usage[field], bool):
            fail(f"{model} usage.{field} is not an integer: {usage!r}")
    check_answers(model, payload["answers"])
    print(json.dumps({"check": "systemone", "requested_model": model, **payload}), flush=True)


check_model_listing()
for tested_model in tested_models:
    check_system_one(tested_model)
'
log INFO "actual local model responses passed image=$image device=$device"
