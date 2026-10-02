# Deployment

Decidealot is a Docker image. Run a released image tag, bind one narrow host directory at `/models`, and publish the HTTP port only where you intend to serve it.

## CPU deployment

This is the normal setup. It binds the API to the local machine, stores model bundles in a host directory you control, drops every Linux capability, blocks privilege escalation, uses a read-only root filesystem, bounds temporary storage, and limits logs and resources. The `8g` memory and four CPU ceilings are deliberate starting limits for loading and running local models. Measure a real workload before lowering them.

```bash
model_directory=/srv/decidealot/models
runtime_uid=$(id -u)
runtime_gid=$(id -g)
sudo install --directory --owner="$runtime_uid" --group="$runtime_gid" "$model_directory"

docker run --detach --name decidealot --init --restart unless-stopped \
  --user "$runtime_uid:$runtime_gid" \
  --read-only --cap-drop ALL --security-opt no-new-privileges:true \
  --pids-limit 512 --memory 8g --cpus 4 \
  --tmpfs /tmp:rw,noexec,nosuid,size=128m \
  --tmpfs /var/run:rw,noexec,nosuid,size=8m \
  --log-driver json-file --log-opt max-size=10m --log-opt max-file=5 \
  --mount type=bind,source="$model_directory",target=/models \
  --publish 127.0.0.1:8080:8080 \
  psyb0t/decidealot:latest
```

On a fresh directory Decidealot downloads and verifies every enabled local provider bundle before `/health` returns `200`. Laya and Von are enabled by default. CLM becomes enabled when its embeddings endpoint is configured. This can take minutes. Decidealot keeps neither model nor Torch loaded after preparation. Later starts verify existing bundles and download only missing files. Hosted Jev needs no bundle and becomes available when its TypeSafe key is configured.

## Configuration

Pass configuration with Docker `--env-file` or your container manager. The image has fixed internal ports. Docker port publishing decides where the service is reachable. REST and MCP Streamable HTTP share port `8080`, with MCP at `/mcp`. Model storage is not an application setting. The container always uses `/models`.

| Variable | Default | Meaning |
| --- | --- | --- |
| `DECIDEALOT_API_KEY` | empty | Optional Bearer token for every public HTTP API and MCP request except `/health`. |
| `DECIDEALOT_PROVIDER_IDLE_UNLOAD_SECONDS` | `600` | Seconds without a request before automatic unload. `0` disables the idle timer. |
| `DECIDEALOT_MAX_RESIDENT_LOCAL_PROVIDERS` | `1` | Maximum simultaneous local model processes, `1` to `3`. Raise only after sizing RAM or VRAM for the selected models. |
| `DECIDEALOT_MAX_BATCH_REQUESTS` | `0` | Optional maximum number of batch items. `0` means no item-count cap; the body-size limit still applies. |
| `DECIDEALOT_MAX_BATCH_CONCURRENCY` | `0` | Optional cap on simultaneous provider calls made by batches. `0` leaves model and device scheduling as the only concurrency limits. |
| `DECIDEALOT_LAYA_ENABLED` | `true` | Download and expose Laya selectors. |
| `DECIDEALOT_VON_ENABLED` | `true` | Download and expose Von selectors. |
| `DECIDEALOT_CLM_ENABLED` | `auto` | Enable CLM when its embeddings URL is set. Use `true` to require it or `false` to skip it. |
| `DECIDEALOT_CLM_EMBEDDINGS_URL` | empty | Fixed OpenAI-compatible `/v1/embeddings` URL for CLM. Required when CLM is enabled. |
| `DECIDEALOT_CLM_EMBEDDINGS_MODEL` | `qwen3-8b` | Model selector sent to the CLM embeddings endpoint. It must return Qwen3-8B last-token vectors with width `4096`. |
| `DECIDEALOT_CLM_EMBEDDINGS_API_KEY` | empty | Optional Bearer token passed only to the configured CLM embeddings endpoint. |
| `DECIDEALOT_CLM_EMBEDDINGS_TIMEOUT_SECONDS` | `120` | CLM embeddings request timeout in seconds. |
| `DECIDEALOT_CLM_PARALLEL_WITH_LOCAL_MODELS` | `false` | Allow CLM and its Qwen encoder to overlap local model inference. Leave false when sharing a host or GPU. |
| `DECIDEALOT_TYPESAFE_API_KEY` | empty | Upstream Bearer key for TypeSafe Jev. Keep it separate from the caller-facing `DECIDEALOT_API_KEY`. |
| `DECIDEALOT_JEV_ENABLED` | `auto` | Enable Jev when the upstream key is present. `true` requires a key; `false` disables it. |
| `DECIDEALOT_MAX_REQUEST_BYTES` | `1048576` | Maximum JSON request size. |
| `DECIDEALOT_LOG_LEVEL` | `INFO` | Structured log threshold. |
| `DECIDEALOT_MCP_ALLOWED_HOSTS` | loopback and `decidealot` | Comma-separated MCP `Host` values accepted while DNS rebinding protection stays enabled. |
| `DECIDEALOT_MCP_ALLOWED_ORIGINS` | loopback HTTP origins | Comma-separated MCP `Origin` values accepted while DNS rebinding protection stays enabled. |

