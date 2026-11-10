---
title: "Configuration"
description: "Every DECIDEALOT_ environment variable with its default and valid range: providers, CLM, Jev, limits, MCP allowlists, logging and Compose values."
nav_order: 5
---

# Configuration

Decidealot reads its configuration once, at startup, from `DECIDEALOT_*` environment variables. Pass them with `--env`, `--env-file` or your container manager; a change needs a container restart. An invalid value stops the service before it listens, with a message that names the variable (see [Troubleshooting](troubleshooting.md#startup-refusals)). [`.env.example`](https://github.com/psyb0t/decidealot/blob/main/.env.example) lists the values the Compose file passes. Boolean variables accept `true` and `false` (also `1`, `0`, `yes`, `no`).

The container always stores local bundles under `/models`. Its only model storage setting is the host directory mounted there.

## Access and limits

| Variable | Default | Meaning |
| --- | --- | --- |
| `DECIDEALOT_API_KEY` | empty | Optional Bearer token for every public HTTP API and MCP request except `/health`. Empty disables authentication. |
| `DECIDEALOT_MAX_REQUEST_BYTES` | `1048576` | Maximum request body size, from `1024` to `16777216`. Larger bodies return `413`. |
| `DECIDEALOT_MAX_BATCH_REQUESTS` | `0` | Optional batch item cap. `0` means no item-count cap; the body-size limit still applies. Larger batches return `422`. |
| `DECIDEALOT_MAX_BATCH_CONCURRENCY` | `0` | Optional cap on simultaneous provider calls made by batches. `0` leaves only model and device scheduling limits. |
| `DECIDEALOT_MCP_ALLOWED_HOSTS` | loopback names and `decidealot` | Comma-separated `Host` values accepted by MCP. Add each reverse-proxy hostname here. Empty entries are refused. |
| `DECIDEALOT_MCP_ALLOWED_ORIGINS` | loopback HTTP origins | Comma-separated browser origins accepted by MCP. Add each public browser origin here. Empty entries are refused. |

The MCP defaults, which a value you set replaces rather than extends:

```dotenv
DECIDEALOT_MCP_ALLOWED_HOSTS=127.0.0.1,127.0.0.1:*,localhost,localhost:*,[::1],[::1]:*,decidealot,decidealot:*
DECIDEALOT_MCP_ALLOWED_ORIGINS=http://127.0.0.1:*,http://localhost:*,http://[::1]:*
```

## Local providers and lifecycle

| Variable | Default | Meaning |
| --- | --- | --- |
| `DECIDEALOT_LAYA_ENABLED` | `true` | Download and expose Laya selectors. |
| `DECIDEALOT_VON_ENABLED` | `true` | Download and expose Von selectors. |
| `DECIDEALOT_PROVIDER_IDLE_UNLOAD_SECONDS` | `600` | Idle time before automatic unload, `0` to `86400`. Set `0` to disable only timeout-based unloads. |
| `DECIDEALOT_MAX_RESIDENT_LOCAL_PROVIDERS` | `1` | Maximum resident local provider processes, from `1` to `3`. CUDA inference remains serial regardless of this setting. |
| `DECIDEALOT_PROVIDER_START_TIMEOUT_SECONDS` | `180` | How long a starting local provider may take to answer its health check, above `0` and at most `1800`. |
| `DECIDEALOT_REQUEST_TIMEOUT_SECONDS` | `120` | Timeout for one call to a local provider or to TypeSafe, above `0` and at most `600`. A timeout returns `503`. |

## CLM

| Variable | Default | Meaning |
| --- | --- | --- |
| `DECIDEALOT_CLM_ENABLED` | `auto` | Enable CLM when its embeddings URL is set. Use `true` to require it or `false` to skip it. |
| `DECIDEALOT_CLM_EMBEDDINGS_URL` | empty | Exact OpenAI-compatible `/v1/embeddings` URL used only by CLM. Required when CLM is enabled. Must be an absolute `http` or `https` URL without credentials, query or fragment. |
| `DECIDEALOT_CLM_EMBEDDINGS_MODEL` | `qwen3-8b` | Model selector sent to the embeddings endpoint. It must produce Qwen3-8B last-token vectors with width `4096`. |
| `DECIDEALOT_CLM_EMBEDDINGS_API_KEY` | empty | Optional Bearer token sent only to the configured CLM embeddings endpoint. |
| `DECIDEALOT_CLM_EMBEDDINGS_TIMEOUT_SECONDS` | `120` | One CLM embeddings request timeout in seconds, above `0` and at most `600`. |
| `DECIDEALOT_CLM_CANDIDATE_CACHE_ENTRIES` | `1024` | Candidate embedding LRU capacity, `0` to `4096`. `0` disables caching. |
| `DECIDEALOT_CLM_CANDIDATE_CACHE_TTL_SECONDS` | `600` | Candidate vector lifetime, greater than `0` and at most `86400` seconds. |
| `DECIDEALOT_CLM_MAX_TEXT_BYTES` | `8192` | UTF-8 byte limit per rendered state plus instructions or candidate, `1` to `1048576`. Over-limit CLM inputs return `422`. |
| `DECIDEALOT_CLM_PARALLEL_WITH_LOCAL_MODELS` | `false` | Permit CLM and its Qwen encoder to overlap other local models. Keep false when they share this host's resources. |

[Models](models.md#clm) explains the encoder requirements and the cache.

## Hosted Jev

| Variable | Default | Meaning |
| --- | --- | --- |
| `DECIDEALOT_TYPESAFE_API_KEY` | empty | Private upstream Bearer key for TypeSafe's hosted Jev API. Separate from the caller-facing `DECIDEALOT_API_KEY`. |
| `DECIDEALOT_JEV_ENABLED` | `auto` | Enable Jev when the upstream key is set. `true` requires a key; `false` hides Jev even with a key. |

## Logging

| Variable | Default | Meaning |
| --- | --- | --- |
| `DECIDEALOT_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING` or `ERROR`, case-insensitive. |
| `DECIDEALOT_LOG_FILE` | `/tmp/decidealot.log` | Absolute path of the rotating JSON log file (50 MB, five backups). Logs also go to stderr, which is what `docker logs` shows. In the documented read-only container, `/tmp` is a tmpfs. |

## Set by the image

The images set these, and the defaults below match them. Leave them alone unless you know why.

| Variable | Image value | Meaning |
| --- | --- | --- |
| `DECIDEALOT_DEVICE` | `cpu` or `cuda` | Torch device for local providers. Must match the image variant. |
| `DECIDEALOT_IMAGE_VARIANT` | `cpu` or `cuda` | Must match `DECIDEALOT_DEVICE` and the variant recorded in `/etc/decidealot/image-variant`. |
| `DECIDEALOT_LISTEN_HOST` | `0.0.0.0` | Address uvicorn binds inside the container. Docker port publishing decides where the service is reachable. |
| `DECIDEALOT_LISTEN_PORT` | `8080` | Port uvicorn binds, `1024` to `65535`. The image healthcheck and every example assume `8080`. |

## Compose values

`docker-compose.yml` reads three more variables that are not application settings:

| Variable | Meaning |
| --- | --- |
| `DECIDEALOT_MODEL_DIRECTORY` | Host directory bind-mounted at `/models`. Required; Compose refuses to start without it. |
| `DECIDEALOT_UID`, `DECIDEALOT_GID` | Runtime user and group for the container process. `make run` passes your own `id -u` and `id -g`. Each missing value falls back to `1000`. |
