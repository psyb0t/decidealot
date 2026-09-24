# @psyb0t/decidealot

This bridge lets a stdio-only MCP client reach a self-hosted [Decidealot](https://github.com/psyb0t/decidealot) container. Decidealot already serves Streamable HTTP at `/mcp`. The bridge uses `mcp-remote` to forward stdio traffic to that endpoint and attach the optional Bearer token.

Run Decidealot first, then point the bridge at it. The bridge forwards stdio traffic to the running service.

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

If a client supports remote Streamable HTTP directly, connect it to `$DECIDEALOT_URL/mcp`. Add `Authorization: Bearer <token>` when the server requires it. MCP accepts loopback `Host` headers, so connect from the same host or through a loopback-preserving tunnel.

## License

MIT. See [LICENSE](LICENSE).
