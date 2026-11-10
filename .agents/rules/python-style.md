# Python style

Prefix: PY. Applies to `src/`, `tests/` and `scripts/`.

- **PY1:** `make lint` must pass: ruff (rules `E`, `F`, `I`, `UP`, `B`, `S`, `ASYNC`, 100 columns), pyright strict, mypy strict, bandit and shellcheck.
- **PY2:** Shared values live in `constants.py`.
- **PY3:** Errors are the typed classes in `errors.py`. Their messages are returned to callers, so they never carry upstream bodies, keys or paths.
- **PY4:** Configuration is read once in `Settings`. Every new setting is a `DECIDEALOT_*` field with bounds, added to `.env.example`, `docker-compose.yml` when Compose should pass it, and `docs/configuration.md`.
- **PY6:** Application dependencies change through `make pkg-add`, `make pkg-update` or `make pkg-remove`, which respect the `DEPENDENCY_CUTOFF` age gate.
