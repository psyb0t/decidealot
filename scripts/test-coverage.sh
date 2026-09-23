#!/bin/bash
set -euo pipefail

readonly log_file="${LOG_FILE:-/tmp/decidealot-test-coverage.log}"
readonly coverage_file="${COVERAGE_FILE:-coverage-percent.txt}"
readonly coverage_marker="not real"
readonly minimum_coverage_percent="${COVERAGE_MINIMUM:-90}"

log() {
	local level="$1"
	shift
	jq -nc \
		--arg time "$(date -u '+%Y-%m-%dT%H:%M:%S.%3NZ')" \
		--arg level "$level" \
		--arg file "${BASH_SOURCE[1]##*/}" \
		--argjson line "${BASH_LINENO[0]}" \
		--arg func "${FUNCNAME[1]:-main}" \
		--arg msg "$*" \
		'{time:$time,level:$level,file:$file,line:$line,func:$func,msg:$msg}' >&2
}

on_error() {
	local status=$?

	log ERROR "command failed exit=$status"
	exit "$status"
}

trap on_error ERR
exec > >(tee -a "$log_file") 2>&1

if ! [[ "$minimum_coverage_percent" =~ ^[0-9]+$ ]]; then
	log ERROR "COVERAGE_MINIMUM must be a whole percentage"
	exit 2
fi

if [[ -n "${DEBUG:-}" ]]; then
	log DEBUG "running coverage suite marker=$coverage_marker"
fi

python -m pytest -m "$coverage_marker" --cov --cov-report=term --cov-fail-under="$minimum_coverage_percent"
coverage_percent="$(python -m coverage report --format=total)"
printf '%s\n' "$coverage_percent" >"$coverage_file"
log INFO "coverage badge input wrote percent=$coverage_percent path=$coverage_file"
