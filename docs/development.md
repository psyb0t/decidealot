---
title: "Development"
description: "Build decidealot from source: the containerized toolchain, every Make target, the test suites, dependency locks, the CI pipeline and releases."
nav_order: 11
---

# Development

You need Docker and Make. Every lint and test target builds the toolchain image from [`Dockerfile.dev`](https://github.com/psyb0t/decidealot/blob/main/Dockerfile.dev) and runs inside it with your user ID and the checkout mounted at `/work`, so nothing is installed on the host. `make help` lists every target. [AGENTS.md](https://github.com/psyb0t/decidealot/blob/main/AGENTS.md) and [`.agents/rules/`](https://github.com/psyb0t/decidealot/tree/main/.agents/rules) hold the rules for changes.

## Checks

```bash
make lint            # ruff, pyright, mypy, bandit, shellcheck
make format          # ruff format
make test            # unit tests, then integration tests
make test-coverage   # all tests not marked real, 90 percent branch coverage gate
make sec             # bandit and pip-audit, written to sec.sarif
make generate        # regenerate owned artifacts; there are none today
```

`make test-coverage` is what CI runs. It covers the unit tests in [`tests/`](https://github.com/psyb0t/decidealot/tree/main/tests) and the provider-process tests in [`tests/integration/`](https://github.com/psyb0t/decidealot/tree/main/tests/integration), writes the total to `coverage-percent.txt` for the badge, and fails below 90 percent. None of these tests download a model or need credentials.

## Tests with real models

These build images or call real services, so CI never runs them:

| Target | What it does |
| --- | --- |
| `make test-real` | Builds the CPU image, then sends real HTTP requests to downloaded Laya and Von weights. |
| `make test-real-cuda` | The same with the CUDA image and a GPU. |
| `make test-real-clm` | Builds the CPU image and runs the real CLM head against a strict mock embeddings server. Needs the Docker socket. |
| `make test-real-jev` | One live hosted Jev decision. Needs `DECIDEALOT_TYPESAFE_API_KEY` in your environment and costs a TypeSafe call. |
| `make build-test`, `make build-test-cuda` | Build an image and check its user, variant metadata, Torch version and provider commands. |

## Images and local runs

```bash
make build           # psyb0t/decidealot:local, :v<version> and :latest
make build-cuda      # the same tags with -cuda
make run             # build, then docker compose up -d
make run-cuda        # build the CUDA image, then Compose with the CUDA override
make status          # docker compose ps
make stop            # docker compose down
```

`make run` needs `DECIDEALOT_MODEL_DIRECTORY` in `.env`; see [Compose](deployment.md#compose).

## Dependencies

Application dependencies are pinned with `==` in [`pyproject.toml`](https://github.com/psyb0t/decidealot/blob/main/pyproject.toml) and locked in `uv.lock`. Change them only through the Make targets, which apply the `DEPENDENCY_CUTOFF` age gate from the Makefile:

```bash
make pkg-add PKG=name==1.2.3
make pkg-update PKG=name==1.2.4
make pkg-remove PKG=name
make pkg-upgrade
```

Model runtime dependencies live in `requirements-providers-cpu.in` and `requirements-providers-cuda.in`. `make model-lock` compiles them into the hash-locked `.txt` files the images install. `make dep` refreshes both kinds.

## Documentation

The site is built from `docs/` with the same Jekyll image GitHub Pages uses:

```bash
make docs
```

The output goes to `.testing/docs-site`. Every page needs front matter with a unique `title` and a `description`. Link pages to each other by relative `.md` path and link repository files by full `https://github.com/psyb0t/decidealot/blob/main/...` URL.

## CI

[`.github/workflows/pipeline.yml`](https://github.com/psyb0t/decidealot/blob/main/.github/workflows/pipeline.yml) runs on every push:

| Job | Runs on | What it does |
| --- | --- | --- |
| `code` | Every branch push | `make lint`, `make test-coverage`, `make sec` and `make generate`. Uploads the SARIF report and fails if `make generate` changes a tracked file. |
| `call-docker-image-workflow` | Pushes to `main` | Builds both images for `linux/amd64` and pushes `:latest`, `:latest-cuda` and `:sha-<commit>` tags, then scans them with Grype. |
| `badges` | `main` and tags | Updates the coverage, version and license badges on the `badges` branch. |
| `wait` | `v*` tags | Waits for the `main` run of the tagged commit to succeed. |
| `docker-promote` | `v*` tags | Points the release tags at the images that `main` run built, without rebuilding. |
| `release` | `v*` tags | Creates the GitHub Release. |
| `publish-to-clawhub` | `v*` tags | Publishes the `decidealot` skill and the `decidealot` OpenClaw bridge. |
| `publish-to-mcp-registry` | `v*` tags | Publishes `server.json` with the release version. |

## Releases

A release bumps `version` in `pyproject.toml` and `.agents/.codex-plugin/plugin.json`, adds a [`CHANGELOG.md`](https://github.com/psyb0t/decidealot/blob/main/CHANGELOG.md) entry, and tags `vX.Y.Z` on a commit that is already on `main`. `make version` prints the current tag.
