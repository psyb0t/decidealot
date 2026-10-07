# Decidealot

[![CI](https://github.com/psyb0t/decidealot/actions/workflows/pipeline.yml/badge.svg?branch=main)](https://github.com/psyb0t/decidealot/actions/workflows/pipeline.yml)
[![coverage](https://raw.githubusercontent.com/psyb0t/decidealot/badges/coverage.svg)](https://github.com/psyb0t/decidealot/actions/workflows/pipeline.yml)
[![version](https://raw.githubusercontent.com/psyb0t/decidealot/badges/version.svg)](https://github.com/psyb0t/decidealot/releases)
[![license](https://raw.githubusercontent.com/psyb0t/decidealot/badges/license.svg)](LICENSE)
[![Docker Pulls](https://img.shields.io/docker/pulls/psyb0t/decidealot?style=flat-square)](https://hub.docker.com/r/psyb0t/decidealot)

Your hardware or TypeSafe's hosted Jev. Run Laya, Von, a CLM projection head, or Jev through one TypeSafe-compatible HTTP and MCP endpoint.

At startup Decidealot downloads and verifies every enabled local bundle. It then loads only the provider selected by a request, unloads it after the configured idle period, and returns typed `choice`, `score`, and `noul` answers with model probabilities. CLM adds a small local projection head over one configured Qwen3-8B embeddings endpoint. It exposes the TypeSafe HTTP API and MCP Streamable HTTP from the same local container.

## Contents

- [Quick start](#quick-start)
- [Use hosted Jev](#use-hosted-jev)
- [Use the API](#use-the-api)
- [Use MCP](#use-mcp)
- [Expose MCP through a proxy](#expose-mcp-through-a-proxy)
- [Pick a model](#pick-a-model)
- [Configuration](#configuration)
- [CLM embeddings and caching](#clm-embeddings-and-caching)
- [CUDA](#cuda)
- [Model storage and unloading](#model-storage-and-unloading)
- [Agent integrations](#agent-integrations)
- [Docs](#docs)

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

## Use hosted Jev

Set `DECIDEALOT_TYPESAFE_API_KEY` in a private environment file or secret store. Jev enables automatically when the key is present. Call `GET /v1/models` and select a hosted name returned by TypeSafe, such as `jev-latest`. Decidealot caches the authenticated TypeSafe catalog for 60 seconds and refreshes it on the next model-listing or hosted-model request after expiry. New hosted model names need no Decidealot release. It sends the complete `state` and `questions` to TypeSafe's fixed HTTPS API, not to a local model. The key never belongs in the request body, a tracked file, or a client-side application.

For a Jev-only container, disable all local models so startup downloads no model bundles. Pass the upstream key as an environment variable from your secret store, then use the same REST or MCP endpoint shown below:

```bash
docker run --detach --name decidealot --init --restart unless-stopped \
  --read-only --cap-drop ALL --security-opt no-new-privileges:true \
  --tmpfs /tmp:rw,noexec,nosuid,size=128m \
  --env DECIDEALOT_TYPESAFE_API_KEY \
  --env DECIDEALOT_LAYA_ENABLED=false \
  --env DECIDEALOT_VON_ENABLED=false \
  --publish 127.0.0.1:8080:8080 \
  psyb0t/decidealot:latest
```

The variable must already be set in the shell or supplied by your container manager. `GET /v1/models` confirms which Jev selectors are enabled. The [API guide](docs/api.md#hosted-jev) has a complete request and response example. TypeSafe charges for its API calls; model probabilities still need your own action thresholds.

## Use the API

Send the state to judge and a bounded question. Decidealot returns typed results with probabilities.

| Endpoint | What it does |
| --- | --- |
| `POST /v1/systemone` | Runs the selected local model or hosted Jev against `state` and returns typed answers. |
| `POST /v1/systemone/batch` | Runs independent System One requests and returns an ordered `results` list. |
| `GET /v1/models` | Lists every supported alias and the model behind it. |
| `POST /v1/models/unload` | Stops loaded local providers; hosted Jev is unaffected. |
| `/mcp` | MCP Streamable HTTP, with `system_one`, `system_one_batch`, `list_models`, and `unload_models` tools. |
| `GET /health` | Reports whether configured local bundles are ready. Jev-only startup has no bundle download. |

`choice` questions need named `criteria`. `score` questions need an ordered criteria array whose position is the score. `noul` questions return a probability between zero and one. [The API guide](docs/api.md) has the request rules, response shape, validation failures, aliases, authentication, and lifecycle behavior.

Put the subject in `state`, the question and decision rules in each question's `instructions`, and explicit descriptions in `criteria`. A label like `review` does not explain what should be reviewed. For `noul`, write the statement whose probability you want in `instructions`.

CLM accepts optional flat request configuration. The selected `model` already identifies which settings apply:

```json
{
  "model": "clm",
  "config": {"temperature": 0.8},
  "state": "The operation permanently removes protected data.",
  "questions": {
    "review": {
      "type": "noul",
      "instructions": "This operation requires human review because it removes protected data."
    }
  }
}
```

CLM temperature defaults to `1` and accepts finite numbers greater than `0` and at most `100`. Lower values sharpen its probability distribution; they do not make the answer more correct. Laya, Von, and hosted Jev currently accept only omitted or empty `config`. Unsupported keys and values return `422`, never silently disappear. The same config belongs in MCP tool arguments or each individual batch request. [Request configuration](docs/api.md#request-configuration) covers the contract.

## Use MCP

The same container serves MCP Streamable HTTP at `http://127.0.0.1:8080/mcp`. The `system_one` tool takes the same `model`, `state`, and `questions` fields as `POST /v1/systemone`. `system_one_batch` takes a `requests` list, each item with those same fields. `list_models` returns the live catalog. `unload_models` releases local model memory. Direct loopback clients and containers using the `decidealot` Docker service name work by default.

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

## Expose MCP through a proxy

The MCP server keeps DNS-rebinding protection enabled. A proxy, tunnel, or public DNS name must be allowed explicitly. Add the exact public `Host` value to `DECIDEALOT_MCP_ALLOWED_HOSTS`. Browser-based MCP clients must also add their exact origin, including the scheme, to `DECIDEALOT_MCP_ALLOWED_ORIGINS`.

For a proxy that publishes `https://mcp.example.net/mcp`, put these values in the Compose `.env` file before `docker compose up -d`, or pass the same variables with `docker run --env`:

```dotenv
DECIDEALOT_API_KEY=replace-with-a-real-secret
DECIDEALOT_MCP_ALLOWED_HOSTS=127.0.0.1,127.0.0.1:*,localhost,localhost:*,[::1],[::1]:*,decidealot,decidealot:*,mcp.example.net
DECIDEALOT_MCP_ALLOWED_ORIGINS=http://127.0.0.1:*,http://localhost:*,http://[::1]:*,https://mcp.example.net
```

Keep `--publish 127.0.0.1:8080:8080` when a local reverse proxy terminates TLS. The proxy forwards the request unchanged with `Host: mcp.example.net`. After bearer authentication, Decidealot returns `421` for an untrusted host and `403` for an untrusted browser origin. Invalid or missing bearer credentials return `401` first. Do not disable this protection or allow a broad wildcard for an internet-facing endpoint.

## Pick a model

Every request must name a selector. `GET /v1/models` returns the same catalog at runtime.

| Selector | What it runs | Pick it when |
| --- | --- | --- |
| `laya`, `laya-auto`, `laya-latest` | Laya with automatic checkpoint routing. | State may arrive in more than one language or script. |
| `laya-english` | Laya's English checkpoint. | State is English and uses the Latin script. |
| `laya-multilingual` | Laya's multilingual checkpoint. | State is in another language or script, including short Latin-script text that is not clearly English. |
| `laya-typed-decisions` | Laya's checkpoint tuned for structured workflow decisions. | Your workload looks like repeated policy, routing, triage, or approval decisions. Validate it on your own cases first. |
| `von`, `von-latest`, `von-1.1`, `von-1.1.0` | The local English-only Von 1.1 model. | You want Von's independent result for a short, well-posed decision, or want to compare it with Laya before standardizing a workflow. |
| `clm`, `clm-latest`, `clm-0.1`, `clm-0.1-8b` | The local CLM v0.1 projection head over one configured Qwen3-8B embeddings endpoint. | You have a trusted embeddings service that emits CLM-compatible 4096-wide last-token Qwen3-8B vectors. |
| Names returned by TypeSafe, such as `jev-latest` | TypeSafe's hosted Jev API. | You have a TypeSafe API key and intend to send the state and questions to TypeSafe. |

### What differs

Laya is one model family with three checkpoints. Its automatic selectors choose English or multilingual checkpoints from the input script and a language heuristic. Use `laya-multilingual` for known non-English short Latin-script messages. `laya-typed-decisions` targets repeated structured decision work.

Von is a separate English-only decision model for short questions with clear criteria. CLM is a local 75 MB projection head, not a text encoder. It calls one configured OpenAI-compatible `/v1/embeddings` URL and requires its configured model to return Qwen3-8B last-token vectors with exactly 4096 float values. All providers take the same TypeSafe `state` and `questions` shape and return typed `choice`, `score`, and `noul` answers with probabilities. Your application applies the threshold and action that follow.

Decidealot keeps one local provider resident by default. Set `DECIDEALOT_MAX_RESIDENT_LOCAL_PROVIDERS=2` or `3` only when the host has enough RAM or VRAM to hold that many processes. A batch can name more local model families than the resident limit; those calls wait for a slot and switch models as needed. Repeated requests to one underlying model run sequentially. Local CUDA models also run sequentially, even with multiple resident slots. CLM and its Qwen embeddings calls share the local serial lane by default, including on CPU. Set `DECIDEALOT_CLM_PARALLEL_WITH_LOCAL_MODELS=true` only when that workload can safely overlap other local models, such as when Qwen runs on another machine. Hosted Jev can overlap local work. A Jev call uses HTTPS and does not load, switch, or unload a local provider.

Set `DECIDEALOT_PROVIDER_IDLE_UNLOAD_SECONDS` to choose when an idle provider releases its model and Torch memory.

## Configuration

Pass configuration with `--env-file` or your container manager. The image uses fixed internal ports. Docker port publishing controls where the service is reachable.

| Variable | Default | Meaning |
| --- | --- | --- |
| `DECIDEALOT_API_KEY` | empty | Optional Bearer token for every public API and MCP request. |
| `DECIDEALOT_MAX_REQUEST_BYTES` | `1048576` | Maximum JSON request body size. |
| `DECIDEALOT_MAX_BATCH_REQUESTS` | `0` | Optional batch item cap. `0` means no item-count cap; the body-size limit still applies. |
| `DECIDEALOT_MAX_BATCH_CONCURRENCY` | `0` | Optional cap on simultaneous batch provider calls. `0` leaves only model and device scheduling limits. |
| `DECIDEALOT_PROVIDER_IDLE_UNLOAD_SECONDS` | `600` | Idle time before automatic unload. Set `0` to disable only timeout-based unloads. |
| `DECIDEALOT_MAX_RESIDENT_LOCAL_PROVIDERS` | `1` | Maximum resident local provider processes, from `1` to `3`. CUDA inference remains serial regardless of this setting. |
| `DECIDEALOT_LAYA_ENABLED` | `true` | Download and expose Laya selectors. |
| `DECIDEALOT_VON_ENABLED` | `true` | Download and expose Von selectors. |
| `DECIDEALOT_CLM_ENABLED` | `auto` | Enable CLM when its embeddings URL is set. Use `true` to require it or `false` to skip it. |
| `DECIDEALOT_CLM_EMBEDDINGS_URL` | empty | Exact OpenAI-compatible `/v1/embeddings` URL used only by CLM. Required when CLM is enabled. |
| `DECIDEALOT_CLM_EMBEDDINGS_MODEL` | `qwen3-8b` | Model selector sent to the embeddings endpoint. It must produce Qwen3-8B last-token vectors with width `4096`. |
| `DECIDEALOT_CLM_EMBEDDINGS_API_KEY` | empty | Optional Bearer token sent only to the configured CLM embeddings endpoint. |
| `DECIDEALOT_CLM_EMBEDDINGS_TIMEOUT_SECONDS` | `120` | One CLM embeddings request timeout in seconds. |
| `DECIDEALOT_CLM_CANDIDATE_CACHE_ENTRIES` | `1024` | Candidate embedding LRU capacity, `0` to `4096`. `0` disables caching. |
| `DECIDEALOT_CLM_CANDIDATE_CACHE_TTL_SECONDS` | `600` | Candidate vector lifetime, greater than `0` and at most `86400` seconds. |
| `DECIDEALOT_CLM_MAX_TEXT_BYTES` | `8192` | UTF-8 byte limit per rendered state plus instructions or candidate, `1` to `1048576`. Over-limit CLM inputs return `422`. |
| `DECIDEALOT_CLM_PARALLEL_WITH_LOCAL_MODELS` | `false` | Permit CLM and its Qwen encoder to overlap other local models. Keep false when they share this host's resources. |
| `DECIDEALOT_TYPESAFE_API_KEY` | empty | Private upstream Bearer key for TypeSafe's hosted Jev API. Separate from the caller-facing `DECIDEALOT_API_KEY`. |
| `DECIDEALOT_JEV_ENABLED` | `auto` | Enable Jev when the upstream key is set. `true` requires a key; `false` hides Jev even with a key. |
| `DECIDEALOT_MCP_ALLOWED_HOSTS` | loopback names and `decidealot` | Comma-separated `Host` values accepted by MCP. Add each reverse-proxy hostname here. |
| `DECIDEALOT_MCP_ALLOWED_ORIGINS` | loopback HTTP origins | Comma-separated browser origins accepted by MCP. Add each public browser origin here. |

The container always stores local bundles under `/models`. Its only model storage setting is the host directory mounted there. To run only CLM, set `DECIDEALOT_LAYA_ENABLED=false`, `DECIDEALOT_VON_ENABLED=false`, and configure `DECIDEALOT_CLM_EMBEDDINGS_URL`. To run only Jev, disable Laya and Von, leave CLM unconfigured, and supply `DECIDEALOT_TYPESAFE_API_KEY`; no `/models` mount is needed. CLM's embeddings endpoint and TypeSafe's Jev API receive decision state and criteria, so use them only when that data may leave your host. Keep the loopback bind for one-host use. Before putting Decidealot behind a proxy, tunnel, or public address, set `DECIDEALOT_API_KEY` to a real secret, require `Authorization: Bearer <your-key>` from every caller, and configure the precise MCP host and origin allowlists above.

## CLM embeddings and caching

CLM normalizes encoder vectors before applying its projection heads. Repeated candidate texts reuse a bounded process-local cache; raw decision state is never cached. Unloading CLM clears that cache. `usage.input_tokens` reports only tokens charged by the embeddings calls actually made. The byte limit is not a token limit: configure your encoder to reject inputs beyond its context instead of silently truncating. A quantized Qwen encoder is not equivalent to upstream's full-precision encoder, and matching vector width alone does not establish equivalent accuracy.

## CUDA

`psyb0t/decidealot:latest-cuda` uses CUDA 12.6 and needs a compatible NVIDIA driver, NVIDIA Container Toolkit, and `--gpus all`. CUDA images are amd64-only. The CPU image is the right default unless inference speed and model memory justify the GPU setup.

```bash
model_directory="${model_directory:-$HOME/.local/share/decidealot/models}"
runtime_uid=$(id -u)
runtime_gid=$(id -g)
mkdir --parents "$model_directory"

docker run --detach --name decidealot --init --restart unless-stopped \
  --user "$runtime_uid:$runtime_gid" \
  --gpus all --read-only --cap-drop ALL --security-opt no-new-privileges:true \
  --pids-limit 512 --memory 8g --cpus 4 \
  --tmpfs /tmp:rw,noexec,nosuid,size=128m \
  --tmpfs /var/run:rw,noexec,nosuid,size=8m \
  --tmpfs /var/cache:rw,exec,nosuid,nodev,size=512m,uid=$runtime_uid,gid=$runtime_gid,mode=0755 \
  --log-driver json-file --log-opt max-size=10m --log-opt max-file=5 \
  --mount type=bind,source="$model_directory",target=/models \
  --publish 127.0.0.1:8080:8080 \
  psyb0t/decidealot:latest-cuda
```

CUDA needs one writable executable cache because Triton compiles and loads short-lived CUDA helpers there. The rest of the container remains read-only and `noexec`. [Deployment](docs/deployment.md) has the complete CPU, CUDA, authentication, persistent-storage, and host-directory recipes.

## Model storage and unloading

Mount one narrow host directory at `/models`. Decidealot creates and manages `/models/laya`, `/models/von`, and, when enabled, `/models/clm` inside it. The Docker commands run as your current host UID and GID, so a directory you create yourself is writable without an image-specific `chown`. The image falls back to non-root `1000:1000` only when no runtime user is supplied.

Unload the loaded local runtime when you are done with it:

```bash
curl --fail --request POST http://127.0.0.1:8080/v1/models/unload
```

The endpoint reports each configured local provider and stops every loaded idle provider. It refuses to unload any while a local request is active. Stopping a provider process releases its model weights, Torch allocations, worker threads, and CUDA context. It does not affect TypeSafe's hosted Jev. A later local request starts its provider again.

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

## Docs

| Doc | What it covers |
| --- | --- |
| [API](docs/api.md) | TypeSafe request and response shapes, errors, authentication, aliases, and provider lifecycle. |
| [Deployment](docs/deployment.md) | Hardened Docker commands, CUDA, persistent model directories, and exposure rules. |
| [Changelog](CHANGELOG.md) | User-visible changes by version. |

## License

Decidealot code is WTFPL. Laya, Von, CLM, PyTorch, Transformers, and each downloaded model keep their own upstream licenses. Read those before putting a model into a commercial product.
