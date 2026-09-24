# @psyb0t/decidealot

This bridge lets a stdio-only MCP client reach a self-hosted [Decidealot](https://github.com/psyb0t/decidealot) container. Decidealot already serves Streamable HTTP at `/mcp`. The bridge uses `mcp-remote` to forward stdio traffic to that endpoint and attach the optional Bearer token.

It does not ship Laya or Von, start Docker, choose a model, or act on a decision. Run Decidealot first, then point the bridge at it.

## Configuration

| Environment variable | Required | Description |
| --- | --- | --- |
| `DECIDEALOT_URL` | yes | Base URL of a running Decidealot instance, such as `http://127.0.0.1:8080`. The bridge appends `/mcp`. |
| `DECIDEALOT_API_KEY` | no | Bearer token. Set it whenever the container uses `DECIDEALOT_API_KEY`. |

## Install

```bash
openclaw plugins install clawhub:@psyb0t/decidealot
export DECIDEALOT_URL=http://127.0.0.1:8080
export DECIDEALOT_API_KEY=your-token-here
```

The service exposes `system_one`, `list_models`, and `unload_models`. `system_one` uses the same `model`, `state`, and `questions` arguments as the REST API. `list_models` and `unload_models` take no arguments. Read the [Decidealot API guide](https://github.com/psyb0t/decidealot/blob/main/docs/api.md#mcp-streamable-http) for input, structured output, and MCP error shapes.

## Native remote MCP

If a client supports remote Streamable HTTP directly, skip this bridge and connect to `$DECIDEALOT_URL/mcp`. Add `Authorization: Bearer <token>` only when the server requires it. The endpoint is loopback-only, so use it from the same host or through a tunnel that preserves a loopback `Host` header. Do not route it through a reverse proxy.

## License

MIT. See [LICENSE](LICENSE).
