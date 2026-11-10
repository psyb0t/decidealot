# decidealot

Instructions for AI coding agents (Codex, Claude Code and others) working in this repository. Human contributors can read it too; the user documentation is the site under `docs/`, published at https://psyb0t.github.io/decidealot/.

## Agent setup

`AGENTS.md` is the source of these instructions. `CLAUDE.md` is a committed symlink to `AGENTS.md`, and `.claude` is a committed symlink to `.agents`, so Claude Code reads the same instructions, rules and skills as Codex. Claude's Edit and Write tools refuse to write through a symlink, so edit `AGENTS.md` and `.agents/rules/*.md` directly.

## What it is

Decidealot is a Docker image that serves typed System One decisions (`choice`, `score`, `noul`) through one TypeSafe-compatible HTTP API and an MCP Streamable HTTP endpoint on port `8080`. A request names a model selector. Local selectors run Laya, Von or a CLM projection head inside the container; hosted selectors forward to TypeSafe's Jev API. Decidealot returns answers with probabilities and never acts on them; the caller applies thresholds.

## Architecture

- `src/decidealot/main.py` loads `Settings`, configures JSON logging and runs uvicorn on `create_app()`.
- `src/decidealot/settings.py`: every `DECIDEALOT_*` variable through pydantic-settings. The model validator resolves the `auto` values of `clm_enabled` and `jev_enabled`, refuses an enabled CLM without an embeddings URL or an enabled Jev without a key, refuses a deployment with no provider, and checks `DECIDEALOT_DEVICE` against the variant baked into `/etc/decidealot/image-variant`.
- `src/decidealot/app.py`: the FastAPI app. Routes `/health`, `/v1/models`, `/v1/models/unload`, `/v1/systemone`, `/v1/systemone/batch`; exception handlers map the typed errors in `errors.py` to `422`, `400`, `401`, `409` and `503`; the MCP app is mounted at `/` behind `BearerASGI` with DNS rebinding protection.
- `src/decidealot/mcp_server.py`: the four MCP tools (`system_one`, `system_one_batch`, `list_models`, `unload_models`). Each calls the same `DecisionService` method as its REST route.
- `src/decidealot/decisions.py`: `DecisionService`. Parses the request, resolves the route, validates `config`, then runs it under the per-model lock, the local lane (serial on CUDA, CLM exclusive unless `clm_parallel_with_local_models`), the supervisor lease and the optional batch semaphore. It also refreshes the hosted catalog with a 60 second TTL.
- `src/decidealot/providers.py`: `ModelRouter` (public selector to fixed backend), `HTTPProviderClient` (no redirects, no proxy environment), `CLMRequestConfig`, and `native_provider_payload`, which turns a TypeSafe request into the native body each provider accepts.
- `src/decidealot/typesafe.py`: pydantic models that mirror the TypeSafe OpenAPI document, the batch wrapper, the `{"detail": [...]}` validation envelope and response projection.
- `src/decidealot/supervisor.py`: `ProviderSupervisor`. At startup it runs `provider_entrypoint.py prepare <name>` for each enabled local provider; at request time it starts the provider as a child process on loopback (`8011` Laya, `8012` Von, `8013` CLM), waits for its `/health`, evicts the least recently used idle provider at the resident limit, and stops idle providers after the configured timeout.
- `src/decidealot/provider_entrypoint.py` and `src/decidealot/clm_runtime.py` run inside `/opt/torch-venv`, not the app venv. The Dockerfiles copy them to `/opt/decidealot/`. They download pinned Hugging Face revisions into `/models/<provider>`, check required files (and the CLM checkpoint SHA-256), switch to offline mode and serve the native model.
- `src/decidealot/security.py`: bearer check with `hmac.compare_digest`, request ID middleware, the `Content-Length` limit and security headers. `src/decidealot/logging_config.py`: JSON logs to stderr and a rotating file, with request scope and key-based redaction.

Data flow: REST route or MCP tool -> `DecisionService` -> `ModelRouter` -> local lane and `ProviderSupervisor.acquire` (local only) -> `HTTPProviderClient.forward` to `127.0.0.1:801x` or `https://api.typesafe.ai/v1/systemone` -> `project_system_one_response` -> caller.

## Stack

