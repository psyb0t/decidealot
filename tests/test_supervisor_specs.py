"""Configured local model directories must produce offline provider processes."""

from pathlib import Path

from decidealot.constants import (
    LAYA_PROVIDER_NAME,
    LAYA_VENV_PYTHON,
    LOCAL_PROVIDER_ENTRYPOINT,
    VON_PROVIDER_NAME,
    VON_VENV_PYTHON,
)
from decidealot.settings import Settings
from decidealot.supervisor import ProviderSupervisor


def test_default_specs_prepare_models_in_the_configured_model_data_directory(
    tmp_path: Path,
) -> None:
    laya_model_dir = tmp_path / "laya"
    von_model_dir = tmp_path / "von"
    supervisor = ProviderSupervisor(
        Settings(
            model_data_dir=tmp_path / "cache",
            laya_model_dir=laya_model_dir,
            von_model_dir=von_model_dir,
        )
    )

    specs = {spec.name: spec for spec in supervisor.provider_specs}

    assert specs[LAYA_PROVIDER_NAME].command == (
        LAYA_VENV_PYTHON,
        LOCAL_PROVIDER_ENTRYPOINT,
        LAYA_PROVIDER_NAME,
    )
    assert specs[VON_PROVIDER_NAME].command == (
        VON_VENV_PYTHON,
        LOCAL_PROVIDER_ENTRYPOINT,
        VON_PROVIDER_NAME,
    )
    for provider_name, expected_model_dir in (
        (LAYA_PROVIDER_NAME, laya_model_dir),
        (VON_PROVIDER_NAME, von_model_dir),
    ):
        environment = specs[provider_name].environment
        assert environment[f"DECIDEALOT_{provider_name.upper()}_MODEL_DIR"] == str(
            expected_model_dir
        )
        assert "HF_HUB_OFFLINE" not in environment
        assert "TRANSFORMERS_OFFLINE" not in environment


def test_default_specs_use_provider_subdirectories_when_no_custom_directory_is_set(
    tmp_path: Path,
) -> None:
    model_data_dir = tmp_path / "model-data"
    supervisor = ProviderSupervisor(Settings(model_data_dir=model_data_dir))

    specs = {spec.name: spec for spec in supervisor.provider_specs}

    assert specs[LAYA_PROVIDER_NAME].environment["DECIDEALOT_LAYA_MODEL_DIR"] == str(
        model_data_dir / LAYA_PROVIDER_NAME
    )
    assert specs[VON_PROVIDER_NAME].environment["DECIDEALOT_VON_MODEL_DIR"] == str(
        model_data_dir / VON_PROVIDER_NAME
    )
