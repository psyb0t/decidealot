# Providers and scheduling

Prefix: PRV. Covers `providers.py`, `supervisor.py`, `decisions.py`, `provider_entrypoint.py`, `clm_runtime.py` and `constants.py`.

- **PRV1:** Selectors map to backends only in `ModelRouter`. A new alias goes into the matching `_*_catalog_entries` tuple, and `docs/models.md`, `docs/api.md` and the README selector table follow. Hosted names come only from TypeSafe's catalog; never add a local `jev` alias.
- **PRV2:** Local providers are child processes started by `ProviderSupervisor` from fixed `ProviderSpec` argument lists, bound to `127.0.0.1` on the ports in `constants.py`. Never start one through a shell or from request data.
- **PRV3:** Model bundles are pinned by Hugging Face revision, and CLM also by SHA-256, in both `constants.py` and `provider_entrypoint.py`. `provider_entrypoint.py` and `clm_runtime.py` run in `/opt/torch-venv` and must not import `decidealot`. After preparation they set `HF_HUB_OFFLINE` and `TRANSFORMERS_OFFLINE`.
- **PRV4:** Resident slots (`max_resident_local_providers`) and execution lanes (`DecisionService._local_lane`) are separate. CUDA inference stays serial and CLM stays exclusive unless `clm_parallel_with_local_models` is set. Hosted Jev bypasses both. Keep the per-model lock so requests to one model run in order.
- **PRV5:** Unload is all-or-nothing: `unload_all` raises `ProviderBusyError` while any local provider has an active request. Idle unload never stops a provider with an active lease.
- **PRV6:** `HTTPProviderClient` keeps `follow_redirects=False` and `trust_env=False`. A provider `5xx`, timeout, transport error or non-JSON body becomes `ProviderUnavailableError`; a provider `422` becomes the official validation envelope through `provider_validation_content`.
- **PRV7:** A model runtime dependency changes in `requirements-providers-*.in`, then `make model-lock` regenerates the hashed `.txt` files. Never edit the `.txt` files by hand.
