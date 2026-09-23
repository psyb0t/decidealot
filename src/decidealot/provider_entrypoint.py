"""Run one native provider against its configured local model directory."""

import logging
import os
import sys
from importlib import import_module
from pathlib import Path
from typing import Any

_laya_provider = "laya"
_von_provider = "von"
_model_data_directory = Path("/models")
_prepare_action = "prepare"
_laya_host_env = "LAYA_HOST"
_laya_port_env = "LAYA_PORT"
_laya_device_env = "LAYA_DEVICE"
_laya_models_env = "LAYA_MODELS"
_laya_auto_task_env = "LAYA_AUTO_TASK"
_laya_preload_env = "LAYA_PRELOAD"
_laya_log_level_env = "LAYA_LOG_LEVEL"
_von_host_env = "VON_HOST"
_von_port_env = "VON_PORT"
_von_device_env = "VON_DEVICE"
_von_backend_env = "VON_BACKEND"
_default_laya_host = "127.0.0.1"
_default_laya_port = 8000
_default_von_host = "127.0.0.1"
_default_von_port = 8000
_default_von_backend = "von-1.1"
_laya_router_module = "laya.router"
_laya_router_name = "Router"
_laya_serve_module = "laya.serve"
_laya_apply_thread_limit_name = "_apply_thread_limit"
_laya_env_bool_name = "_env_bool"
_laya_create_app_name = "create_app"
_von_option_marker_module = "von.backends.option_marker_backend"
_von_option_marker_name = "OptionMarkerBackend"
_von_engine_module = "von.engine"
_von_engine_name = "VonEngine"
_von_server_module = "von.server"
_von_server_app_name = "app"
_huggingface_hub_module = "huggingface_hub"
_snapshot_download_name = "snapshot_download"
_laya_model_repository = "convaiinnovations/laya"
_von_model_repository = "wfzyx/von"
_laya_model_revision = "5e7b2b1b8ca2ecdd3f2322d94069c9b6ce7e844b"
_von_model_revision = "d8bb5e0745d8ee1fb65d536d6d4892d54d5a93fd"
_hf_hub_offline_env = "HF_HUB_OFFLINE"
_transformers_offline_env = "TRANSFORMERS_OFFLINE"
_offline_enabled = "1"
_laya_checkpoint_directories = ("", "multilingual", "typed-decisions")
_laya_checkpoint_files = (
    "rl_agent_config.json",
    "model.safetensors",
    "encoder/config.json",
    "tokenizer/tokenizer.json",
)
_von_model_files = (
    "option_marker.pt",
    "model.safetensors",
    "config.json",
    "marker_calibration.json",
    "tokenizer.json",
    "tokenizer_config.json",
)

logger = logging.getLogger(__name__)


def import_provider_attribute(module_name: str, attribute_name: str) -> Any:
    """Load an optional provider API only in that provider's isolated virtualenv."""

    return getattr(import_module(module_name), attribute_name)


def local_model_dir(provider_name: str) -> Path:
    """Return one fixed provider directory under the mounted model root."""

    if provider_name not in {_laya_provider, _von_provider}:
        raise RuntimeError(f"unsupported local provider: {provider_name}")
    path = _model_data_directory / provider_name
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise RuntimeError(f"create local model directory for {provider_name}") from error
    if not os.access(path, os.R_OK | os.X_OK):
        raise RuntimeError(f"local model directory for {provider_name} must be readable")
    return path


def laya_model_specs(model_dir: Path) -> dict[str, tuple[str, str | None]]:
    """Map Laya aliases to a locally downloaded complete checkpoint bundle."""

    path = str(model_dir)
    return {
        "english": (path, None),
        "multilingual": (path, "multilingual"),
        "typed-decisions": (path, "typed-decisions"),
    }


def require_laya_bundle(model_dir: Path) -> None:
    """Reject an incomplete local Laya bundle before its server starts."""

    missing_files = _missing_laya_bundle_files(model_dir)
    if missing_files:
        raise RuntimeError(f"Laya bundle is missing required files: {', '.join(missing_files)}")


def _missing_laya_bundle_files(model_dir: Path) -> list[str]:
    return [
        str(Path(checkpoint_directory) / required_file)
        for checkpoint_directory in _laya_checkpoint_directories
        for required_file in _laya_checkpoint_files
        if not (model_dir / checkpoint_directory / required_file).is_file()
    ]


def require_von_model(model_dir: Path) -> None:
    """Reject an incomplete local Von repository before its server starts."""

    missing_files = _missing_von_model_files(model_dir)
    if missing_files:
        raise RuntimeError(f"Von model is missing required files: {', '.join(missing_files)}")


def _missing_von_model_files(model_dir: Path) -> list[str]:
    return [
        required_file
        for required_file in _von_model_files
        if not (model_dir / required_file).is_file()
    ]


