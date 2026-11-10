---
title: "Architecture"
description: "How decidealot is put together: the FastAPI and MCP process, the provider supervisor, local model child processes, and the path one request takes."
nav_order: 10
---

# Architecture

Decidealot is one Python process that owns up to three model processes. The main process speaks HTTP and MCP to callers. Each local model runs in its own child process with its own Torch runtime, so stopping the process frees all of its memory.

## Processes

| Process | Started | Listens on | Code |
| --- | --- | --- | --- |
| Decidealot (FastAPI, uvicorn, MCP) | Container start | `0.0.0.0:8080` | [`src/decidealot/app.py`](https://github.com/psyb0t/decidealot/blob/main/src/decidealot/app.py) |
| Laya | First request for a Laya selector | `127.0.0.1:8011` | [`src/decidealot/provider_entrypoint.py`](https://github.com/psyb0t/decidealot/blob/main/src/decidealot/provider_entrypoint.py) |
| Von | First request for a Von selector | `127.0.0.1:8012` | `provider_entrypoint.py` |
| CLM | First request for a CLM selector | `127.0.0.1:8013` | [`src/decidealot/clm_runtime.py`](https://github.com/psyb0t/decidealot/blob/main/src/decidealot/clm_runtime.py) |

The main process runs from `/opt/app-venv`. The model processes run from `/opt/torch-venv`, which holds PyTorch, Laya, von-sdk and huggingface_hub, installed from hash-locked requirement files. Hosted Jev has no process: the main process calls `https://api.typesafe.ai` directly.

## Startup

1. [`Settings`](https://github.com/psyb0t/decidealot/blob/main/src/decidealot/settings.py) reads and checks every `DECIDEALOT_*` variable. An invalid value stops the process here.
2. The app starts the MCP session manager, then the [provider supervisor](https://github.com/psyb0t/decidealot/blob/main/src/decidealot/supervisor.py) runs `provider_entrypoint.py prepare <provider>` for each enabled local provider, one after another. Each run downloads missing files from a pinned Hugging Face revision into `/models/<provider>` and checks them.
3. Only then does uvicorn start listening on port `8080`. No model is loaded yet.

## One request

1. A REST route or an MCP tool hands the body to `DecisionService` in [`decisions.py`](https://github.com/psyb0t/decidealot/blob/main/src/decidealot/decisions.py). Both paths share it, so validation, routing and scheduling are the same.
2. The body is checked against the TypeSafe request models in [`typesafe.py`](https://github.com/psyb0t/decidealot/blob/main/src/decidealot/typesafe.py). `ModelRouter` in [`providers.py`](https://github.com/psyb0t/decidealot/blob/main/src/decidealot/providers.py) maps the selector to a fixed backend, refreshing TypeSafe's catalog first when a hosted name is involved. `config` is checked for that provider.
3. For a local provider, the request takes the per-provider lock, then a place in the local lane (one at a time on CUDA, CLM alone by default), then a lease from the supervisor. The supervisor starts the provider process if needed, first stopping the least recently used idle one when the resident limit is reached, and waits for its `/health`.
4. `native_provider_payload` rewrites the request into the body that provider accepts, and `HTTPProviderClient` posts it to the provider's loopback port, or to TypeSafe for Jev.
5. The provider's answer is reduced to the TypeSafe `model`, `answers` and `usage` fields and returned. Provider-only fields never reach the caller.

A background task stops local providers that have been idle for `DECIDEALOT_PROVIDER_IDLE_UNLOAD_SECONDS`.

## CLM

The CLM process loads the verified projection head and, for each request, renders the state and every candidate answer to text, asks the configured embeddings endpoint for their vectors, normalizes them and scores candidates with the two projection heads. It keeps a bounded cache of candidate vectors. [Models](models.md#clm) covers the encoder requirements.

## Images

`Dockerfile` and `Dockerfile.cuda` build on `psyb0t/torchbase` images with PyTorch 2.14 for CPU or CUDA 12.6. The application is built in a separate `python:3.12-slim-bookworm` stage with `uv sync --frozen --no-dev` and copied in. Each image writes its variant to `/etc/decidealot/image-variant`, which `Settings` checks against `DECIDEALOT_DEVICE`.
