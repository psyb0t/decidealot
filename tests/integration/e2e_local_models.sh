#!/bin/bash
set -euo pipefail

LOG_FILE="${LOG_FILE:-/tmp/decidealot-real-models.log}"

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
exec > >(tee -a "$LOG_FILE") 2>&1

script_directory="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly script_directory
workspace_directory="$(cd -- "$script_directory/../.." && pwd -P)"
readonly workspace_directory
readonly healthcheck_attempts=900
readonly healthcheck_interval_seconds=2
readonly cpu_image="${DECIDEALOT_TEST_CPU_IMAGE:-psyb0t/decidealot:local}"
readonly cuda_image="${DECIDEALOT_TEST_CUDA_IMAGE:-psyb0t/decidealot:local-cuda}"
readonly runtime_uid="${DECIDEALOT_TEST_UID:-$(id -u)}"
readonly runtime_gid="${DECIDEALOT_TEST_GID:-$(id -g)}"

usage() {
	printf 'usage: %s [--cuda]\n' "${0##*/}" >&2
}

device="cpu"
image="$cpu_image"
case "$#:${1:-}" in
0:) ;;
1:--cuda)
	device="cuda"
	image="$cuda_image"
	;;
*)
	usage
	exit 2
	;;
esac

command -v docker >/dev/null 2>&1 || {
	log ERROR "docker is not on PATH"
	exit 2
}
command -v curl >/dev/null 2>&1 || {
	log ERROR "curl is not on PATH"
	exit 2
}
command -v python3 >/dev/null 2>&1 || {
	log ERROR "python3 is not on PATH"
	exit 2
}

if [[ "$device" == "cuda" ]]; then
	docker info --format '{{json .Runtimes}}' | grep --quiet '"nvidia"' || {
		log ERROR "Docker has no NVIDIA runtime required for --gpus all"
		exit 2
	}
fi

mkdir --parents "$workspace_directory/.testing"
model_directory=$(mktemp -d "$workspace_directory/.testing/decidealot-real-models-$device-XXXXXX")
chmod 0777 "$model_directory"
container_name="decidealot-real-$device-$$-$RANDOM"
port=$(python3 -c 'import socket; socket_instance = socket.socket(); socket_instance.bind(("127.0.0.1", 0)); print(socket_instance.getsockname()[1]); socket_instance.close()')
base_url="http://127.0.0.1:$port"

cleanup() {
	local exit_code=$?
	trap - EXIT
	if [[ -n "${started_container:-}" ]]; then
		if ! docker rm --force "$container_name" >/dev/null; then
			log WARN "could not remove owned test container name=$container_name"
			exit_code=1
		fi
	fi
	if [[ "$model_directory" == "$workspace_directory"/.testing/decidealot-real-models-* ]]; then
		if ! docker run --rm --user root --entrypoint /bin/sh \
			--mount "type=bind,source=$model_directory,target=/models" \
			"$image" -ceu 'find /models -mindepth 1 -maxdepth 1 -exec rm --recursive --force -- {} +'; then
			log ERROR "could not clear test model directory path=$model_directory"
			exit_code=1
		fi
		if ! rmdir -- "$model_directory"; then
			log ERROR "could not remove test model directory path=$model_directory"
			exit_code=1
		fi
	fi
	exit "$exit_code"
}
trap cleanup EXIT

log INFO "starting real local model service image=$image device=$device"
docker_args=(
	--detach
	--rm
	--init
	--name "$container_name"
	--user "$runtime_uid:$runtime_gid"
	--publish "127.0.0.1:$port:8080"
	--read-only
	--cap-drop ALL
	--security-opt no-new-privileges:true
	--pids-limit 512
	--tmpfs "/tmp:rw,noexec,nosuid,size=128m"
	--tmpfs "/var/run:rw,noexec,nosuid,size=8m"
	--tmpfs "/var/cache:rw,exec,nosuid,nodev,size=512m,uid=$runtime_uid,gid=$runtime_gid,mode=0755"
	--mount "type=bind,source=$model_directory,target=/models"
)
if [[ "$device" == "cuda" ]]; then
	docker_args+=(--gpus all)
fi
docker run "${docker_args[@]}" "$image" >/dev/null
started_container=1

healthy=""
for _ in $(seq 1 "$healthcheck_attempts"); do
	if curl --fail --silent --max-time 3 "$base_url/health" >/dev/null; then
		healthy=1
		break
	fi
	if [[ "$(docker inspect --format '{{.State.Running}}' "$container_name")" != "true" ]]; then
		docker logs "$container_name" >&2
		log ERROR "real local model service stopped before becoming ready"
		exit 1
	fi
	sleep "$healthcheck_interval_seconds"
done

if [[ -z "$healthy" ]]; then
	docker logs "$container_name" >&2
	log ERROR "real local model service did not become ready"
	exit 1
fi

for provider_name in laya von; do
	if [[ -z "$(find "$model_directory/$provider_name" -type f -print -quit)" ]]; then
		log ERROR "startup did not download provider bundle provider=$provider_name"
		exit 1
	fi
done

log INFO "checking HTTP model listing and real decisions device=$device"
if ! DECIDEALOT_BASE_URL="$base_url" python3 "$script_directory/real_models_client.py"; then
	docker logs "$container_name" >&2
	log ERROR "real local model decision check failed device=$device"
	exit 1
fi
log INFO "real local model responses passed image=$image device=$device"
