---
title: "Security"
description: "What decidealot listens on, what it sends off the host, how bearer authentication and MCP host checks work, and what to set before exposing it."
nav_order: 7
---

# Security

Decidealot is built for one host by default: the examples publish port `8080` on loopback only, and authentication is off. Read this page before putting it behind a proxy, a tunnel or a public address.

## What it listens on

The container serves one port, `8080`, for both the HTTP API and MCP. Local providers listen on `127.0.0.1:8011` (Laya), `127.0.0.1:8012` (Von) and `127.0.0.1:8013` (CLM) inside the container; they are not published and requests cannot reach them directly.

## Authentication

`DECIDEALOT_API_KEY` empty means no authentication. When it is set, every route except `/health` and every MCP request need `Authorization: Bearer <key>`. Decidealot compares the token in constant time. A missing, malformed or wrong token returns `401` with `UNAUTHORIZED` and `WWW-Authenticate: Bearer`, and Decidealot logs the failure reason without the token.

Before putting Decidealot behind a proxy, tunnel, or public address, set `DECIDEALOT_API_KEY` to a real secret, require it from every caller, and terminate TLS in front of the container. Keep the key in a private environment file or secret store, never in a tracked file or a client-side application.

## MCP host and origin checks

The MCP endpoint keeps DNS rebinding protection on. It accepts only the `Host` values in `DECIDEALOT_MCP_ALLOWED_HOSTS` and the browser `Origin` values in `DECIDEALOT_MCP_ALLOWED_ORIGINS`; the defaults cover loopback and the `decidealot` Compose service name. After bearer authentication, an unlisted host returns `421` and an unlisted origin returns `403`. Add the exact public names for a proxy as shown in [Expose MCP through a proxy](mcp.md#expose-mcp-through-a-proxy). Do not add a broad wildcard for an internet-facing endpoint.

## Data that leaves the host

Laya and Von run entirely inside the container after their bundles are downloaded. Two providers send decision data elsewhere:

- CLM sends rendered state, instructions and criteria to the one embeddings URL in `DECIDEALOT_CLM_EMBEDDINGS_URL`, with `DECIDEALOT_CLM_EMBEDDINGS_API_KEY` as its Bearer token when set.
- Jev sends the complete `state` and `questions` to `https://api.typesafe.ai/v1/systemone`, with `DECIDEALOT_TYPESAFE_API_KEY` as its Bearer token. The model catalog comes from `https://api.typesafe.ai/v1/models`.

Use them only when that data may leave your host. No request can name another upstream URL, a provider executable, a filesystem path or a key: callers choose a model selector and nothing else. Outbound provider calls do not follow redirects and ignore proxy environment variables.

At startup, the local providers download their pinned revisions from Hugging Face into `/models`. The CLM checkpoint is checked against a pinned SHA-256 before it loads. After that check each provider runs with Hugging Face offline mode on.

## Container hardening

The images run as non-root `1000:1000` unless you pass `--user`. The documented `docker run` and Compose recipes add a read-only root filesystem, `--cap-drop ALL`, `no-new-privileges`, `noexec` tmpfs mounts for `/tmp` and `/var/run`, a PID limit, memory and CPU limits, log rotation, and a loopback publish. The CUDA image needs one writable, executable tmpfs at `/var/cache` for Triton's compiled helpers; everything else stays read-only and `noexec`. Mount only the directory reserved for model bundles at `/models`.

## Request limits and logs

Request bodies above `DECIDEALOT_MAX_REQUEST_BYTES` (default 1 MiB) return `413` before they reach a model. Responses carry `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY` and `Referrer-Policy: no-referrer`.

Logs are JSON lines on stderr and in `DECIDEALOT_LOG_FILE`. Decidealot logs provider names, status codes and error types, not request bodies. The log formatter replaces any field whose name contains password, token, secret, API key, authorization or cookie with `[REDACTED]`.