The mounted host directory must be writable by the runtime UID and GID. The Docker commands above pass your current IDs with `--user "$runtime_uid:$runtime_gid"`. Decidealot creates `laya`, `von`, and, when enabled, `clm` subdirectories under `/models`. The image falls back to non-root `1000:1000` only if a caller does not pass a runtime user. Mount only the directory reserved for model bundles.

CLM is a local `Contrastive-LM/CLM-v0.1-8B` projection head over one external embeddings service. It sends rendered decision state and criteria to that service. Configure a URL you control or trust. The configured model must emit Qwen3-8B last-token embeddings with exactly 4096 float values. Decidealot rejects any other vector width before the projection head runs.

Jev is hosted by TypeSafe. Decidealot reads the authenticated model catalog from `https://api.typesafe.ai/v1/models` and refreshes it every 60 seconds. Every Jev decision sends its complete state, instructions, and criteria to `https://api.typesafe.ai/v1/systemone`, using one exact name from that catalog. The upstream key stays in the server process and is never a field in a decision request. To run Jev alone, set `DECIDEALOT_LAYA_ENABLED=false`, `DECIDEALOT_VON_ENABLED=false`, leave CLM unconfigured, and provide `DECIDEALOT_TYPESAFE_API_KEY` through a private environment file or secret store. That configuration needs no writable `/models` mount and downloads no model bundles. A TypeSafe authentication or service failure becomes a safe `503` to the caller; inspect the upstream account separately rather than exposing its response.

## CUDA deployment

CUDA uses the separately tagged amd64 image. Install NVIDIA Container Toolkit on the host first, then use `--gpus all` and the same hardening options as the CPU command.

```bash
model_directory=/srv/decidealot/models
runtime_uid=$(id -u)
runtime_gid=$(id -g)
sudo install --directory --owner="$runtime_uid" --group="$runtime_gid" "$model_directory"

docker run --detach --name decidealot --init --restart unless-stopped \
  --user "$runtime_uid:$runtime_gid" \
  --gpus all \
  --read-only --cap-drop ALL --security-opt no-new-privileges:true \
  --pids-limit 512 --memory 8g --cpus 4 \
  --tmpfs /tmp:rw,noexec,nosuid,size=128m \
  --tmpfs /var/run:rw,noexec,nosuid,size=8m \
  --tmpfs /var/cache:rw,exec,nosuid,nodev,size=512m,uid=$runtime_uid,gid=$runtime_gid,mode=0755 \
  --log-driver json-file --log-opt max-size=10m --log-opt max-file=5 \
  --mount type=bind,source="$model_directory",target=/models \
  --publish 127.0.0.1:8080:8080 \
  psyb0t/decidealot:latest-cuda
```

The CUDA runtime needs `/var/cache` because Triton compiles and loads short-lived CUDA helpers there. That narrow mount is writable and executable for the runtime UID and GID, while the rest of the container remains read-only and `noexec`. The CUDA image retains GCC and Python headers for those helpers. The CPU and CUDA images each select their own runtime and use the fixed `/models` path.

## Compose

