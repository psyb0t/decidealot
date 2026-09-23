"""Fixed local model paths must produce download-only preparation commands."""

from decidealot.constants import (
    LAYA_PROVIDER_NAME,
    LAYA_VENV_PYTHON,
    LOCAL_PROVIDER_ENTRYPOINT,
    PROVIDER_ENTRYPOINT_PREPARE_ACTION,
    VON_PROVIDER_NAME,
    VON_VENV_PYTHON,
)
from decidealot.settings import Settings
from decidealot.supervisor import ProviderSupervisor


def test_default_specs_prepare_fixed_provider_subdirectories_before_serving() -> None:
    supervisor = ProviderSupervisor(Settings())

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
    assert specs[LAYA_PROVIDER_NAME].prepare_command == (
        LAYA_VENV_PYTHON,
        LOCAL_PROVIDER_ENTRYPOINT,
        PROVIDER_ENTRYPOINT_PREPARE_ACTION,
        LAYA_PROVIDER_NAME,
    )
    assert specs[VON_PROVIDER_NAME].prepare_command == (
        VON_VENV_PYTHON,
        LOCAL_PROVIDER_ENTRYPOINT,
        PROVIDER_ENTRYPOINT_PREPARE_ACTION,
        VON_PROVIDER_NAME,
    )
    for spec in specs.values():
        assert "HF_HUB_OFFLINE" not in spec.environment
        assert "TRANSFORMERS_OFFLINE" not in spec.environment
