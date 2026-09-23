"""Configuration must fail before the service launches providers."""

from pathlib import Path

import pytest
from pydantic import ValidationError

import decidealot.settings as settings_module
from decidealot.constants import DEFAULT_PROVIDER_IDLE_UNLOAD_SECONDS
from decidealot.settings import Settings


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("/var/lib/decidealot", Path("/var/lib/decidealot")),
        (Path("/models"), Path("/models")),
    ],
)
def test_settings_accept_absolute_model_data_dir(value: object, expected: Path) -> None:
    settings = Settings.model_validate({"model_data_dir": value})

    assert settings.model_data_dir == expected


@pytest.mark.parametrize("value", ["relative/models", "", "bad\x00path"])
def test_settings_reject_unsafe_model_data_dir(value: str) -> None:
    with pytest.raises(ValidationError, match="absolute path|non-empty path"):
        Settings.model_validate({"model_data_dir": value})


def test_settings_accepts_optional_local_model_directories(tmp_path: Path) -> None:
    laya_model_dir = tmp_path / "laya"
    von_model_dir = tmp_path / "von"

    settings = Settings(laya_model_dir=laya_model_dir, von_model_dir=von_model_dir)

    assert settings.laya_model_dir == laya_model_dir
    assert settings.von_model_dir == von_model_dir


@pytest.mark.parametrize("configured_value", ["relative/models", "bad\x00path"])
def test_settings_rejects_unsafe_local_model_directory(configured_value: str) -> None:
    with pytest.raises(ValidationError, match="absolute path|non-empty path"):
        Settings.model_validate({"laya_model_dir": configured_value})


@pytest.mark.parametrize("directory_kind", ["file"])
def test_settings_rejects_unusable_local_model_directory(
    tmp_path: Path, directory_kind: str
) -> None:
    local_model_dir = tmp_path / directory_kind
    if directory_kind == "file":
        local_model_dir.write_text("not a directory", encoding="utf-8")

    with pytest.raises(ValidationError, match="must be a directory"):
        Settings(von_model_dir=local_model_dir)


@pytest.mark.parametrize(
    ("configured_value", "expected_auth_enabled"),
    [(None, False), ("", False), ("operator-secret", True)],
)
def test_settings_normalizes_optional_api_key(
    configured_value: str | None, expected_auth_enabled: bool
) -> None:
    settings = Settings.model_validate({"api_key": configured_value})

    assert (settings.api_key is not None) is expected_auth_enabled


@pytest.mark.parametrize(
    ("device", "image_variant", "log_level", "expected_device", "expected_log_level"),
    [
        ("CPU", "CPU", "info", "cpu", "INFO"),
        ("cuda", "cuda", "WARNING", "cuda", "WARNING"),
    ],
)
def test_settings_normalizes_case(
    device: str,
    image_variant: str,
    log_level: str,
    expected_device: str,
    expected_log_level: str,
) -> None:
    settings = Settings.model_validate(
        {"device": device, "image_variant": image_variant, "log_level": log_level}
    )

    assert settings.device == expected_device
    assert settings.log_level == expected_log_level


@pytest.mark.parametrize(
    ("device", "image_variant"),
    [("cpu", "cuda"), ("cuda", "cpu")],
)
def test_settings_reject_device_that_does_not_match_image_variant(
    device: str,
    image_variant: str,
) -> None:
    with pytest.raises(ValidationError, match="must match the installed image variant"):
        Settings.model_validate({"device": device, "image_variant": image_variant})


def test_settings_reject_variant_that_disagrees_with_image_metadata(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    metadata_path = tmp_path / "image-variant"
    metadata_path.write_text("cuda\n", encoding="utf-8")
    monkeypatch.setattr(settings_module, "IMAGE_VARIANT_METADATA_PATH", metadata_path)

    with pytest.raises(ValidationError, match="IMAGE_VARIANT must match"):
        Settings(device="cpu", image_variant="cpu")

    assert Settings(device="cuda", image_variant="cuda").image_variant == "cuda"


def test_settings_rejects_invalid_installed_image_metadata(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    metadata_path = tmp_path / "image-variant"
    metadata_path.write_text("unsupported-variant\n", encoding="utf-8")
    monkeypatch.setattr(settings_module, "IMAGE_VARIANT_METADATA_PATH", metadata_path)

    with pytest.raises(ValidationError, match="installed image variant metadata is invalid"):
        Settings(device="cpu", image_variant="cpu")


def test_settings_default_provider_idle_unload_timeout_is_ten_minutes() -> None:
    assert Settings().provider_idle_unload_seconds == DEFAULT_PROVIDER_IDLE_UNLOAD_SECONDS == 600


@pytest.mark.parametrize("idle_seconds", [0, 0.1, 600, 86_400])
def test_settings_accept_provider_idle_unload_range(idle_seconds: float) -> None:
    settings = Settings(provider_idle_unload_seconds=idle_seconds)

    assert settings.provider_idle_unload_seconds == idle_seconds


@pytest.mark.parametrize("idle_seconds", [-0.1, 86_400.1])
def test_settings_reject_provider_idle_unload_outside_range(idle_seconds: float) -> None:
    with pytest.raises(
        ValidationError, match="greater than or equal to 0|less than or equal to 86400"
    ):
        Settings(provider_idle_unload_seconds=idle_seconds)
