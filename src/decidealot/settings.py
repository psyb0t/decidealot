"""Validated process-wide configuration."""

from pathlib import Path
from typing import Annotated, Literal, cast

from pydantic import BeforeValidator, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from decidealot.constants import (
    DEFAULT_DEVICE,
    DEFAULT_IMAGE_VARIANT,
    DEFAULT_LISTEN_HOST,
    DEFAULT_LISTEN_PORT,
    DEFAULT_LOG_FILE,
    DEFAULT_LOG_LEVEL,
    DEFAULT_MAX_REQUEST_BYTES,
    DEFAULT_PROVIDER_IDLE_UNLOAD_SECONDS,
    DEFAULT_PROVIDER_START_TIMEOUT_SECONDS,
    DEFAULT_REQUEST_TIMEOUT_SECONDS,
    IMAGE_VARIANT_METADATA_PATH,
)

Device = Literal["cpu", "cuda"]
ImageVariant = Literal["cpu", "cuda"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]


def _absolute_path(value: object) -> Path:
    value_as_text = str(value)
    if not value_as_text or "\x00" in value_as_text:
        raise ValueError("must be a non-empty path without NUL bytes")
    path = Path(value_as_text)
    if not path.is_absolute():
        raise ValueError("must be an absolute path")
    return path


AbsolutePath = Annotated[Path, BeforeValidator(_absolute_path)]


class Settings(BaseSettings):
    """All configuration is parsed once during process startup."""

    model_config = SettingsConfigDict(env_prefix="DECIDEALOT_", extra="ignore")

    api_key: SecretStr | None = None
    listen_host: str = Field(default=DEFAULT_LISTEN_HOST, min_length=1, max_length=255)
    listen_port: int = Field(default=DEFAULT_LISTEN_PORT, ge=1024, le=65535)
    device: Device = cast(Device, DEFAULT_DEVICE)
    image_variant: ImageVariant = cast(ImageVariant, DEFAULT_IMAGE_VARIANT)
    log_level: LogLevel = cast(LogLevel, DEFAULT_LOG_LEVEL)
    log_file: AbsolutePath = DEFAULT_LOG_FILE
    request_timeout_seconds: float = Field(default=DEFAULT_REQUEST_TIMEOUT_SECONDS, gt=0, le=600)
    provider_start_timeout_seconds: float = Field(
        default=DEFAULT_PROVIDER_START_TIMEOUT_SECONDS,
        gt=0,
        le=1800,
    )
    provider_idle_unload_seconds: float = Field(
        default=DEFAULT_PROVIDER_IDLE_UNLOAD_SECONDS,
        ge=0,
        le=86_400,
    )
    max_request_bytes: int = Field(default=DEFAULT_MAX_REQUEST_BYTES, ge=1024, le=16_777_216)

    @field_validator("api_key", mode="before")
    @classmethod
    def blank_api_key_disables_authentication(cls, value: object) -> object:
        if value is None or value == "":
            return None
        return value

    @field_validator("device", mode="before")
    @classmethod
    def normalize_device(cls, value: object) -> Device:
        return cast(Device, str(value).lower())

    @field_validator("image_variant", mode="before")
    @classmethod
    def normalize_image_variant(cls, value: object) -> ImageVariant:
        return cast(ImageVariant, str(value).lower())

    @field_validator("log_level", mode="before")
    @classmethod
    def normalize_log_level(cls, value: object) -> LogLevel:
        return cast(LogLevel, str(value).upper())

    @model_validator(mode="after")
    def device_matches_image_variant(self) -> "Settings":
        if self.device != self.image_variant:
            raise ValueError("DECIDEALOT_DEVICE must match the installed image variant")
        installed_image_variant = _read_installed_image_variant()
        if installed_image_variant is not None and self.image_variant != installed_image_variant:
            raise ValueError("DECIDEALOT_IMAGE_VARIANT must match the installed image variant")
        return self


def _read_installed_image_variant() -> ImageVariant | None:
    """Read immutable variant metadata baked into production image filesystems."""

    try:
        variant = IMAGE_VARIANT_METADATA_PATH.read_text(encoding="utf-8").strip().lower()
    except FileNotFoundError:
        return None
    except OSError as error:
        raise ValueError("read installed image variant metadata") from error
    if variant not in {"cpu", "cuda"}:
        raise ValueError("installed image variant metadata is invalid")
    return cast(ImageVariant, variant)
