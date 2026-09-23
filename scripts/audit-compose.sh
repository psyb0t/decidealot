#!/bin/bash
set -euo pipefail

readonly CPU_COMPOSE_FILE="docker-compose.yml"
readonly CUDA_COMPOSE_FILE="docker-compose.cuda.yml"
readonly SHARED_AUDIT_PATH="/home/bw/.codex/rig/scripts/audit-compose.sh"
readonly DEFAULT_LOG_FILE="/tmp/decidealot-audit-compose.log"

LOG_FILE="${LOG_FILE:-$DEFAULT_LOG_FILE}"
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

trap 'log ERROR "command failed exit=$?"' ERR

usage() {
	printf 'usage: %s [--cuda]\n' "${0##*/}" >&2
}

cuda=0
case "$#:${1:-}" in
0:) ;;
1:--cuda) cuda=1 ;;
*)
	usage
	exit 2
	;;
esac

repo_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
rendered_json=$(mktemp)

cleanup() {
	if ! rm --force "$rendered_json"; then
		log WARN "could not remove temporary rendered Compose file"
	fi
}
trap cleanup EXIT

compose_args=(-f "$CPU_COMPOSE_FILE")
if ((cuda == 1)); then
	compose_args+=(-f "$CUDA_COMPOSE_FILE")
fi

if [[ -n "${DEBUG:-}" ]]; then
	log DEBUG "rendering Compose configuration cuda=$cuda"
fi
log INFO "auditing resolved Compose configuration cuda=$cuda"
cd "$repo_dir"
docker compose "${compose_args[@]}" config --format json |
	jq '
		# Docker Compose v2 emits duration strings, while the shared auditor expects nanoseconds.
		def healthcheck_duration:
			if . == "10s" then 10000000000
			elif . == "3s" then 3000000000
			elif . == "30s" then 30000000000
			else .
			end;
		.services |= with_entries(
			if .value.healthcheck? then
				.value.healthcheck.interval |= healthcheck_duration |
				.value.healthcheck.timeout |= healthcheck_duration |
				.value.healthcheck.start_period |= healthcheck_duration
			else
				.
			end
		)
	' >"$rendered_json"
"$SHARED_AUDIT_PATH" --rendered-json "$rendered_json"
log INFO "Compose audit passed cuda=$cuda"
