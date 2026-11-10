# decidealot

[![CI](https://github.com/psyb0t/decidealot/actions/workflows/pipeline.yml/badge.svg?branch=main)](https://github.com/psyb0t/decidealot/actions/workflows/pipeline.yml)
[![coverage](https://raw.githubusercontent.com/psyb0t/decidealot/badges/coverage.svg)](https://github.com/psyb0t/decidealot/actions/workflows/pipeline.yml)
[![version](https://raw.githubusercontent.com/psyb0t/decidealot/badges/version.svg)](https://github.com/psyb0t/decidealot/releases)
[![license](https://raw.githubusercontent.com/psyb0t/decidealot/badges/license.svg)](https://github.com/psyb0t/decidealot/blob/main/LICENSE)
[![Docker Pulls](https://img.shields.io/docker/pulls/psyb0t/decidealot?style=flat-square)](https://hub.docker.com/r/psyb0t/decidealot)

Your hardware or TypeSafe's hosted Jev. Run Laya, Von, a CLM projection head, or Jev through one TypeSafe-compatible HTTP and MCP endpoint.

At startup Decidealot downloads and verifies every enabled local bundle. It then loads only the provider selected by a request, unloads it after the configured idle period, and returns typed `choice`, `score`, and `noul` answers with model probabilities. CLM adds a small local projection head over one configured Qwen3-8B embeddings endpoint. It exposes the TypeSafe HTTP API and MCP Streamable HTTP from the same local container.

## Contents

- [Documentation](#documentation)
- [Quick start](#quick-start)
- [Pick a model](#pick-a-model)
- [Use the API](#use-the-api)
- [Use MCP](#use-mcp)
- [Use hosted Jev](#use-hosted-jev)
- [CUDA](#cuda)
- [Agent integrations](#agent-integrations)
- [Development](#development)
- [Sponsor](#sponsor)
- [License](#license)

## Documentation

The full documentation lives at **[psyb0t.github.io/decidealot](https://psyb0t.github.io/decidealot/)**.

| Section | What's there |
| --- | --- |
| [Models](https://psyb0t.github.io/decidealot/models/) | Every selector, how Laya, Von, CLM and Jev differ, resident slots, execution lanes, CLM caching. |
| [HTTP API](https://psyb0t.github.io/decidealot/api/) | Request rules, `choice`, `score` and `noul`, request config, batches, unload, errors. |
| [MCP](https://psyb0t.github.io/decidealot/mcp/) | Client setup, the four tools, error shapes, proxy allowlists. |
| [Configuration](https://psyb0t.github.io/decidealot/configuration/) | Every `DECIDEALOT_*` variable with its default and range. |
| [Deployment](https://psyb0t.github.io/decidealot/deployment/) | Hardened CPU and CUDA commands, Jev-only, Compose, model storage, upgrades. |
| [Security](https://psyb0t.github.io/decidealot/security/) | Authentication, MCP host checks, data that leaves the host, container hardening. |
| [Troubleshooting](https://psyb0t.github.io/decidealot/troubleshooting/) | Startup refusals, failed downloads, and every error response with its fix. |
| [Agent integrations](https://psyb0t.github.io/decidealot/agent-integrations/) | The skill for Claude Code, Codex and OpenClaw, and the stdio bridge. |
| [Architecture](https://psyb0t.github.io/decidealot/architecture/) | Processes, startup, and the path one request takes. |
| [Development](https://psyb0t.github.io/decidealot/development/) | Make targets, tests, dependency locks, CI and releases. |

## Quick start

You need Docker. This starts the CPU image on loopback, stores downloaded model files in one narrow host directory, and gives the container no capabilities or writable root filesystem.

```bash
model_directory="$HOME/.local/share/decidealot/models"
runtime_uid=$(id -u)
runtime_gid=$(id -g)
mkdir --parents "$model_directory"

docker run --detach --name decidealot --init --restart unless-stopped \
  --user "$runtime_uid:$runtime_gid" \
  --read-only --cap-drop ALL --security-opt no-new-privileges:true \
  --pids-limit 512 --memory 8g --cpus 4 \
  --tmpfs /tmp:rw,noexec,nosuid,size=128m \
  --tmpfs /var/run:rw,noexec,nosuid,size=8m \
  --log-driver json-file --log-opt max-size=10m --log-opt max-file=5 \
  --mount type=bind,source="$model_directory",target=/models \
  --publish 127.0.0.1:8080:8080 \
  psyb0t/decidealot:latest
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

The first service startup downloads the enabled pinned model bundles and can take several minutes. The default configuration enables Laya and Von. Wait for `/health` before sending a decision. Later service starts reuse the same host directory.

An example response looks like this. The answer, probabilities, and token count vary with the model call:

```json
{
  "model": "laya",
  "answers": {
    "handling": {
      "type": "choice",
      "choice": "require_review",
      "confidence": 0.94,
      "probabilities": {"allow": 0.06, "require_review": 0.94}
    }
  },
  "usage": {"input_tokens": 91, "output_tokens": 0}
}
```

For that result, your application queues the action for human review instead of running it. Decidealot does not perform the action. Your caller checks the chosen label and applies its own probability threshold, for example allowing an action only when `choice` is `allow` and `probabilities.allow` is at least `0.95`.

## Pick a model

Every request must name a selector. `GET /v1/models` returns the selectors the running deployment accepts.

| Selectors | What runs |
| --- | --- |
| `laya`, `laya-auto`, `laya-latest`, `laya-english`, `laya-multilingual`, `laya-typed-decisions` | Laya, with automatic checkpoint routing or a named checkpoint. |
| `von`, `von-latest`, `von-1.1`, `von-1.1.0` | The local English-only Von 1.1 model. |
| `clm`, `clm-latest`, `clm-0.1`, `clm-0.1-8b` | The local CLM v0.1 projection head over one configured Qwen3-8B embeddings endpoint. |
| Names returned by TypeSafe, such as `jev-latest` | TypeSafe's hosted Jev API. |

Decidealot keeps one local provider resident by default and stops it after 600 idle seconds. [Models](https://psyb0t.github.io/decidealot/models/) says which selector to pick, how many models can stay loaded, and what runs in parallel.

## Use the API

Send the state to judge and a bounded question. Decidealot returns typed results with probabilities.

| Endpoint | What it does |
| --- | --- |
| `POST /v1/systemone` | Runs the selected local model or hosted Jev against `state` and returns typed answers. |
| `POST /v1/systemone/batch` | Runs independent System One requests and returns an ordered `results` list. |
| `GET /v1/models` | Lists every supported alias and the model behind it. |
| `POST /v1/models/unload` | Stops loaded local providers; hosted Jev is unaffected. |
| `GET /health` | Reports whether configured local bundles are ready. |

`choice` questions need named `criteria`. `score` questions need an ordered criteria array whose position is the score. `noul` questions return a probability between zero and one. Put the subject in `state`, the question and decision rules in each question's `instructions`, and explicit descriptions in `criteria`. CLM also accepts `"config": {"temperature": 0.8}`. The [HTTP API](https://psyb0t.github.io/decidealot/api/) page has the request rules, response shapes, validation failures, batches and errors.

Set `DECIDEALOT_API_KEY` before the port is reachable from anywhere but loopback, and send it as `Authorization: Bearer <key>`. [Configuration](https://psyb0t.github.io/decidealot/configuration/) lists every setting.

## Use MCP

The same container serves MCP Streamable HTTP at `http://127.0.0.1:8080/mcp`, with `system_one`, `system_one_batch`, `list_models`, and `unload_models` tools that take the same fields as the REST API.

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

Omit the `Authorization` header only when `DECIDEALOT_API_KEY` is empty. A proxy or tunnel must add its public host name to `DECIDEALOT_MCP_ALLOWED_HOSTS`. [MCP](https://psyb0t.github.io/decidealot/mcp/) covers the tools, errors and proxy setup.

## Use hosted Jev

Set `DECIDEALOT_TYPESAFE_API_KEY` in a private environment file or secret store. Jev enables automatically when the key is present. Call `GET /v1/models` and select a hosted name returned by TypeSafe, such as `jev-latest`. Decidealot sends the complete `state` and `questions` to TypeSafe's fixed HTTPS API, not to a local model, and TypeSafe charges for its API calls. To run Jev alone, disable Laya and Von; startup then downloads nothing. [Deployment](https://psyb0t.github.io/decidealot/deployment/#jev-only-deployment) has the command.

## CUDA

`psyb0t/decidealot:latest-cuda` uses CUDA 12.6 and needs a compatible NVIDIA driver, NVIDIA Container Toolkit, and `--gpus all`. CUDA images are amd64-only. It also needs one writable, executable tmpfs at `/var/cache` for Triton. The CPU image is the right default unless inference speed and model memory justify the GPU setup. [Deployment](https://psyb0t.github.io/decidealot/deployment/#cuda-deployment) has the complete command.

## Agent integrations

Install the Decidealot skill from the psyb0t marketplace after the release that contains it. The skill tells an agent how to deploy the Docker image, choose Laya, Von, CLM, or Jev, submit typed decisions, read probabilities, use direct MCP, and use the stdio bridge only when its client needs one.

```bash
claude plugin marketplace add psyb0t/agents
claude plugin install decidealot@psyb0t

codex plugin marketplace add psyb0t/agents
codex plugin add decidealot@psyb0t
```

OpenClaw can install the skill or its stdio bridge. The bridge forwards stdio traffic to a Decidealot container you already run.

```bash
openclaw skills install @psyb0t/decidealot
openclaw plugins install clawhub:@psyb0t/decidealot
```

## Development

You need Docker and Make; every check runs in a container.

```bash
make lint            # ruff, pyright, mypy, bandit, shellcheck
make test-coverage   # the offline test suite with the 90 percent gate
make build           # the CPU image
make docs            # the documentation site, into .testing/docs-site
```

[Development](https://psyb0t.github.io/decidealot/development/) lists every target, the real-model tests and the CI pipeline. User-visible changes are in the [changelog](https://github.com/psyb0t/decidealot/blob/main/CHANGELOG.md).

## Sponsor

<a href="https://www.buymeacoffee.com/psyb0t"><img src="https://cdn.buymeacoffee.com/buttons/v2/default-blue.png" alt="Buy Me a Coffee" height="60" width="217"></a>

## License

Decidealot code is WTFPL ([LICENSE](https://github.com/psyb0t/decidealot/blob/main/LICENSE)). Laya, Von, CLM, PyTorch, Transformers, and each downloaded model keep their own upstream licenses. Read those before putting a model into a commercial product.
