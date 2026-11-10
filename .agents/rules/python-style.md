# Python style

Prefix: PY. Applies to `src/`, `tests/` and `scripts/`.

- **PY1:** `make lint` must pass: ruff (rules `E`, `F`, `I`, `UP`, `B`, `S`, `ASYNC`, 100 columns), pyright strict, mypy strict, bandit and shellcheck. No `|| true`, no blanket `# noqa` or `# type: ignore`. A narrow suppression names the rule and carries a comment saying why, as `subprocess.Popen(  # noqa: S603  # nosec B603` does in `supervisor.py`.
- **PY2:** Shared values live in `constants.py`. Module-private values are `_snake_case` module constants, as in `providers.py`. No magic strings or numbers inline.
- **PY3:** Errors are the typed classes in `errors.py`. Their messages are safe to return to callers; never put upstream bodies, keys or paths in them.
- **PY4:** Configuration is read once in `Settings`. Every new setting is a `DECIDEALOT_*` field with bounds, added to `.env.example`, `docker-compose.yml` when Compose should pass it, and `docs/configuration.md`.
- **PY5:** Log through `logging.getLogger(__name__)` with a short lowercase message and structured `extra` fields. Never `print`.
- **PY6:** Application dependencies are pinned with `==` and changed through `make pkg-add`, `make pkg-update` or `make pkg-remove`, which respect the `DEPENDENCY_CUTOFF` age gate. Never edit `uv.lock` by hand.
- **PY7:** `make format` runs `ruff format`. Keep formatter output as is.