The included Compose file uses the same fixed `/models` path. Use it from a checked-out release when you want Compose to build the image locally.

```bash
cp .env.example .env
model_directory=/srv/decidealot/models
runtime_uid=$(id -u)
runtime_gid=$(id -g)
sudo install --directory --owner="$runtime_uid" --group="$runtime_gid" "$model_directory"
# Set DECIDEALOT_MODEL_DIRECTORY=$model_directory in .env.
DECIDEALOT_UID="$runtime_uid" DECIDEALOT_GID="$runtime_gid" docker compose up --detach
```

`DECIDEALOT_MODEL_DIRECTORY` is a Compose variable. It names the host directory that Compose bind-mounts at `/models`. `DECIDEALOT_UID` and `DECIDEALOT_GID` set the container process to your current host identity. They are passed on the command line rather than stored in `.env`, so each caller gets its own identity. Each missing value falls back to `1000`. Decidealot uses the fixed container path. A cold start becomes healthy after every enabled bundle is present and verified.

For CUDA, use the same `.env` file and apply the CUDA override. It builds the local CUDA image, grants the service GPU access, and adds only the writable executable Triton cache that CUDA needs.

```bash
DECIDEALOT_UID="$runtime_uid" DECIDEALOT_GID="$runtime_gid" docker compose -f docker-compose.yml -f docker-compose.cuda.yml up --detach
```

## Authentication and exposure

Loopback with no token is fine for one host. Before any network boundary is involved, set `DECIDEALOT_API_KEY` in a private environment file and send it as a Bearer token:

```bash
export DECIDEALOT_API_KEY=REPLACE_ME

curl --fail --show-error http://127.0.0.1:8080/v1/models \
  --header "Authorization: Bearer $DECIDEALOT_API_KEY"
```

Use TLS and `DECIDEALOT_API_KEY` when you expose the API beyond the local host. Keep MCP DNS rebinding protection on by allowlisting the exact reverse-proxy host name, with no scheme, and the browser Origin, with its scheme, when a browser MCP client sends one. Direct loopback clients and another container addressing `decidealot` already match the defaults.

```bash
export DECIDEALOT_MCP_ALLOWED_HOSTS='127.0.0.1,127.0.0.1:*,localhost,localhost:*,[::1],[::1]:*,decidealot,decidealot:*,mcp.example.net'
export DECIDEALOT_MCP_ALLOWED_ORIGINS='http://127.0.0.1:*,http://localhost:*,http://[::1]:*,https://mcp.example.net'
```

Pass both values to `docker run` with `--env DECIDEALOT_MCP_ALLOWED_HOSTS --env DECIDEALOT_MCP_ALLOWED_ORIGINS`, or set them in the Compose `.env` file. Do not use a catch-all host or disable the protection. After bearer authentication, an unexpected `Host` returns `421`, and an unexpected browser `Origin` returns `403` before an MCP session exists.

## Lifecycle and upgrades

The service keeps at most one local provider resident by default. A request for another local model waits for an idle slot, releases the least recently used idle model and Torch runtime, then starts the selected provider. A batch may name more local providers than resident slots; each waits its turn. Set `DECIDEALOT_MAX_RESIDENT_LOCAL_PROVIDERS=2` or `3` to keep more families loaded when memory permits. On CUDA, local inference is still serialized. CLM shares that local serial lane by default, even on CPU, because its Qwen embeddings endpoint may use the same host. Set `DECIDEALOT_CLM_PARALLEL_WITH_LOCAL_MODELS=true` only when that overlap is safe. Hosted Jev does not alter the local provider lifecycle and can overlap local calls. `POST /v1/models/unload` is the only public unload endpoint. It affects local providers only, is idempotent, and returns `409` without unloading anything while a local provider has an active decision request.

Use a versioned image tag for upgrades. Pull it, recreate the container with the same model directory and configuration file, then check `/health`. Keep the model directory to reuse downloaded bundles.

```bash
docker pull psyb0t/decidealot:latest
docker stop decidealot
docker rm decidealot
```

The commands above target only the explicitly named Decidealot container. Run the CPU or CUDA command again with the new image tag and the same `model_directory`.