- Python 3.12, FastAPI, uvicorn, httpx, pydantic and pydantic-settings, the MCP Python SDK 2.x, python-json-logger. Pinned with `==` in `pyproject.toml` and locked in `uv.lock` under the `exclude-newer` age gate.
- Model runtimes: PyTorch 2.14 from the `psyb0t/torchbase` base images, Laya, von-sdk and huggingface_hub, installed with `--require-hashes` from `requirements-providers-cpu.txt` or `requirements-providers-cuda.txt`.
- Lint: ruff, pyright (strict), mypy (strict), bandit, shellcheck. Tests: pytest with pytest-asyncio and pytest-cov, 90 percent branch coverage minimum.
- Images: `psyb0t/decidealot:latest` (CPU, `Dockerfile`) and `:latest-cuda` (CUDA 12.6, `Dockerfile.cuda`), both `linux/amd64`, running as `1000:1000` by default.

## Layout

| Path | Contents |
| --- | --- |
| `src/decidealot/` | The application package, plus the two files that run in the Torch venv. |
| `tests/` | Unit and contract tests with behavioral doubles in `conftest.py`. |
| `tests/integration/` | Provider-process integration tests (`-m integration`) and the real-image scripts `e2e_local_models.sh` and `e2e_clm.sh`. |
| `tests/real/` | Opt-in live hosted Jev test (`-m real`). |
| `scripts/` | `test-coverage.sh`, `sec.sh` (bandit and pip-audit to SARIF), `generate.py`, `audit-compose.sh`. |
| `Dockerfile`, `Dockerfile.cuda` | Production images. |
| `Dockerfile.dev` | The development toolchain image every lint and test target runs in. |
| `docker-compose.yml`, `docker-compose.cuda.yml` | Hardened local Compose service and its CUDA override. |
| `requirements-providers-*.in`, `requirements-providers-*.txt` | Model runtime inputs and their hash-locked output. |
| `server.json` | MCP Registry entry. |
| `.agents/skills/decidealot/`, `.agents/plugins/decidealot/` | The agent skill and the OpenClaw stdio bridge, published to ClawHub. |
| `.agents/.claude-plugin/`, `.agents/.codex-plugin/` | Plugin manifests for the psyb0t marketplace. |
| `.agents/rules/` | Rules for every change. |
| `docs/` | The documentation site (Jekyll, just-the-docs), published by GitHub Pages from `main`. |
| `.github/workflows/pipeline.yml` | CI: lint, coverage, security scan, generate check, image build, tag promotion, release, publishing. |
| `.env.example` | Every runtime setting the Compose file passes. |

## Commands

Docker and Make are enough. Every lint and test target builds `Dockerfile.dev` and runs inside it.

```bash
make help            # every target
make lint            # ruff, pyright, mypy, bandit, shellcheck
make test            # unit and integration tests
make test-coverage   # what CI runs: tests with the 90 percent gate, writes coverage-percent.txt
make sec             # bandit and pip-audit, writes sec.sarif
make generate        # what CI runs last; fails CI if it changes a tracked file
make build           # CPU image as psyb0t/decidealot:local, :v<version> and :latest
make build-cuda      # CUDA image
make build-test      # build, then check the CPU image's runtime metadata
make test-real       # build, then real HTTP requests against downloaded Laya and Von
make test-real-clm   # build, then the real CLM head against a mock embeddings server
make docs            # build the documentation site into .testing/docs-site
```

`make test-real-jev` needs `DECIDEALOT_TYPESAFE_API_KEY` in the environment and calls the paid TypeSafe API. Never run it unless asked.

## Project-specific landmines

- `provider_entrypoint.py` and `clm_runtime.py` run under `/opt/torch-venv/bin/python` and cannot import `decidealot`. Their model repositories, revisions and the CLM digest are repeated there on purpose. A change to a pinned revision or digest updates both `constants.py` and `provider_entrypoint.py`.
- Bundle preparation runs in the app lifespan before uvicorn binds port `8080`, so nothing answers, not even `/health`, until every enabled local bundle is prepared. On a fresh `/models` directory that is a large download. A failed preparation raises out of the lifespan and the process exits.
- `DecisionService` never runs two CUDA inferences at once and keeps CLM exclusive by default, whatever `DECIDEALOT_MAX_RESIDENT_LOCAL_PROVIDERS` says. Resident slots and execution lanes are separate controls.
- Local selectors win over hosted names: `DecisionService._refresh_hosted_catalog` drops any TypeSafe model whose name collides with a local selector.
- `Settings` reads `/etc/decidealot/image-variant` when it exists. Tests run outside the image, so the file is absent there.
- `.env.example` is copied as-is into Compose `.env` files. Keep every value in it valid for `Settings`.

## Rules

Rules for every change: `.agents/rules/`. Read the ones that match what you are touching before editing.
