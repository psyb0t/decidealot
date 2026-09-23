"""The CLI process entrypoint must wire validated configuration into Uvicorn."""

from pathlib import Path

import pytest
import uvicorn

import decidealot.main as main_module
from decidealot.settings import Settings


def test_main_runs_the_application_with_validated_settings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = Settings(
        log_file=tmp_path / "decidealot.log",
        model_data_dir=tmp_path / "models",
        listen_host="127.0.0.1",
        listen_port=8088,
    )
    calls: dict[str, object] = {}

    def configure(level: str, log_file: Path) -> None:
        calls["logging"] = (level, log_file)

    def create_application(received_settings: Settings) -> object:
        calls["settings"] = received_settings
        return "application"

    def run(application: object, **kwargs: object) -> None:
        calls["application"] = application
        calls["server"] = kwargs

    monkeypatch.setattr(main_module, "Settings", lambda: settings)
    monkeypatch.setattr(main_module, "configure_logging", configure)
    monkeypatch.setattr(main_module, "create_app", create_application)
    monkeypatch.setattr(uvicorn, "run", run)

    main_module.main()

    assert calls["logging"] == (settings.log_level, settings.log_file)
    assert calls["settings"] is settings
    assert calls["application"] == "application"
    assert calls["server"] == {"host": "127.0.0.1", "port": 8088, "log_config": None}
