"""Fixed local model paths must produce download-only preparation commands."""

from pydantic import SecretStr

from decidealot.constants import (
    CLM_PROVIDER_NAME,
    CLM_VENV_PYTHON,
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


def test_clm_spec_uses_the_laya_torch_runtime_and_fixed_embeddings_configuration() -> None:
    supervisor = ProviderSupervisor(
        Settings(
            laya_enabled=False,
            von_enabled=False,
            clm_enabled=True,
            clm_embeddings_url="https://embeddings.example.test/v1/embeddings",
            clm_embeddings_model="qwen3-8b",
        )
    )

    spec = supervisor.provider_specs[0]

    assert spec.name == CLM_PROVIDER_NAME
    assert spec.command == (CLM_VENV_PYTHON, LOCAL_PROVIDER_ENTRYPOINT, CLM_PROVIDER_NAME)
    assert spec.prepare_command == (
        CLM_VENV_PYTHON,
        LOCAL_PROVIDER_ENTRYPOINT,
        PROVIDER_ENTRYPOINT_PREPARE_ACTION,
        CLM_PROVIDER_NAME,
    )
    assert spec.environment["CLM_EMBEDDINGS_URL"] == "https://embeddings.example.test/v1/embeddings"
    assert spec.environment["CLM_EMBEDDINGS_MODEL"] == "qwen3-8b"


def test_jev_only_configuration_has_no_local_process_or_download_steps() -> None:
    supervisor = ProviderSupervisor(
        Settings(
            laya_enabled=False,
            von_enabled=False,
            typesafe_api_key=SecretStr("upstream-secret"),
        )
    )

    assert supervisor.provider_specs == ()
