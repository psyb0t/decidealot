---
title: "Troubleshooting"
description: "Startup refusals, failed model downloads, 503, 409, 413, 421 and 422 responses from decidealot, with the exact message, cause and fix for each."
nav_order: 8
---

# Troubleshooting

Start with the logs. Decidealot writes JSON lines to stderr, so `docker logs decidealot` shows them. Each line has `time`, `level`, `msg` and structured fields such as `provider`, `status_code` and `request_id`. Set `DECIDEALOT_LOG_LEVEL=DEBUG` for health-check and routing detail.

## Startup refusals

Configuration is checked before anything else. An invalid value stops the process with a pydantic `ValidationError` for `Settings` that names the field, and the container exits.

| Message | Cause | Fix |
| --- | --- | --- |
| `DECIDEALOT_CLM_EMBEDDINGS_URL is required when CLM is enabled` | `DECIDEALOT_CLM_ENABLED=true` without a URL. | Set the URL, or set `DECIDEALOT_CLM_ENABLED=auto` or `false`. |
| `DECIDEALOT_TYPESAFE_API_KEY is required when Jev is enabled` | `DECIDEALOT_JEV_ENABLED=true` without a key. | Supply the key, or set `DECIDEALOT_JEV_ENABLED=auto` or `false`. |
| `at least one provider must be enabled` | Laya and Von are disabled, and neither CLM nor Jev is configured. | Enable a local provider or configure CLM or Jev. |
| `DECIDEALOT_DEVICE must match the installed image variant` | `DECIDEALOT_DEVICE` differs from `DECIDEALOT_IMAGE_VARIANT`. | Remove both overrides; each image sets them. Use the `-cuda` image for CUDA. |
| `DECIDEALOT_IMAGE_VARIANT must match the installed image variant` | The variant override disagrees with the image. | Remove the override. |
| `must be an absolute HTTP URL`, `must not contain credentials`, `must not contain a query or fragment` | `DECIDEALOT_CLM_EMBEDDINGS_URL` is malformed. | Use a plain `http://` or `https://` URL to the `/v1/embeddings` path and put the token in `DECIDEALOT_CLM_EMBEDDINGS_API_KEY`. |
| `must be a non-empty comma-separated allowlist` | An MCP allowlist is empty or has an empty entry, such as a trailing comma. | Remove the variable to use the default, or list every value. |

Numeric settings outside their range fail the same way with pydantic's own message, such as `Input should be less than or equal to 3`. [Configuration](configuration.md) lists every range.

## The model download fails or never finishes

Before it listens, Decidealot prepares every enabled local bundle in turn and logs `preparing local provider bundle` and `local provider bundle prepared` for each. Nothing answers on port `8080` during that time, so `curl` reports a refused connection and Docker reports the container as `starting`. The image healthcheck allows 900 seconds before it counts failures.

If preparation fails, Decidealot logs `local provider bundle preparation failed` with the provider and return code, and the process exits. The provider's own error is printed just above it:

| Error | Fix |
| --- | --- |
| `configured model directory must be writable to download missing files` | The host directory mounted at `/models` is not writable by the container user. Create it as the user you pass with `--user`, or fix its ownership. |
| `create local model directory for <provider>` or `local model directory for <provider> must be readable` | Same cause, at the provider subdirectory. |
| `download model repository <repository>@<revision>` or `download pinned CLM projection head` | Hugging Face could not be reached or refused the download. Check outbound HTTPS from the container, then restart it. Files already downloaded are kept. |
| `Laya bundle is missing required files: ...` or `Von model is missing required files: ...` | The download finished without every required file. Delete that provider's subdirectory under the model directory and restart. |
| `CLM checkpoint digest does not match the pinned release` | The CLM file on disk is not the pinned release, even after a forced download. Delete `clm/` under the model directory and restart. |

A Jev-only deployment downloads nothing and starts in seconds.

## 503 PROVIDER_UNAVAILABLE

The `message` field says which case it is.

| Message | Cause and fix |
| --- | --- |
| `<provider> provider did not become ready before the timeout` | The local provider process did not answer its health check within `DECIDEALOT_PROVIDER_START_TIMEOUT_SECONDS`. Loading a model on a slow disk or a busy host can take longer; raise the timeout. |
| `<provider> provider stopped before becoming ready` | The provider process exited while loading. The provider's traceback is in the logs just above. Common causes are memory limits (`--memory`) and a missing GPU on the CUDA image. |
| `start <provider> provider` | The provider command could not be executed. This points at a damaged image; pull it again. |
| `the selected provider timed out` | One call took longer than `DECIDEALOT_REQUEST_TIMEOUT_SECONDS`. |
| `the selected provider is unavailable` | The provider closed the connection or answered with a `5xx`. For CLM this includes an embeddings endpoint that is down, times out after `DECIDEALOT_CLM_EMBEDDINGS_TIMEOUT_SECONDS`, rejects the request or returns vectors of the wrong width. |
| `the selected provider returned an invalid response` | The provider answered with a body that does not match the TypeSafe response shape, or with an unexpected status. For Jev this includes a rejected `DECIDEALOT_TYPESAFE_API_KEY`. |
| `the hosted model catalog is unavailable` or `the hosted model catalog is invalid` | TypeSafe's `GET /v1/models` failed, rejected the key, or returned something unexpected. Local selectors keep working. Check the key and TypeSafe's status. |
| `local providers are not ready` | The service is shutting down. |

## 409 PROVIDER_BUSY

`POST /v1/models/unload` and the `unload_models` tool refuse to unload anything while a local provider is answering a request, with `a local provider is processing a request`. Retry when the request finishes. Decisions never return `409`; they wait for a free slot instead.

## 413 REQUEST_TOO_LARGE

The `Content-Length` of the request is above `DECIDEALOT_MAX_REQUEST_BYTES`. Split a large batch or raise the limit, up to `16777216`.

## 422 validation errors

The body is the TypeSafe `{"detail": [...]}` envelope, and `loc` points at the field.

| `msg` | Fix |
| --- | --- |
| `Value error, unknown model '<name>'; use a name returned by GET /v1/models` | The selector is misspelled or its provider is disabled. Call `GET /v1/models`. |
| `The selected provider does not support this setting` | Laya, Von and Jev take no `config` keys. Remove `config` or send `{}`. |
| `Value error, rendered CLM text exceeds the configured UTF-8 byte limit` | A CLM state plus instructions, or one criterion, is longer than `DECIDEALOT_CLM_MAX_TEXT_BYTES`. Shorten it or raise the limit. |
| `List should have at most <n> items` | The batch is larger than `DECIDEALOT_MAX_BATCH_REQUESTS`. |

## MCP answers 421 or 403

`421` means the request's `Host` header is not in `DECIDEALOT_MCP_ALLOWED_HOSTS`; `403` means its browser `Origin` is not in `DECIDEALOT_MCP_ALLOWED_ORIGINS`. Both happen behind a proxy or tunnel that uses a public name. Add the exact name as shown in [Expose MCP through a proxy](mcp.md#expose-mcp-through-a-proxy), and keep the loopback defaults in the list.

## 401 UNAUTHORIZED

`DECIDEALOT_API_KEY` is set and the request has no `Authorization: Bearer <key>` header or a different key. MCP clients need the same header in their server configuration. `/health` never needs it.
