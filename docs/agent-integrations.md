---
title: "Agent integrations"
description: "Install the decidealot skill for Claude Code, Codex or OpenClaw, and the OpenClaw stdio bridge for MCP clients that cannot reach a remote endpoint."
nav_order: 9
---

# Agent integrations

An agent can reach Decidealot three ways: directly over [MCP](mcp.md) at `/mcp`, through REST with the skill's instructions, or through the OpenClaw stdio bridge when its client only speaks local stdio. All three need a Decidealot container you already run.

## The skill

The skill in [`.agents/skills/decidealot`](https://github.com/psyb0t/decidealot/tree/main/.agents/skills/decidealot) tells an agent how to deploy the Docker image, choose Laya, Von, CLM, or Jev, submit typed decisions, read probabilities, use direct MCP, and use the stdio bridge only when its client needs one. It treats a decision as evidence, not permission: the caller keeps the threshold and the final action.

Install it from the psyb0t marketplace after the release that contains it:

```bash
claude plugin marketplace add psyb0t/agents
claude plugin install decidealot@psyb0t

codex plugin marketplace add psyb0t/agents
codex plugin add decidealot@psyb0t
```

OpenClaw installs the same skill from ClawHub:

```bash
openclaw skills install @psyb0t/decidealot
```

The skill reads `DECIDEALOT_URL` (default `http://127.0.0.1:8080`) and, when the container has authentication on, `DECIDEALOT_API_KEY`.

## The OpenClaw stdio bridge

The bridge in [`.agents/plugins/decidealot`](https://github.com/psyb0t/decidealot/tree/main/.agents/plugins/decidealot) is for MCP clients that cannot connect to a remote Streamable HTTP server. It runs a pinned `mcp-remote`, which forwards stdio traffic to `$DECIDEALOT_URL/mcp` and adds the Bearer header when a key is set.

```bash
openclaw plugins install clawhub:@psyb0t/decidealot
export DECIDEALOT_URL=http://127.0.0.1:8080
export DECIDEALOT_API_KEY=your-token-here
```

| Environment variable | Required | Description |
| --- | --- | --- |
| `DECIDEALOT_URL` | yes | Base URL of a running Decidealot instance, such as `http://127.0.0.1:8080`. The bridge appends `/mcp`. |
| `DECIDEALOT_API_KEY` | no | Bearer token. Set it whenever the container uses `DECIDEALOT_API_KEY`. |

The bridge exits with an error naming `DECIDEALOT_URL` when that variable is missing. It exposes the same four tools as the server: `system_one`, `system_one_batch`, `list_models` and `unload_models`.

If a client supports remote Streamable HTTP directly, skip the bridge and point it at `$DECIDEALOT_URL/mcp` as shown in [Connect a client](mcp.md#connect-a-client).

## MCP Registry

Each release publishes [`server.json`](https://github.com/psyb0t/decidealot/blob/main/server.json) to the MCP Registry as `io.github.psyb0t/decidealot`. The entry points at the `docker.io/psyb0t/decidealot` image with Streamable HTTP at `http://localhost:8080/mcp`.
