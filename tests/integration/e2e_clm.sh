#!/bin/bash
set -euo pipefail

readonly LOG_FILE="${LOG_FILE:-/tmp/decidealot-real-clm.log}"
readonly HEALTHCHECK_ATTEMPTS=900
readonly HEALTHCHECK_INTERVAL_SECONDS=2
readonly CPU_IMAGE="${DECIDEALOT_TEST_CPU_IMAGE:-psyb0t/decidealot:local}"
readonly RUNTIME_UID="${DECIDEALOT_TEST_UID:-$(id -u)}"
readonly RUNTIME_GID="${DECIDEALOT_TEST_GID:-$(id -g)}"
readonly MOCK_HOSTNAME="clm-embeddings"
readonly MOCK_PORT=8081
readonly MOCK_MODEL="qwen3-8b"
readonly MOCK_API_KEY="EXAMPLE-DO-NOT-USE"

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
readonly mock_script="$script_directory/mock_embeddings_server.py"

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
[[ -f "$mock_script" ]] || {
	log ERROR "mock embeddings server is missing path=$mock_script"
	exit 2
}

mkdir --parents "$workspace_directory/.testing"
model_directory="$(mktemp -d "$workspace_directory/.testing/decidealot-real-clm-XXXXXX")"
chmod 0777 "$model_directory"
readonly model_directory
network_name="decidealot-real-clm-network-$$-$RANDOM"
readonly network_name
mock_container_name="decidealot-real-clm-embeddings-$$-$RANDOM"
readonly mock_container_name
service_container_name="decidealot-real-clm-service-$$-$RANDOM"
readonly service_container_name
port="$(python3 -c 'import socket; socket_instance = socket.socket(); socket_instance.bind(("127.0.0.1", 0)); print(socket_instance.getsockname()[1]); socket_instance.close()')"
readonly port
base_url="http://127.0.0.1:$port"
readonly base_url

cleanup() {
	local exit_code=$?
	trap - EXIT
	if [[ -n "${started_service:-}" ]] && docker inspect "$service_container_name" >/dev/null 2>&1; then
		docker rm --force "$service_container_name" >/dev/null || {
			log WARN "could not remove owned service container name=$service_container_name"
			exit_code=1
		}
	fi
	if [[ -n "${started_mock:-}" ]] && docker inspect "$mock_container_name" >/dev/null 2>&1; then
		docker rm --force "$mock_container_name" >/dev/null || {
			log WARN "could not remove owned mock container name=$mock_container_name"
			exit_code=1
		}
	fi
	if [[ -n "${created_network:-}" ]]; then
		docker network rm "$network_name" >/dev/null || {
			log WARN "could not remove owned test network name=$network_name"
			exit_code=1
		}
	fi
	if [[ "$model_directory" == "$workspace_directory"/.testing/decidealot-real-clm-* ]]; then
		docker run --rm --user root --entrypoint /bin/sh \
			--mount "type=bind,source=$model_directory,target=/models" \
			"$CPU_IMAGE" -ceu 'find /models -mindepth 1 -maxdepth 1 -exec rm --recursive --force -- {} +' || {
			log ERROR "could not clear test model directory path=$model_directory"
			exit_code=1
		}
		rmdir -- "$model_directory" || {
			log ERROR "could not remove test model directory path=$model_directory"
			exit_code=1
		}
	fi
	exit "$exit_code"
}
trap cleanup EXIT

docker network create "$network_name" >/dev/null
created_network=1

log INFO "starting strict mock embeddings endpoint"
docker run --detach --rm --init \
	--name "$mock_container_name" \
	--network "$network_name" \
	--network-alias "$MOCK_HOSTNAME" \
	--user "$RUNTIME_UID:$RUNTIME_GID" \
	--read-only \
	--cap-drop ALL \
	--security-opt no-new-privileges:true \
	--pids-limit 64 \
	--tmpfs "/tmp:rw,noexec,nosuid,size=8m" \
	--mount "type=bind,source=$mock_script,target=/opt/mock_embeddings_server.py,readonly" \
	--env "MOCK_EMBEDDINGS_MODEL=$MOCK_MODEL" \
	--env "MOCK_EMBEDDINGS_API_KEY=$MOCK_API_KEY" \
	--entrypoint /opt/torch-venv/bin/python \
	"$CPU_IMAGE" /opt/mock_embeddings_server.py >/dev/null
started_mock=1

log INFO "starting real CLM service image=$CPU_IMAGE"
docker run --detach --rm --init \
	--name "$service_container_name" \
	--network "$network_name" \
	--user "$RUNTIME_UID:$RUNTIME_GID" \
	--publish "127.0.0.1:$port:8080" \
	--read-only \
	--cap-drop ALL \
	--security-opt no-new-privileges:true \
	--pids-limit 512 \
	--tmpfs "/tmp:rw,noexec,nosuid,size=128m" \
	--tmpfs "/var/run:rw,noexec,nosuid,size=8m" \
	--tmpfs "/var/cache:rw,exec,nosuid,nodev,size=512m,uid=$RUNTIME_UID,gid=$RUNTIME_GID,mode=0755" \
	--mount "type=bind,source=$model_directory,target=/models" \
	--env DECIDEALOT_LAYA_ENABLED=false \
	--env DECIDEALOT_VON_ENABLED=false \
	--env DECIDEALOT_CLM_ENABLED=true \
	--env "DECIDEALOT_CLM_EMBEDDINGS_URL=http://$MOCK_HOSTNAME:$MOCK_PORT/v1/embeddings" \
	--env "DECIDEALOT_CLM_EMBEDDINGS_MODEL=$MOCK_MODEL" \
	--env "DECIDEALOT_CLM_EMBEDDINGS_API_KEY=$MOCK_API_KEY" \
	"$CPU_IMAGE" >/dev/null
started_service=1

healthy=""
for _ in $(seq 1 "$HEALTHCHECK_ATTEMPTS"); do
	if curl --fail --silent --max-time 3 "$base_url/health" >/dev/null; then
		healthy=1
		break
	fi
	if [[ "$(docker inspect --format '{{.State.Running}}' "$service_container_name")" != "true" ]]; then
		docker logs "$service_container_name" >&2
		log ERROR "real CLM service stopped before becoming ready"
		exit 1
	fi
	sleep "$HEALTHCHECK_INTERVAL_SECONDS"
done

if [[ -z "$healthy" ]]; then
	docker logs "$service_container_name" >&2
	log ERROR "real CLM service did not become ready"
	exit 1
fi

[[ -f "$model_directory/clm/CLM_v0.1-8B.pt" ]] || {
	log ERROR "startup did not download the CLM checkpoint"
	exit 1
}
[[ ! -e "$model_directory/laya" && ! -e "$model_directory/von" ]] || {
	log ERROR "CLM-only startup prepared an unrelated provider"
	exit 1
}

log INFO "checking real CLM head through the public TypeSafe API"
DECIDEALOT_BASE_URL="$base_url" DECIDEALOT_TESTED_MODELS=clm \
	python3 "$script_directory/real_models_client.py" || {
	docker logs "$service_container_name" >&2
	docker logs "$mock_container_name" >&2
	log ERROR "real CLM decision check failed"
	exit 1
}
log INFO "real CLM checkpoint and mock embeddings contract passed image=$CPU_IMAGE"
