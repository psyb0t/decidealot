# API contract

Prefix: API. Covers `src/decidealot/app.py`, `mcp_server.py`, `decisions.py` and `typesafe.py`.

- **API1:** `POST /v1/systemone`, `GET /v1/models` and the request and response models in `typesafe.py` mirror TypeSafe's published OpenAPI document. Do not add a response field, rename one, or change a question or answer shape. Provider-only fields are dropped by `project_system_one_response`.
- **API2:** Request validation failures, including an unknown model selector, return `422` with the `{"detail": [...]}` envelope built by `validation_error_content`. Every other failure uses Decidealot's `{"code", "message", "details"}` envelope from `_error_response`. Keep the codes in `constants.py` (`UNAUTHORIZED`, `PROVIDER_BUSY`, `REQUEST_TOO_LARGE`, `PROVIDER_UNAVAILABLE`, `INVALID_REQUEST`).
- **API3:** REST and MCP share one `DecisionService`. A new operation gets a method there, then a REST route and an MCP tool that both call it, so validation, routing and scheduling stay identical. MCP tools raise `ToolError` through `_run_tool`; a validation failure carries the same JSON envelope as text.
- **API4:** `config` is flat and provider-specific. Validate it in `validate_provider_config` before a provider is acquired or any batch item runs. Unknown keys and out-of-range values return `422`; never ignore a setting silently. Hosted Jev never receives `config`.
- **API5:** A batch validates every item, then runs. Results keep input order, and any failure fails the whole batch without partial results. `POST /v1/systemone/batch` and `system_one_batch` are Decidealot extensions, not TypeSafe endpoints.
- **API6:** Callers choose a model selector, never a URL, host, path, executable or key. Every backend URL is a constant in `constants.py`.
- **API7:** A change to a route, tool, field, status code or error message updates `docs/api.md` or `docs/mcp.md`, and the skill reference in `.agents/skills/decidealot/references/setup.md` when the agent workflow changes, in the same change.
