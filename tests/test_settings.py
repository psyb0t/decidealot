"""Configuration must fail before the service launches providers."""

from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

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


@pytest.mark.parametrize("capacity", [0, 4, -1])
def test_settings_rejects_invalid_resident_provider_capacity(capacity: int) -> None:
    with pytest.raises(ValidationError):
        Settings(max_resident_local_providers=capacity)


def test_settings_batch_limit_defaults_to_zero_and_reads_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert Settings().max_batch_requests == 0
    monkeypatch.setenv("DECIDEALOT_MAX_BATCH_REQUESTS", "12")
    assert Settings().max_batch_requests == 12


@pytest.mark.parametrize("value", [-1, "not-a-number"])
def test_settings_rejects_invalid_batch_limit(value: object) -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate({"max_batch_requests": value})


def test_settings_batch_concurrency_defaults_to_zero_and_reads_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert Settings().max_batch_concurrency == 0
    monkeypatch.setenv("DECIDEALOT_MAX_BATCH_CONCURRENCY", "2")
    assert Settings().max_batch_concurrency == 2


@pytest.mark.parametrize("value", [-1, "not-a-number"])
def test_settings_rejects_invalid_batch_concurrency(value: object) -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate({"max_batch_concurrency": value})


def test_settings_clm_parallel_mode_defaults_off_and_reads_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert Settings().clm_parallel_with_local_models is False
    monkeypatch.setenv("DECIDEALOT_CLM_PARALLEL_WITH_LOCAL_MODELS", "true")
    assert Settings().clm_parallel_with_local_models is True


def test_settings_rejects_invalid_clm_parallel_mode() -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate({"clm_parallel_with_local_models": "maybe"})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("clm_candidate_cache_entries", -1),
        ("clm_candidate_cache_entries", 4097),
        ("clm_candidate_cache_ttl_seconds", 0),
        ("clm_candidate_cache_ttl_seconds", float("inf")),
        ("clm_max_text_bytes", 0),
        ("clm_max_text_bytes", 1048577),
    ],
)
def test_settings_rejects_invalid_clm_cache_and_text_bounds(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate({field: value})


def test_settings_reads_clm_cache_and_text_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DECIDEALOT_CLM_CANDIDATE_CACHE_ENTRIES", "0")
    monkeypatch.setenv("DECIDEALOT_CLM_CANDIDATE_CACHE_TTL_SECONDS", "42")
    monkeypatch.setenv("DECIDEALOT_CLM_MAX_TEXT_BYTES", "4096")
    settings = Settings()
    assert settings.clm_candidate_cache_entries == 0
    assert settings.clm_candidate_cache_ttl_seconds == 42
    assert settings.clm_max_text_bytes == 4096


def test_settings_enables_clm_only_when_an_embeddings_endpoint_is_configured() -> None:
    disabled = Settings()
    enabled = Settings(clm_embeddings_url="https://embeddings.example.test/v1/embeddings")

    assert disabled.enabled_provider_names == ("laya", "von")
    assert enabled.enabled_provider_names == ("laya", "von", "clm")


def test_settings_treats_the_compose_auto_value_as_endpoint_driven_clm_enablement() -> None:
    disabled = Settings.model_validate({"clm_enabled": "auto"})
    enabled = Settings.model_validate(
        {
            "clm_enabled": "auto",
            "clm_embeddings_url": "https://embeddings.example.test/v1/embeddings",
        }
    )

    assert disabled.enabled_provider_names == ("laya", "von")
    assert enabled.enabled_provider_names == ("laya", "von", "clm")


def test_settings_supports_clm_as_the_only_enabled_provider() -> None:
    settings = Settings(
        laya_enabled=False,
        von_enabled=False,
        clm_enabled=True,
        clm_embeddings_url="https://embeddings.example.test/v1/embeddings",
        clm_embeddings_model="qwen3-8b",
    )

    assert settings.enabled_provider_names == ("clm",)


def test_settings_enables_jev_from_a_private_typesafe_key_without_local_models() -> None:
    settings = Settings(
        laya_enabled=False,
        von_enabled=False,
        typesafe_api_key=SecretStr("upstream-secret"),
    )

    assert settings.enabled_provider_names == ("jev",)
    assert settings.jev_enabled is True


def test_settings_can_disable_jev_with_a_configured_key() -> None:
    settings = Settings(typesafe_api_key=SecretStr("upstream-secret"), jev_enabled=False)

    assert settings.enabled_provider_names == ("laya", "von")


def test_settings_rejects_explicit_jev_without_an_upstream_key() -> None:
    with pytest.raises(ValidationError, match="DECIDEALOT_TYPESAFE_API_KEY is required"):
        Settings(jev_enabled=True)


def test_settings_accepts_compose_auto_jev_setting() -> None:
    settings = Settings.model_validate(
        {"typesafe_api_key": "upstream-secret", "jev_enabled": "auto"}
    )

    assert settings.enabled_provider_names == ("laya", "von", "jev")


@pytest.mark.parametrize(
    "settings",
    [
        {"laya_enabled": False, "von_enabled": False, "clm_enabled": False},
        {"clm_enabled": True},
        {"clm_enabled": True, "clm_embeddings_url": "file:///models/embeddings"},
        {
            "clm_enabled": True,
            "clm_embeddings_url": "https://embeddings.example.test/v1/embeddings",
            "clm_embeddings_model": " ",
        },
    ],
)
def test_settings_rejects_invalid_enabled_provider_configuration(
    settings: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate(settings)


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
