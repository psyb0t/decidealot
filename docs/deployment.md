# Deployment

Decidealot is a Docker image. Run a released image tag, bind one narrow host directory at `/models`, and publish the HTTP port only where you intend to serve it.

## CPU deployment

This is the normal setup. It binds the API to the local machine, stores model bundles in a host directory you control, drops every Linux capability, blocks privilege escalation, uses a read-only root filesystem, bounds temporary storage, and limits logs and resources. The `8g` memory and four CPU ceilings are deliberate starting limits for loading and running local models. Measure a real workload before lowering them.

```bash
model_directory=/srv/decidealot/models
sudo install --directory --owner=10001 --group=10001 "$model_directory"

docker run --detach --name decidealot --init --restart unless-stopped \
  --read-only --cap-drop ALL --security-opt no-new-privileges:true \
  --pids-limit 512 --memory 8g --cpus 4 \
  --tmpfs /tmp:rw,noexec,nosuid,size=128m \
  --tmpfs /var/run:rw,noexec,nosuid,size=8m \
  --log-driver json-file --log-opt max-size=10m --log-opt max-file=5 \
  --mount type=bind,source="$model_directory",target=/models \
  --publish 127.0.0.1:8080:8080 \
  psyb0t/decidealot:v0.3.0
```

On a fresh directory Decidealot downloads and verifies Laya and Von before `/health` returns `200`. This can take minutes. It keeps neither model nor Torch loaded after preparation. Later starts verify the existing bundles and download only missing files.

## Configuration

Pass configuration with Docker `--env-file` or your container manager. The image has fixed internal ports. Docker port publishing decides where the service is reachable. REST and MCP Streamable HTTP share port `8080`, with MCP at `/mcp`. Model storage is not an application setting. The container always uses `/models`.

| Variable | Default | Meaning |
| --- | --- | --- |
| `DECIDEALOT_API_KEY` | empty | Optional Bearer token for every public HTTP API and MCP request except `/health`. |
| `DECIDEALOT_PROVIDER_IDLE_UNLOAD_SECONDS` | `600` | Seconds without a request before automatic unload. `0` disables the idle timer. |
| `DECIDEALOT_MAX_REQUEST_BYTES` | `1048576` | Maximum JSON request size. |
| `DECIDEALOT_LOG_LEVEL` | `INFO` | Structured log threshold. |

The mounted host directory must be writable by UID and GID `10001`. Decidealot creates its fixed `laya` and `von` subdirectories under `/models`. Do not mount `/`, `/home`, `/root`, a Docker socket, or a broad parent directory.

## CUDA deployment

CUDA uses the separately tagged amd64 image. Install NVIDIA Container Toolkit on the host first, then use `--gpus all` and the same hardening options as the CPU command.

```bash
model_directory=/srv/decidealot/models
sudo install --directory --owner=10001 --group=10001 "$model_directory"

docker run --detach --name decidealot --init --restart unless-stopped \
  --gpus all \
  --read-only --cap-drop ALL --security-opt no-new-privileges:true \
  --pids-limit 512 --memory 8g --cpus 4 \
  --tmpfs /tmp:rw,noexec,nosuid,size=128m \
  --tmpfs /var/run:rw,noexec,nosuid,size=8m \
  --tmpfs /var/cache:rw,exec,nosuid,nodev,size=512m,uid=10001,gid=10001,mode=0755 \
  --log-driver json-file --log-opt max-size=10m --log-opt max-file=5 \
  --mount type=bind,source="$model_directory",target=/models \
  --publish 127.0.0.1:8080:8080 \
  psyb0t/decidealot:v0.3.0-cuda
```

The CUDA runtime needs `/var/cache` because Triton compiles and loads short-lived CUDA helpers there. That narrow mount is writable and executable for UID and GID `10001`, while the rest of the container remains read-only and `noexec`. The CUDA image intentionally retains GCC and Python headers because Triton compiles those helpers during real model work. The image chooses its own CPU or CUDA runtime. Do not set a device or model-directory environment variable. A CPU or CUDA mismatch fails before provider startup instead of pretending to work.

## Compose

The included Compose file uses the same fixed `/models` path. Use it from a checked-out release when you want Compose to build the image locally.

```bash
cp .env.example .env
model_directory=/srv/decidealot/models
sudo install --directory --owner=10001 --group=10001 "$model_directory"
# Set DECIDEALOT_MODEL_DIRECTORY=$model_directory in .env.
docker compose up --detach
```

`DECIDEALOT_MODEL_DIRECTORY` belongs to Compose only. It names the host directory that Compose bind-mounts at `/models`. Decidealot does not read it. A cold start remains unhealthy until both model bundles are present and verified.

For CUDA, use the same `.env` file and apply the CUDA override. It builds the local CUDA image, grants the service GPU access, and adds only the writable executable Triton cache that CUDA needs.

```bash
docker compose -f docker-compose.yml -f docker-compose.cuda.yml up --detach
```

## Authentication and exposure

Loopback with no token is fine for one host. Before any network boundary is involved, set `DECIDEALOT_API_KEY` in a private environment file and send it as a Bearer token:

```bash
export DECIDEALOT_API_KEY=REPLACE_ME

curl --fail --show-error http://127.0.0.1:8080/v1/models \
  --header "Authorization: Bearer $DECIDEALOT_API_KEY"
```

Use TLS at the edge. Do not expose an unauthenticated model API to the internet.

MCP is intentionally loopback-only in this release. The v2 transport keeps its DNS rebinding defense and rejects non-loopback `Host` headers. Use a local MCP client or a loopback-preserving tunnel. Do not put `/mcp` behind a reverse proxy until Decidealot has an explicit trusted-host configuration for that deployment.

## Lifecycle and upgrades

The service keeps at most one provider resident. A request for another model waits for the active request, releases the old model and Torch runtime, then starts the selected provider. `POST /v1/models/unload` is the only public unload endpoint. It is idempotent and returns `409` without unloading anything while a provider has an active decision request.

Use a versioned image tag for upgrades. Pull it, recreate the container with the same model directory and configuration file, then check `/health`. Do not replace the model directory unless you want to download model files again.

```bash
docker pull psyb0t/decidealot:v0.3.0
docker stop decidealot
docker rm decidealot
```

The commands above target only the explicitly named Decidealot container. Run the CPU or CUDA command again with the new image tag and the same `model_directory`.
