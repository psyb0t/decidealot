"""Local provider entrypoints prepare and reuse provider model directories."""

import os
import sys
from collections.abc import Callable
from pathlib import Path
from threading import Lock
from typing import Any

import pytest

import decidealot.provider_entrypoint as provider_entrypoint
from decidealot.provider_entrypoint import (
    enable_offline_model_loading,
    ensure_laya_bundle,
    ensure_von_model,
    laya_model_specs,
    local_model_dir,
    require_laya_bundle,
    require_von_model,
)

_laya_repository = "convaiinnovations/laya"
_von_repository = "wfzyx/von"
_laya_revision = "5e7b2b1b8ca2ecdd3f2322d94069c9b6ce7e844b"
_von_revision = "d8bb5e0745d8ee1fb65d536d6d4892d54d5a93fd"
_hf_hub_offline_env = "HF_HUB_OFFLINE"
_transformers_offline_env = "TRANSFORMERS_OFFLINE"
_offline_enabled = "1"
BundleEnsurer = Callable[[Path], None]
BundleWriter = Callable[[Path], None]


def _write_laya_bundle(model_dir: Path) -> None:
    for checkpoint_directory in ("", "multilingual", "typed-decisions"):
        for required_file in (
            "rl_agent_config.json",
            "model.safetensors",
            "encoder/config.json",
            "tokenizer/tokenizer.json",
        ):
            file_path = model_dir / checkpoint_directory / required_file
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.touch()


def _write_von_model(model_dir: Path) -> None:
    for required_file in (
        "option_marker.pt",
        "model.safetensors",
        "config.json",
        "marker_calibration.json",
        "tokenizer.json",
        "tokenizer_config.json",
    ):
        file_path = model_dir / required_file
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.touch()


def test_laya_model_specs_keep_every_checkpoint_inside_the_downloaded_bundle(
    tmp_path: Path,
) -> None:
    specs = laya_model_specs(tmp_path)

    assert specs == {
        "english": (str(tmp_path), None),
        "multilingual": (str(tmp_path), "multilingual"),
        "typed-decisions": (str(tmp_path), "typed-decisions"),
    }


def test_laya_bundle_validation_accepts_complete_downloaded_layout(tmp_path: Path) -> None:
    _write_laya_bundle(tmp_path)

    require_laya_bundle(tmp_path)


def test_laya_bundle_validation_rejects_incomplete_download(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="Laya bundle is missing required files"):
        require_laya_bundle(tmp_path)


def test_von_model_validation_accepts_complete_downloaded_layout(tmp_path: Path) -> None:
    _write_von_model(tmp_path)

    require_von_model(tmp_path)


def test_von_model_validation_rejects_incomplete_download(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="Von model is missing required files"):
        require_von_model(tmp_path)


def test_von_model_validation_requires_calibration_data(tmp_path: Path) -> None:
    for required_file in (
        "option_marker.pt",
        "model.safetensors",
        "config.json",
        "tokenizer.json",
        "tokenizer_config.json",
    ):
        (tmp_path / required_file).touch()

    with pytest.raises(RuntimeError, match="marker_calibration.json"):
        require_von_model(tmp_path)


def test_local_model_dir_uses_the_fixed_provider_subdirectory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(provider_entrypoint, "_model_data_directory", tmp_path)

    assert local_model_dir("laya") == tmp_path / "laya"
    assert (tmp_path / "laya").is_dir()


@pytest.mark.parametrize("provider_name", ["", "remote-url", "https://example.test"])
def test_local_model_dir_rejects_unsupported_provider_names(provider_name: str) -> None:
    with pytest.raises(RuntimeError, match="unsupported local provider"):
        local_model_dir(provider_name)


@pytest.mark.parametrize(
    ("ensure_bundle", "repository", "revision", "write_bundle"),
    [
        (ensure_laya_bundle, _laya_repository, _laya_revision, _write_laya_bundle),
        (ensure_von_model, _von_repository, _von_revision, _write_von_model),
    ],
)
def test_incomplete_model_directory_downloads_the_official_bundle(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    ensure_bundle: BundleEnsurer,
    repository: str,
    revision: str,
    write_bundle: BundleWriter,
) -> None:
    model_dir = tmp_path / "downloaded-model"
    model_dir.mkdir()
    download_calls: list[tuple[str, str, str]] = []

    def snapshot_download(*, repo_id: str, revision: str, local_dir: str) -> str:
        download_calls.append((repo_id, revision, local_dir))
        write_bundle(Path(local_dir))
        return local_dir

    def provider_attribute(module_name: str, attribute_name: str) -> object:
        assert module_name == "huggingface_hub"
        assert attribute_name == "snapshot_download"
        return snapshot_download

    monkeypatch.setattr(provider_entrypoint, "import_provider_attribute", provider_attribute)

    ensure_bundle(model_dir)

    assert download_calls == [(repository, revision, str(model_dir))]


