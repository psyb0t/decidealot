---
title: "Deployment"
description: "Hardened docker run commands for the CPU and CUDA images, a Jev-only container, Compose, model storage, network exposure and upgrades."
nav_order: 6
---

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

On a fresh directory Decidealot downloads and verifies every enabled local provider bundle before it starts listening, so `/health` does not answer until then. Laya and Von are enabled by default. CLM becomes enabled when its embeddings endpoint is configured; [Models](models.md#clm) covers what that endpoint must return. This can take minutes. Decidealot keeps neither model nor Torch loaded after preparation. Later starts verify existing bundles and download only missing files. Hosted Jev needs no bundle and becomes available when its TypeSafe key is configured.

## Configuration

Pass configuration with Docker `--env-file` or your container manager. REST and MCP Streamable HTTP share port `8080`, with MCP at `/mcp`. Docker port publishing decides where the service is reachable. [Configuration](configuration.md) lists every variable with its default and range.

## Model storage

Mount one narrow host directory at `/models`, reserved for model bundles. Decidealot creates and manages `/models/laya`, `/models/von`, and, when enabled, `/models/clm` inside it. Model storage is not an application setting; the container always uses `/models`.

The mounted directory must be writable by the runtime UID and GID. The commands on this page pass your current IDs with `--user "$runtime_uid:$runtime_gid"`, so a directory you create yourself is writable without an image-specific `chown`. The image falls back to non-root `1000:1000` only if a caller does not pass a runtime user.

Keep the directory between upgrades to reuse downloaded bundles. Deleting a provider's subdirectory makes the next start download it again.

## Jev-only deployment

To run only TypeSafe's hosted Jev, disable Laya and Von and leave CLM unconfigured. Startup then downloads no model bundles and needs no `/models` mount. Pass the upstream key as an environment variable from your secret store:

```bash
docker run --detach --name decidealot --init --restart unless-stopped \
  --read-only --cap-drop ALL --security-opt no-new-privileges:true \
  --tmpfs /tmp:rw,noexec,nosuid,size=128m \
  --env DECIDEALOT_TYPESAFE_API_KEY \
  --env DECIDEALOT_LAYA_ENABLED=false \
  --env DECIDEALOT_VON_ENABLED=false \
  --publish 127.0.0.1:8080:8080 \
  psyb0t/decidealot:latest
```

`DECIDEALOT_TYPESAFE_API_KEY` must already be set in the shell or supplied by your container manager. `GET /v1/models` confirms which Jev selectors are enabled. Every Jev decision sends its complete state, instructions and criteria to TypeSafe, and the key stays in the server process. A TypeSafe authentication or service failure becomes a `503` to the caller; check the upstream account separately. [Models](models.md#hosted-jev) covers the catalog and its cache.

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

Pass both values to `docker run` with `--env DECIDEALOT_MCP_ALLOWED_HOSTS --env DECIDEALOT_MCP_ALLOWED_ORIGINS`, or set them in the Compose `.env` file. Do not use a catch-all host or disable the protection. After bearer authentication, an unexpected `Host` returns `421`, and an unexpected browser `Origin` returns `403` before an MCP session exists. [Security](security.md) lists everything to check before exposing the service.

## Lifecycle and upgrades

Resident slots, idle unloading and the execution lanes are covered in [Models](models.md#scheduling-and-memory). `POST /v1/models/unload` releases every loaded local provider at once; it returns `409` without unloading anything while a local provider has an active decision request.

Use a versioned image tag for upgrades. Pull it, recreate the container with the same model directory and configuration file, then check `/health`. Keep the model directory to reuse downloaded bundles.

```bash
docker pull psyb0t/decidealot:latest
docker stop decidealot
docker rm decidealot
```

The commands above target only the explicitly named Decidealot container. Run the CPU or CUDA command again with the new image tag and the same `model_directory`.
