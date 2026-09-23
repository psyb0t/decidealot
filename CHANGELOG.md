# Changelog

All notable changes per release. Versions follow [SemVer](https://semver.org/). Before 1.0, compatible additions use a minor version and fixes use a patch version. Breaking API or configuration changes are called out.

## v0.1.0

Initial release.

- Added local Laya and Von execution behind TypeSafe-compatible `POST /v1/systemone` and `GET /v1/models` endpoints.
- Added CPU and CUDA Docker images, lazy provider startup, explicit unload endpoints, and configurable idle unloading that releases the full model and Torch runtime.
- Added pinned model download and reuse through named volumes or narrow host directories.
- Added TypeSafe request validation, optional Bearer authentication, request-size limits, structured logs, and public API and deployment documentation.
- Added deterministic unit and local-provider integration tests, real CPU model checks, and CUDA image validation.
