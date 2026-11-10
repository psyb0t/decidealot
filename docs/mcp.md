---
title: "MCP"
description: "Decidealot's MCP Streamable HTTP endpoint at /mcp: client setup, the system_one, system_one_batch, list_models and unload_models tools, and proxy allowlists."
nav_order: 4
---

# MCP

The container also exposes MCP Streamable HTTP at `http://127.0.0.1:8080/mcp`, on the same port as the [HTTP API](api.md). It shares the decision service, public model aliases, request validation, body limit, Bearer authentication, and request ID rules with the TypeSafe-compatible HTTP endpoints. Local models use the provider supervisor; hosted Jev does not. The server answers with plain JSON responses rather than an SSE stream.

## Connect a client

MCP clients differ in configuration syntax, but they need one Streamable HTTP server URL and the same optional Bearer header:

```json
{
  "mcpServers": {
    "decidealot": {
      "url": "http://127.0.0.1:8080/mcp",
      "headers": {
        "Authorization": "Bearer your-token-here"
      }
    }
  }
}
```

When authentication is disabled, omit `headers`. When it is enabled, an absent or wrong token gets the normal HTTP `401` envelope before an MCP session starts. MCP clients perform initialization and retain the `Mcp-Session-Id`. A client that only supports local stdio can use the OpenClaw bridge described in [Agent integrations](agent-integrations.md).

## Tools

| Tool | Input | Structured output |
| --- | --- | --- |
| `system_one` | Required `model`, `state`, and `questions`, plus optional `config`, exactly as described in [Make decisions](api.md#make-decisions). | The same `model`, `answers`, and `usage` object returned by `POST /v1/systemone`. |
| `system_one_batch` | Required nonempty `requests` list of complete `model`, `state`, and `questions` objects, each with its own optional `config`. | The same ordered `{ "results": [...] }` object returned by `POST /v1/systemone/batch`. |
| `list_models` | None. | The same `{ "models": [...] }` catalog returned by `GET /v1/models`. |
| `unload_models` | None. | The same unload status and provider list returned by `POST /v1/models/unload`. |

For a `system_one` call, use this MCP tool argument shape. The `questions` value has the same `choice`, `score`, and `noul` rules as the HTTP endpoint.

```json
{
  "model": "laya-typed-decisions",
  "state": {
    "operation": "delete",
    "reversible": false
  },
  "questions": {
    "handling": {
      "type": "choice",
      "criteria": {
        "allow": "The operation is reversible and authorized.",
        "review": "A human must review the operation first."
      }
    }
  }
}
```

Call `list_models` and `unload_models` with an empty argument object, `{}`. The tools return structured output matching the HTTP result bodies, so an agent can inspect probabilities before it chooses the next action.

## Errors

MCP tool validation, unknown model, provider busy, and provider unavailable failures return `isError: true`. A validation error includes the TypeSafe `{"detail":[...]}` body in its text content. Other failures carry the same message the HTTP API returns, such as `the selected provider is unavailable`. No tool accepts a provider URL, executable, filesystem path, model directory, or arbitrary runtime option.

## Expose MCP through a proxy

The MCP server keeps DNS rebinding protection enabled. Loopback clients and containers using the `decidealot` Docker service name work by default. A proxy, tunnel, or public DNS name must be allowed explicitly. Add the exact public `Host` value to `DECIDEALOT_MCP_ALLOWED_HOSTS`. Browser-based MCP clients must also add their exact origin, including the scheme, to `DECIDEALOT_MCP_ALLOWED_ORIGINS`.

For a proxy that publishes `https://mcp.example.net/mcp`, put these values in the Compose `.env` file before `docker compose up -d`, or pass the same variables with `docker run --env`:

```dotenv
DECIDEALOT_API_KEY=replace-with-a-real-secret
DECIDEALOT_MCP_ALLOWED_HOSTS=127.0.0.1,127.0.0.1:*,localhost,localhost:*,[::1],[::1]:*,decidealot,decidealot:*,mcp.example.net
DECIDEALOT_MCP_ALLOWED_ORIGINS=http://127.0.0.1:*,http://localhost:*,http://[::1]:*,https://mcp.example.net
```

Setting either variable replaces its default, so keep the loopback entries in the list. Keep `--publish 127.0.0.1:8080:8080` when a local reverse proxy terminates TLS. The proxy forwards the request unchanged with `Host: mcp.example.net`. Invalid or missing bearer credentials return `401` first. After bearer authentication, Decidealot returns `421` for an untrusted host and `403` for an untrusted browser origin. Do not disable this protection or allow a broad wildcard for an internet-facing endpoint.
