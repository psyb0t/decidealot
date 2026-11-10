# Testing

Prefix: TEST. How to run each suite is in `AGENTS.md` and `docs/development.md`.

- **TEST1:** Unit and contract tests in `tests/` use the doubles in `tests/conftest.py` (`LifecycleSupervisor`, fake provider clients). They never download a model, call TypeSafe or need credentials.
- **TEST2:** `make test-coverage` is what CI runs. It runs everything not marked `real`, including `tests/integration/`, and fails under 90 percent branch coverage. Keep it deterministic and offline.
- **TEST3:** `tests/integration/` launches real local provider processes against stub servers and is marked `integration`. Supervisor lifecycle changes need a test there.
- **TEST4:** Tests that need real weights, a GPU, Docker images or a paid API (`make test-real`, `make test-real-cuda`, `make test-real-clm`, `make test-real-jev`) stay out of CI and run only on request.
- **TEST5:** A bug fix comes with a test that fails without it. A contract change updates `tests/test_typesafe_contract.py`, `tests/test_api.py` or `tests/test_mcp.py`.