@pytest.mark.parametrize(
    ("ensure_bundle", "write_bundle"),
    [
        (ensure_laya_bundle, _write_laya_bundle),
        (ensure_von_model, _write_von_model),
    ],
)
def test_complete_model_directory_does_not_download_again(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    ensure_bundle: BundleEnsurer,
    write_bundle: BundleWriter,
) -> None:
    write_bundle(tmp_path)

    def download_was_not_expected(**_kwargs: object) -> str:
        raise AssertionError("complete model directory must not download")

    def provider_attribute_not_expected(
        _module_name: str, _attribute_name: str
    ) -> Callable[..., str]:
        return download_was_not_expected

    monkeypatch.setattr(
        provider_entrypoint,
        "import_provider_attribute",
        provider_attribute_not_expected,
    )

    ensure_bundle(tmp_path)


@pytest.mark.parametrize(
    ("ensure_bundle", "write_bundle"),
    [
        (ensure_laya_bundle, _write_laya_bundle),
        (ensure_von_model, _write_von_model),
    ],
)
def test_incomplete_read_only_model_directory_fails_before_downloading(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    ensure_bundle: BundleEnsurer,
    write_bundle: BundleWriter,
) -> None:
    model_dir = tmp_path / "read-only-model"
    model_dir.mkdir()
    write_bundle(model_dir)
    (model_dir / "model.safetensors").unlink()

    def download_was_not_expected(**_kwargs: object) -> str:
        raise AssertionError("read-only incomplete model directory must not download")

    def provider_attribute_not_expected(
        _module_name: str, _attribute_name: str
    ) -> Callable[..., str]:
        return download_was_not_expected

    def access_without_write(path: str | Path, mode: int) -> bool:
        return path != model_dir or mode != os.W_OK | os.X_OK

    monkeypatch.setattr(
        provider_entrypoint,
        "import_provider_attribute",
        provider_attribute_not_expected,
    )
    monkeypatch.setattr("decidealot.provider_entrypoint.os.access", access_without_write)

    with pytest.raises(RuntimeError, match="must be writable"):
        ensure_bundle(model_dir)


def test_prepared_model_bundle_disables_hub_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(_hf_hub_offline_env, raising=False)
    monkeypatch.delenv(_transformers_offline_env, raising=False)

    enable_offline_model_loading()

    assert os.environ[_hf_hub_offline_env] == _offline_enabled
    assert os.environ[_transformers_offline_env] == _offline_enabled


