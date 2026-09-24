"""Configuration must fail before the service launches providers."""

from pathlib import Path

import pytest
from pydantic import ValidationError

import decidealot.settings as settings_module
from decidealot.constants import (
    DEFAULT_MCP_ALLOWED_HOSTS,
    DEFAULT_MCP_ALLOWED_ORIGINS,
    DEFAULT_PROVIDER_IDLE_UNLOAD_SECONDS,
)
from decidealot.settings import Settings


def test_settings_exposes_no_model_location_or_default_model_configuration() -> None:
    settings = Settings()

    assert not hasattr(settings, "model_data_dir")
    assert not hasattr(settings, "laya_model_dir")
    assert not hasattr(settings, "von_model_dir")
    assert not hasattr(settings, "default_model")


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


def test_settings_defaults_to_loopback_and_docker_mcp_hosts() -> None:
    settings = Settings()

    assert settings.mcp_allowed_hosts == DEFAULT_MCP_ALLOWED_HOSTS
    assert settings.mcp_allowed_host_values == (
        "127.0.0.1",
        "127.0.0.1:*",
        "localhost",
        "localhost:*",
        "[::1]",
        "[::1]:*",
        "decidealot",
        "decidealot:*",
    )
    assert settings.mcp_allowed_origins == DEFAULT_MCP_ALLOWED_ORIGINS


def test_settings_normalizes_mcp_proxy_allowlists() -> None:
    settings = Settings(
        mcp_allowed_hosts=" localhost:*, mcp.example.net ",
        mcp_allowed_origins=" http://localhost:*, https://mcp.example.net ",
    )

    assert settings.mcp_allowed_host_values == ("localhost:*", "mcp.example.net")
    assert settings.mcp_allowed_origin_values == (
        "http://localhost:*",
        "https://mcp.example.net",
    )


@pytest.mark.parametrize("value", ["", ",", "localhost:*,", ",localhost:*"])
def test_settings_rejects_empty_mcp_allowlist_entries(value: str) -> None:
    with pytest.raises(ValidationError, match="non-empty comma-separated allowlist"):
        Settings(mcp_allowed_hosts=value)


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
