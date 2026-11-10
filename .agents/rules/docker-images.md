# Docker images

Prefix: IMG. Covers `Dockerfile`, `Dockerfile.cuda`, `Dockerfile.dev` and the Compose files.

- **IMG1:** The Torch base image comes from the `TORCHBASE_IMAGE` build argument the Makefile passes.
- **IMG2:** The app installs with `uv sync --frozen --no-dev --no-editable` into `/opt/app-venv`. Model runtimes install with `--require-hashes` into `/opt/torch-venv`. Keep the two environments separate.
- **IMG3:** `Dockerfile` and `Dockerfile.cuda` stay in step apart from the variant: the requirements file, the `DECIDEALOT_DEVICE` and `DECIDEALOT_IMAGE_VARIANT` values, `/etc/decidealot/image-variant`, and the CUDA cache directories. `make build-test` and `make build-test-cuda` check those.
- **IMG4:** Both images run as `1000:1000`, expose `8080`, keep the `/health` healthcheck and carry the `io.modelcontextprotocol.server.name` label that `server.json` relies on.
- **IMG5:** CI builds `linux/amd64` only, for both images. Adding a platform changes `build_targets` in both pipeline jobs.
- **IMG6:** `.dockerignore` keeps `docs/`, `tests/`, `.agents/`, caches and secrets out of the build context.
