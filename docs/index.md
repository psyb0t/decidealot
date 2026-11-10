---
title: "Home"
description: "decidealot runs Laya, Von, CLM or hosted Jev behind one TypeSafe-compatible HTTP and MCP endpoint and returns typed answers with probabilities."
nav_order: 1
permalink: /
seo:
  type: WebSite
---

# decidealot

Your hardware or TypeSafe's hosted Jev. Decidealot is a Docker image that runs Laya, Von or a CLM projection head on your machine, or forwards to TypeSafe's hosted Jev, behind one TypeSafe-compatible HTTP API and an MCP Streamable HTTP endpoint on the same port.

You send a `state` and one or more named questions. Each question is a `choice` (pick one label), a `score` (an expected position on an ordered rubric) or a `noul` (the probability that a statement is true). Decidealot returns typed answers with probabilities. It never acts on them. Your application decides what a `0.94` means.

```bash
model_directory="$HOME/.local/share/decidealot/models"
mkdir --parents "$model_directory"

docker run --detach --name decidealot --init --restart unless-stopped \
  --user "$(id -u):$(id -g)" \
  --read-only --cap-drop ALL --security-opt no-new-privileges:true \
  --tmpfs /tmp:rw,noexec,nosuid,size=128m \
  --tmpfs /var/run:rw,noexec,nosuid,size=8m \
  --mount type=bind,source="$model_directory",target=/models \
  --publish 127.0.0.1:8080:8080 \
  psyb0t/decidealot:latest
```

The first start downloads the pinned Laya and Von bundles into that directory, which takes a few minutes. Once `curl --fail http://127.0.0.1:8080/health` succeeds, ask for a decision:

```bash
curl --fail http://127.0.0.1:8080/v1/systemone \
  --header 'Content-Type: application/json' \
  --data '{
    "model": "laya",
    "state": "A proposed action would permanently delete protected data.",
    "questions": {
      "handling": {
        "type": "choice",
        "instructions": "Choose the required handling for this action.",
        "criteria": {
          "allow": "The action is reversible and does not affect protected data.",
          "require_review": "The action is irreversible or affects protected data."
        }
      }
    }
  }'
```

[Deployment](deployment.md) has the hardened commands with resource limits, CUDA and Compose.

## What's in the box

| Part | What it does |
| --- | --- |
| Local providers | Laya (three checkpoints with automatic routing), Von 1.1 and the CLM v0.1 projection head. Each runs as its own child process, started on first use and stopped when idle. |
| Hosted provider | TypeSafe's Jev, enabled by a TypeSafe API key. Requests go to `https://api.typesafe.ai`; nothing loads locally. |
| HTTP API | `POST /v1/systemone`, `POST /v1/systemone/batch`, `GET /v1/models`, `POST /v1/models/unload`, `GET /health`. |
| MCP | Streamable HTTP at `/mcp` with `system_one`, `system_one_batch`, `list_models` and `unload_models`. |
| Images | `psyb0t/decidealot:latest` (CPU) and `:latest-cuda` (CUDA 12.6), both `linux/amd64`, non-root by default. |
| Agent integrations | A skill for Claude Code, Codex and OpenClaw, and an OpenClaw stdio bridge for clients without remote MCP. |

## Where to go next

| You want to | Read |
| --- | --- |
| Choose between Laya, Von, CLM and Jev | [Models](models.md) |
| Write requests and read answers | [HTTP API](api.md) |
| Connect an MCP client | [MCP](mcp.md) |
| See every environment variable | [Configuration](configuration.md) |
| Run it for real, on CPU or CUDA | [Deployment](deployment.md) |
| Put it behind a proxy or on a network | [Security](security.md) |
| Fix a startup or request failure | [Troubleshooting](troubleshooting.md) |
| Give an agent the skill or the stdio bridge | [Agent integrations](agent-integrations.md) |
| Understand the processes and request path | [Architecture](architecture.md) |
| Change the code and run the checks | [Development](development.md) |

Decidealot code is WTFPL ([LICENSE](https://github.com/psyb0t/decidealot/blob/main/LICENSE)). Laya, Von, CLM, PyTorch, Transformers and each downloaded model keep their own upstream licenses. Read those before putting a model into a commercial product.