def test_laya_entrypoint_starts_native_server_with_the_local_bundle(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    events: dict[str, object] = {}

    class Router:
        def __init__(
            self,
            *,
            models: dict[str, tuple[str, str | None]],
            device: str | None,
            auto_task_detection: bool,
        ) -> None:
            events["models"] = models
            events["device"] = device
            events["auto_task_detection"] = auto_task_detection

        def preload(self, names: list[str] | None) -> None:
            events["preload_names"] = names

    def apply_thread_limit() -> None:
        events["thread_limit"] = True

    def environment_bool(_name: str, default: bool) -> bool:
        return default

    def create_application(*, router: Router) -> object:
        events["router"] = router
        return "laya-application"

    def provider_attribute(module_name: str, attribute_name: str) -> object:
        values = {
            ("laya.router", "Router"): Router,
            ("laya.serve", "_apply_thread_limit"): apply_thread_limit,
            ("laya.serve", "_env_bool"): environment_bool,
            ("laya.serve", "create_app"): create_application,
        }
        return values[(module_name, attribute_name)]

    def run(application: object, **kwargs: object) -> None:
        events["application"] = application
        events["server"] = kwargs

    def local_model_directory(_: str) -> Path:
        return tmp_path

    def prepared_laya_bundle(_: Path) -> None:
        return None

    monkeypatch.setattr(provider_entrypoint, "import_provider_attribute", provider_attribute)
    monkeypatch.setattr(provider_entrypoint, "local_model_dir", local_model_directory)
    monkeypatch.setattr(provider_entrypoint, "ensure_laya_bundle", prepared_laya_bundle)
    monkeypatch.setattr("uvicorn.run", run)
    monkeypatch.setenv("LAYA_DEVICE", "cpu")
    monkeypatch.setenv("LAYA_HOST", "127.0.0.1")
    monkeypatch.setenv("LAYA_PORT", "8011")
    monkeypatch.setenv("LAYA_MODELS", "english, multilingual")

    provider_entrypoint.run_laya()

    assert events["thread_limit"] is True
    assert events["models"] == laya_model_specs(tmp_path)
    assert events["device"] == "cpu"
    assert events["auto_task_detection"] is False
    assert events["preload_names"] == ["english", "multilingual"]
    assert events["application"] == "laya-application"
    assert events["server"] == {"host": "127.0.0.1", "port": 8011, "log_level": "info"}


def test_von_entrypoint_starts_native_server_with_the_local_bundle(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    events: dict[str, object] = {}

    class OptionMarkerBackend:
        def __init__(self, *, checkpoint_dir: str, device: str | None) -> None:
            events["checkpoint_dir"] = checkpoint_dir
            events["backend_device"] = device

    class Engine:
        _lock = Lock()
        _instance: Any = None

        def __init__(self, *, backend_name: str, device: str | None) -> None:
            self.device = device
            events["backend_name"] = backend_name
            events["engine_device"] = device

    def provider_attribute(module_name: str, attribute_name: str) -> object:
        values = {
            ("von.backends.option_marker_backend", "OptionMarkerBackend"): OptionMarkerBackend,
            ("von.engine", "VonEngine"): Engine,
            ("von.server", "app"): "von-application",
        }
        return values[(module_name, attribute_name)]

    def run(application: object, **kwargs: object) -> None:
        events["application"] = application
        events["server"] = kwargs

    def local_model_directory(_: str) -> Path:
        return tmp_path

    def prepared_von_model(_: Path) -> None:
        return None

    monkeypatch.setattr(provider_entrypoint, "import_provider_attribute", provider_attribute)
    monkeypatch.setattr(provider_entrypoint, "local_model_dir", local_model_directory)
    monkeypatch.setattr(provider_entrypoint, "ensure_von_model", prepared_von_model)
    monkeypatch.setattr("uvicorn.run", run)
    monkeypatch.setenv("VON_BACKEND", "von-1.1")
    monkeypatch.setenv("VON_DEVICE", "cpu")
    monkeypatch.setenv("VON_HOST", "127.0.0.1")
    monkeypatch.setenv("VON_PORT", "8012")

    provider_entrypoint.run_von()

    assert events["backend_name"] == "von-1.1"
    assert events["engine_device"] == "cpu"
    assert events["checkpoint_dir"] == str(tmp_path)
    assert events["backend_device"] == "cpu"
    assert events["application"] == "von-application"
    assert events["server"] == {"host": "127.0.0.1", "port": 8012}


@pytest.mark.parametrize(("provider_name", "expected_call"), [("laya", "laya"), ("von", "von")])
def test_provider_entrypoint_selects_only_the_requested_provider(
    monkeypatch: pytest.MonkeyPatch, provider_name: str, expected_call: str
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(sys, "argv", ["provider_entrypoint", provider_name])
    monkeypatch.setattr(provider_entrypoint, "run_laya", lambda: calls.append("laya"))
    monkeypatch.setattr(provider_entrypoint, "run_von", lambda: calls.append("von"))

    provider_entrypoint.main()

    assert calls == [expected_call]


@pytest.mark.parametrize(("provider_name", "expected_call"), [("laya", "laya"), ("von", "von")])
def test_provider_entrypoint_prepares_only_the_requested_provider(
    monkeypatch: pytest.MonkeyPatch, provider_name: str, expected_call: str
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(sys, "argv", ["provider_entrypoint", "prepare", provider_name])
    monkeypatch.setattr(provider_entrypoint, "prepare_laya", lambda: calls.append("laya"))
    monkeypatch.setattr(provider_entrypoint, "prepare_von", lambda: calls.append("von"))

    provider_entrypoint.main()

    assert calls == [expected_call]


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (["provider_entrypoint"], "expected a provider name"),
        (["provider_entrypoint", "other"], "unsupported local provider"),
        (["provider_entrypoint", "prepare"], "unsupported local provider"),
        (["provider_entrypoint", "prepare", "other"], "unsupported local provider"),
    ],
)
def test_provider_entrypoint_rejects_invalid_provider_selection(
    monkeypatch: pytest.MonkeyPatch, arguments: list[str], message: str
) -> None:
    monkeypatch.setattr(sys, "argv", arguments)

    with pytest.raises(SystemExit, match=message):
        provider_entrypoint.main()
