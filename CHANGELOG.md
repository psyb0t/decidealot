# Changelog

All notable changes per release. Versions follow [SemVer](https://semver.org/). Before 1.0, compatible additions use a minor version and fixes use a patch version. Breaking API or configuration changes are called out.

## v0.3.0

Added:

- Added MCP Streamable HTTP at `/mcp` with `system_one`, `list_models`, and `unload_models` tools. MCP shares the TypeSafe request contract, model supervisor, body limit, request ID handling, and optional Bearer authentication with REST.
- Added a distributable MCP registry manifest, an OCI registry identity label in both CPU and CUDA images, and tag-gated MCP registry publishing.
- Added Claude, Codex, and OpenClaw agent integrations that explain Docker deployment, typed decisions, MCP connection, and safe use of probability-bearing results.
- Added tag-gated ClawHub publishing for the Decidealot skill and OpenClaw stdio bridge.

Fixed:

- Fixed CUDA inference under the hardened runtime by providing Triton's required compiler, Python headers, and narrow executable cache mount.

## v0.2.0

Breaking changes:

- Removed `jev` and every `jev-*` alias. Decidealot runs local Laya and Von only, and every decision request now requires an explicit supported `model` selector.
- Removed `POST /v1/models/{model}/unload`. Use idempotent `POST /v1/models/unload` to release the single resident provider.
- Removed `DECIDEALOT_MODEL_DATA_DIR`, `DECIDEALOT_LAYA_MODEL_DIR`, `DECIDEALOT_VON_MODEL_DIR`, and `DECIDEALOT_DEFAULT_MODEL`. Mount one host directory at the fixed container path `/models` instead.

Changed:

- Startup downloads and verifies both pinned model bundles before readiness, then loads model and Torch memory only for the selected provider.
- Added Laya checkpoint selectors for automatic, English, multilingual, and typed-decision routing. Added model-selection and response-shape examples to the API guide.
- Fixed real model-test cleanup for model files created by the container's non-root runtime user.

## v0.1.0

Initial release.

- Added local Laya and Von execution behind TypeSafe-compatible `POST /v1/systemone` and `GET /v1/models` endpoints.
- Added CPU and CUDA Docker images, lazy provider startup, explicit unload endpoints, and configurable idle unloading that releases the full model and Torch runtime.
- Added pinned model download and reuse through named volumes or narrow host directories.
- Added TypeSafe request validation, optional Bearer authentication, request-size limits, structured logs, and public API and deployment documentation.
- Added deterministic unit and local-provider integration tests, real CPU model checks, and CUDA image validation.