def download_model_bundle(repository: str, revision: str, model_dir: Path) -> None:
    """Populate a provider directory from its official Hugging Face repository."""

    if not os.access(model_dir, os.W_OK | os.X_OK):
        raise RuntimeError("configured model directory must be writable to download missing files")
    snapshot_download = import_provider_attribute(_huggingface_hub_module, _snapshot_download_name)
    logger.info(
        "downloading local provider model bundle",
        extra={
            "repository": repository,
            "revision": revision,
            "model_dir": str(model_dir),
        },
    )
    try:
        snapshot_download(repo_id=repository, revision=revision, local_dir=str(model_dir))
    except Exception as error:  # Hugging Face exposes several transport and cache exception types.
        logger.error(
            "local provider model download failed",
            exc_info=error,
            extra={
                "repository": repository,
                "revision": revision,
                "model_dir": str(model_dir),
            },
        )
        raise RuntimeError(f"download model repository {repository}@{revision}") from error
    logger.info(
        "local provider model bundle downloaded",
        extra={
            "repository": repository,
            "revision": revision,
            "model_dir": str(model_dir),
        },
    )


def ensure_laya_bundle(model_dir: Path) -> None:
    """Download missing Laya files once, then require the complete local bundle."""

    if _missing_laya_bundle_files(model_dir):
        download_model_bundle(_laya_model_repository, _laya_model_revision, model_dir)
    require_laya_bundle(model_dir)


def ensure_von_model(model_dir: Path) -> None:
    """Download missing Von files once, then require the complete local repository."""

    if _missing_von_model_files(model_dir):
        download_model_bundle(_von_model_repository, _von_model_revision, model_dir)
    require_von_model(model_dir)


def prepare_laya() -> None:
    """Download or verify Laya without constructing its runtime or importing Torch."""

    ensure_laya_bundle(local_model_dir(_laya_provider))


def prepare_von() -> None:
    """Download or verify Von without constructing its runtime or importing Torch."""

    ensure_von_model(local_model_dir(_von_provider))


def enable_offline_model_loading() -> None:
    """Forbid provider fallback network requests after the bundle is prepared."""

    os.environ[_hf_hub_offline_env] = _offline_enabled
    os.environ[_transformers_offline_env] = _offline_enabled


def run_laya() -> None:
    """Prepare and start Laya from its local checkpoint bundle."""

    import uvicorn

    router_type = import_provider_attribute(_laya_router_module, _laya_router_name)
    apply_thread_limit = import_provider_attribute(
        _laya_serve_module, _laya_apply_thread_limit_name
    )
    environment_bool = import_provider_attribute(_laya_serve_module, _laya_env_bool_name)
    create_application = import_provider_attribute(_laya_serve_module, _laya_create_app_name)

    apply_thread_limit()
    model_dir = local_model_dir(_laya_provider)
    ensure_laya_bundle(model_dir)
    enable_offline_model_loading()
    models_env = os.environ.get(_laya_models_env, "").strip()
    preload_names = [name.strip() for name in models_env.split(",") if name.strip()] or None
    router = router_type(
        models=laya_model_specs(model_dir),
        device=os.environ.get(_laya_device_env) or None,
        auto_task_detection=environment_bool(_laya_auto_task_env, False),
    )
    if environment_bool(_laya_preload_env, True):
        router.preload(preload_names)
    uvicorn.run(
        create_application(router=router),
        host=os.environ.get(_laya_host_env, _default_laya_host),
        port=int(os.environ.get(_laya_port_env, _default_laya_port)),
        log_level=os.environ.get(_laya_log_level_env, "info"),
    )


def run_von() -> None:
    """Prepare and start Von from its local checkpoint repository."""

    import uvicorn

    option_marker_backend_type = import_provider_attribute(
        _von_option_marker_module, _von_option_marker_name
    )
    engine_type = import_provider_attribute(_von_engine_module, _von_engine_name)

    model_dir = local_model_dir(_von_provider)
    ensure_von_model(model_dir)
    enable_offline_model_loading()
    engine = engine_type(
        backend_name=os.environ.get(_von_backend_env, _default_von_backend),
        device=os.environ.get(_von_device_env),
    )
    engine.backend = option_marker_backend_type(checkpoint_dir=str(model_dir), device=engine.device)
    # Von's server resolves its backend from this singleton, whose public constructor
    # has no checkpoint-directory argument.
    with engine_type._lock:
        engine_type._instance = engine
    application = import_provider_attribute(_von_server_module, _von_server_app_name)

    uvicorn.run(
        application,
        host=os.environ.get(_von_host_env, _default_von_host),
        port=int(os.environ.get(_von_port_env, _default_von_port)),
    )


def main() -> None:
    """Select the fixed provider entrypoint requested by Decidealot."""

    arguments = sys.argv[1:]
    if len(arguments) == 1:
        provider_name = arguments[0]
        if provider_name == _laya_provider:
            run_laya()
            return
        if provider_name == _von_provider:
            run_von()
            return
        raise SystemExit(f"unsupported local provider: {provider_name}")
    if len(arguments) == 2 and arguments[0] == _prepare_action:
        provider_name = arguments[1]
        if provider_name == _laya_provider:
            prepare_laya()
            return
        if provider_name == _von_provider:
            prepare_von()
            return
        raise SystemExit(f"unsupported local provider: {provider_name}")
    raise SystemExit("expected a provider name or 'prepare <provider>'")


if __name__ == "__main__":
    main()
