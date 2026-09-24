# Decidealot

[![CI](https://github.com/psyb0t/decidealot/actions/workflows/pipeline.yml/badge.svg?branch=main)](https://github.com/psyb0t/decidealot/actions/workflows/pipeline.yml)
[![coverage](https://raw.githubusercontent.com/psyb0t/decidealot/badges/coverage.svg)](https://github.com/psyb0t/decidealot/actions/workflows/pipeline.yml)
[![version](https://raw.githubusercontent.com/psyb0t/decidealot/badges/version.svg)](https://github.com/psyb0t/decidealot/releases)
[![license](https://raw.githubusercontent.com/psyb0t/decidealot/badges/license.svg)](LICENSE)
[![Docker Pulls](https://img.shields.io/docker/pulls/psyb0t/decidealot?style=flat-square)](https://hub.docker.com/r/psyb0t/decidealot)

Run Laya and Von decision models on your own machine through the TypeSafe System One HTTP API. No hosted model bill. No response parser held together with duct tape.

At startup Decidealot downloads and verifies both local model bundles. It then loads only the model selected by a request, unloads it after the configured idle period, and returns typed `choice`, `score`, and `noul` answers with model probabilities. It exposes the TypeSafe HTTP API and MCP Streamable HTTP from the same local container.

## Contents

- [Quick start](#quick-start)
- [Use the API](#use-the-api)
- [Use MCP](#use-mcp)
- [Pick a model](#pick-a-model)
- [Configuration](#configuration)
- [CUDA](#cuda)
- [Model storage and unloading](#model-storage-and-unloading)
- [Agent integrations](#agent-integrations)
- [Docs](#docs)

## Quick start

You need Docker. This starts the CPU image on loopback, stores downloaded model files in one narrow host directory, and gives the container no capabilities or writable root filesystem.

```bash
model_directory="$HOME/.local/share/decidealot/models"
sudo install --directory --owner=10001 --group=10001 "$model_directory"

docker run --detach --name decidealot --init --restart unless-stopped \
  --read-only --cap-drop ALL --security-opt no-new-privileges:true \
  --pids-limit 512 --memory 8g --cpus 4 \
  --tmpfs /tmp:rw,noexec,nosuid,size=128m \
  --tmpfs /var/run:rw,noexec,nosuid,size=8m \
  --log-driver json-file --log-opt max-size=10m --log-opt max-file=5 \
  --mount type=bind,source="$model_directory",target=/models \
  --publish 127.0.0.1:8080:8080 \
  psyb0t/decidealot:v0.3.0
```

Check the service, then ask Laya to make one typed decision:

```bash
curl --fail http://127.0.0.1:8080/health

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

The first service startup downloads both pinned model bundles and can take several minutes. Wait for `/health` before sending a decision. Later service starts reuse the same host directory. The response includes `answers.handling.choice` and a probability per choice key. Your caller chooses what to do with that decision, for example only allowing `allow` when its probability meets your own threshold.

## Use the API

Send raw state and questions. Do not send a decision you already made and expect Decidealot to repeat it back.

| Endpoint | What it does |
| --- | --- |
| `POST /v1/systemone` | Runs the selected model against `state` and returns typed answers. |
| `GET /v1/models` | Lists every supported alias and the model behind it. |
| `POST /v1/models/unload` | Stops all providers. |
| `/mcp` | Version 2 MCP Streamable HTTP, with `system_one`, `list_models`, and `unload_models` tools. |
| `GET /health` | Confirms that Decidealot can manage providers. It does not load one. |

`choice` questions need named `criteria`. `score` questions need an ordered criteria array whose position is the score. `noul` questions return a probability between zero and one. [The API guide](docs/api.md) has the request rules, response shape, validation failures, aliases, authentication, and lifecycle behavior.

## Use MCP

The same container serves MCP Streamable HTTP at `http://127.0.0.1:8080/mcp`. There is no second port, separate model lifecycle, or alternate input shape. Use an MCP client that supports Streamable HTTP. The `system_one` tool takes the same required `model`, `state`, and `questions` fields as `POST /v1/systemone`. `list_models` returns the live catalog. `unload_models` releases every idle local runtime.

Point an MCP client at that exact URL. Its configuration format varies, but the connection values are always equivalent to this:

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

Omit the `Authorization` header only when `DECIDEALOT_API_KEY` is empty. The MCP tools return structured output matching the HTTP result bodies, so an agent can inspect probabilities before it chooses the next action. A client that only supports local stdio can use the optional OpenClaw bridge described in [Agent integrations](#agent-integrations). [The API guide](docs/api.md#mcp-streamable-http) has tool inputs, output shapes, session behavior, and failure behavior.

## Pick a model

Every request must name a selector. `GET /v1/models` returns the same catalog at runtime.

| Selector | What it runs | Pick it when |
| --- | --- | --- |
| `laya`, `laya-auto`, `laya-latest` | Laya with automatic checkpoint routing. | State may arrive in more than one language or script. |
| `laya-english` | Laya's English checkpoint. | State is English and uses the Latin script. |
| `laya-multilingual` | Laya's multilingual checkpoint. | State is in another language or script, including short Latin-script text that is not clearly English. |
| `laya-typed-decisions` | Laya's checkpoint tuned for structured workflow decisions. | Your workload looks like repeated policy, routing, triage, or approval decisions. Validate it on your own cases first. |
| `von`, `von-latest`, `von-1.1`, `von-1.1.0` | The local English-only Von 1.1 model. | You want Von's independent result for a short, well-posed decision, or want to compare it with Laya before standardizing a workflow. |

### What differs

Laya is one model family with three checkpoints. Its automatic selectors choose the English or multilingual checkpoint from the input script and a language heuristic. That is the sensible default for mixed traffic. Automatic routing does not reliably identify every short Latin-script message, so pin `laya-multilingual` when those messages are known to be non-English. `laya-typed-decisions` is a separate checkpoint for structured decision work. It is not a general upgrade over the other two.

Von is a separate English-only decision model. It is strongest on short questions with clear criteria. Do not treat it as a long-document legal or policy reasoner. Neither model writes prose or executes an action. Both receive the same TypeSafe `state` and `questions` shape and return typed `choice`, `score`, and `noul` answers with probabilities. Your application owns the threshold and the action that follows.

Decidealot keeps one provider resident. Moving between Laya selectors stays in the Laya provider. Moving between Laya and Von waits for active work, releases the old provider and its Torch memory, then starts the other one.

Decidealot does not host TypeSafe Jev, so `jev`, `jev-latest`, and other Jev selectors return `422`. By default, an unused provider is also stopped after 600 seconds.

## Configuration

Pass configuration with `--env-file` or your container manager. The image uses fixed internal ports. Docker port publishing controls where the service is reachable.

| Variable | Default | Meaning |
| --- | --- | --- |
| `DECIDEALOT_API_KEY` | empty | Optional Bearer token for every public API and MCP request. |
| `DECIDEALOT_MAX_REQUEST_BYTES` | `1048576` | Maximum JSON request body size. |
| `DECIDEALOT_PROVIDER_IDLE_UNLOAD_SECONDS` | `600` | Idle time before automatic unload. Set `0` to disable only timeout-based unloads. |

The container always stores bundles under `/models`. Its only model storage setting is the host directory mounted there. Keep the loopback bind for one-host use. Before putting Decidealot behind a proxy, tunnel, or public address, set `DECIDEALOT_API_KEY` to a real secret and require `Authorization: Bearer <your-key>` from every caller.

## CUDA

`psyb0t/decidealot:v0.3.0-cuda` uses CUDA 12.6 and needs a compatible NVIDIA driver, NVIDIA Container Toolkit, and `--gpus all`. CUDA images are amd64-only. The CPU image is the right default unless inference speed and model memory justify the GPU setup.

```bash
docker run --detach --name decidealot --init --restart unless-stopped \
  --gpus all --read-only --cap-drop ALL --security-opt no-new-privileges:true \
  --pids-limit 512 --memory 8g --cpus 4 \
  --tmpfs /tmp:rw,noexec,nosuid,size=128m \
  --tmpfs /var/run:rw,noexec,nosuid,size=8m \
  --tmpfs /var/cache:rw,exec,nosuid,nodev,size=512m,uid=10001,gid=10001,mode=0755 \
  --log-driver json-file --log-opt max-size=10m --log-opt max-file=5 \
  --mount type=bind,source="$model_directory",target=/models \
  --publish 127.0.0.1:8080:8080 \
  psyb0t/decidealot:v0.3.0-cuda
```

CUDA needs one writable executable cache because Triton compiles and loads short-lived CUDA helpers there. The rest of the container remains read-only and `noexec`. [Deployment](docs/deployment.md) has the complete CPU, CUDA, authentication, persistent-storage, and host-directory recipes.

## Model storage and unloading

Mount one narrow host directory at `/models`. Decidealot creates and manages `/models/laya` and `/models/von` inside it. Make the host directory writable by the container's fixed UID and GID `10001`. Do not mount your home directory, Docker socket, or a broad host path just to save five minutes.

Unload the loaded runtime when you are done with it:

```bash
curl --fail --request POST http://127.0.0.1:8080/v1/models/unload
```

Only one provider can be loaded, but the endpoint reports both providers so the result is clear. Unload terminates the provider process, so it releases model weights, Torch allocations, worker threads, and the CUDA context. A later request starts it again.

## Agent integrations

Install the Decidealot skill from the psyb0t marketplace after the release that contains it. The skill tells an agent how to deploy the Docker image, choose Laya or Von, submit TypeSafe decisions, read probabilities, use direct MCP, and use the stdio bridge only when its client needs one.

```bash
claude plugin marketplace add psyb0t/agents
claude plugin install decidealot@psyb0t

codex plugin marketplace add psyb0t/agents
codex plugin add decidealot@psyb0t
```

OpenClaw can install the skill or its stdio bridge. The bridge connects to a Decidealot container you already run. It does not start Docker or download models itself.

```bash
openclaw skills install @psyb0t/decidealot
openclaw plugins install clawhub:@psyb0t/decidealot
```

## Docs

| Doc | What it covers |
| --- | --- |
| [API](docs/api.md) | TypeSafe request and response shapes, errors, authentication, aliases, and provider lifecycle. |
| [Deployment](docs/deployment.md) | Hardened Docker commands, CUDA, persistent model directories, and exposure rules. |
| [Changelog](CHANGELOG.md) | User-visible changes by version. |

## License

Decidealot code is WTFPL. Laya, Von, PyTorch, Transformers, and each downloaded model keep their own upstream licenses. Read those before putting a model into a commercial product.
