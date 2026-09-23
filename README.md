# Decidealot

[![CI](https://github.com/psyb0t/decidealot/actions/workflows/pipeline.yml/badge.svg?branch=main)](https://github.com/psyb0t/decidealot/actions/workflows/pipeline.yml)
[![coverage](https://raw.githubusercontent.com/psyb0t/decidealot/badges/coverage.svg)](https://github.com/psyb0t/decidealot/actions/workflows/pipeline.yml)
[![version](https://raw.githubusercontent.com/psyb0t/decidealot/badges/version.svg)](https://github.com/psyb0t/decidealot/releases)
[![license](https://raw.githubusercontent.com/psyb0t/decidealot/badges/license.svg)](LICENSE)
[![Docker Pulls](https://img.shields.io/docker/pulls/psyb0t/decidealot?style=flat-square)](https://hub.docker.com/r/psyb0t/decidealot)

Run Laya and Von decision models on your own machine through the TypeSafe System One HTTP API. No hosted model bill. No response parser held together with duct tape.

Decidealot starts a model only when a request selects it, stops it after the configured idle period, and returns typed `choice`, `score`, and `noul` answers with their model probabilities. It exposes the TypeSafe-compatible `POST /v1/systemone` and `GET /v1/models` endpoints, plus explicit unload endpoints for reclaiming model and Torch memory.

## Quick start

You need Docker. This starts the CPU image on loopback, keeps downloaded model files in a named volume, and gives the container no capabilities or writable root filesystem.

```bash
docker volume create decidealot-model-data

docker run --detach --name decidealot --init --restart unless-stopped \
  --read-only --cap-drop ALL --security-opt no-new-privileges:true \
  --pids-limit 512 --memory 8g --cpus 4 \
  --tmpfs /tmp:rw,noexec,nosuid,size=128m \
  --tmpfs /var/run:rw,noexec,nosuid,size=8m \
  --log-driver json-file --log-opt max-size=10m --log-opt max-file=5 \
  --mount type=volume,source=decidealot-model-data,target=/models \
  --publish 127.0.0.1:8080:8080 \
  psyb0t/decidealot:v0.1.0
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

The first request for a backend downloads its pinned model bundle and can take several minutes. Later calls reuse `/models`. The response includes `answers.handling.choice` and a probability per choice key. Your caller chooses what to do with that decision, for example only allowing `allow` when its probability meets your own threshold.

## Use the API

Send raw state and questions. Do not send a decision you already made and expect Decidealot to repeat it back.

| Endpoint | What it does |
| --- | --- |
| `POST /v1/systemone` | Runs the selected model against `state` and returns typed answers. |
| `GET /v1/models` | Lists every supported alias and the model behind it. |
| `POST /v1/models/{model}/unload` | Stops one selected provider. |
| `POST /v1/models/unload` | Stops all providers. |
| `GET /health` | Confirms that Decidealot can manage providers. It does not load one. |

`choice` questions need named `criteria`. `score` questions need an ordered criteria array whose position is the score. `noul` questions return a probability between zero and one. [The API guide](docs/api.md) has the request rules, response shape, validation failures, aliases, authentication, and lifecycle behavior.

## Pick a model

| Selector | Local model |
| --- | --- |
| `laya`, `laya-auto`, `laya-latest` | Laya with automatic checkpoint routing. |
| `laya-english`, `laya-multilingual`, `laya-typed-decisions` | A specific Laya checkpoint. |
| `von`, `von-latest`, `von-1.1`, `von-1.1.0` | Von 1.1. |
| `jev`, `jev-latest`, `jev-1`, `jev-1.0` | The configured default backend. |

Only one provider remains resident. Selecting another provider waits for any active call, stops the old process, then starts the requested one. By default, an unused provider is also stopped after 600 seconds.

## Configuration

Pass configuration with `--env-file` or your container manager. The image uses fixed internal ports. Docker port publishing controls where the service is reachable.

| Variable | Default | Meaning |
| --- | --- | --- |
| `DECIDEALOT_MODEL_DATA_DIR` | `/models` | Parent directory for downloaded Laya and Von bundles. |
| `DECIDEALOT_LAYA_MODEL_DIR` | empty | Optional absolute in-container Laya bundle directory. |
| `DECIDEALOT_VON_MODEL_DIR` | empty | Optional absolute in-container Von bundle directory. |
| `DECIDEALOT_DEFAULT_MODEL` | `laya` | Backend used by `jev-*` aliases. |
| `DECIDEALOT_API_KEY` | empty | Optional Bearer token for every public endpoint. |
| `DECIDEALOT_MAX_REQUEST_BYTES` | `1048576` | Maximum JSON request body size. |
| `DECIDEALOT_PROVIDER_IDLE_UNLOAD_SECONDS` | `600` | Idle time before automatic unload. Set `0` to disable only timeout-based unloads. |

Keep the loopback bind for one-host use. Before putting Decidealot behind a proxy, tunnel, or public address, set `DECIDEALOT_API_KEY` to a real secret and require `Authorization: Bearer <your-key>` from every caller.

## CUDA

`psyb0t/decidealot:v0.1.0-cuda` uses CUDA 12.6 and needs a compatible NVIDIA driver, NVIDIA Container Toolkit, and `--gpus all`. CUDA images are amd64-only. The CPU image is the right default unless inference speed and model memory justify the GPU setup.

```bash
docker run --detach --name decidealot --gpus all \
  --mount type=volume,source=decidealot-model-data,target=/models \
  --publish 127.0.0.1:8080:8080 \
  psyb0t/decidealot:v0.1.0-cuda
```

Use the hardening options from the CPU quick start here too. [Deployment](docs/deployment.md) has the complete CPU, CUDA, authentication, persistent-storage, and host-directory recipes.

## Model data and unloading

The named volume is the normal setup. To control where model files land, mount narrow writable directories at `/local-models/laya` and `/local-models/von`, set the matching `DECIDEALOT_*_MODEL_DIR`, and make them writable by the container's fixed UID and GID `10001`. Do not mount your home directory, Docker socket, or a broad host path just to save five minutes.

Unload one provider when you are done with it:

```bash
curl --fail --request POST http://127.0.0.1:8080/v1/models/laya/unload
```

Unload terminates the provider process, so it releases model weights, Torch allocations, worker threads, and the CUDA context. A later request starts it again.

## Docs

| Doc | What it covers |
| --- | --- |
| [API](docs/api.md) | TypeSafe request and response shapes, errors, authentication, aliases, and provider lifecycle. |
| [Deployment](docs/deployment.md) | Hardened Docker commands, CUDA, persistent model directories, and exposure rules. |
| [Changelog](CHANGELOG.md) | User-visible changes by version. |

## License

Decidealot code is WTFPL. Laya, Von, PyTorch, Transformers, and each downloaded model keep their own upstream licenses. Read those before putting a model into a commercial product.
