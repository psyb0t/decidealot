"""The process logger must produce scoped, redacted JSON records."""

import json
import logging
from pathlib import Path

from decidealot.logging_config import configure_logging, reset_scope, set_scope

_request_id = "1d3fb045-4d61-4ecc-b169-cf012a10ea57"
_redacted_value = "[REDACTED]"


def test_configured_logger_writes_scoped_redacted_utc_json(tmp_path: Path) -> None:
    log_file = tmp_path / "logs" / "decidealot.log"
    configure_logging("INFO", log_file)
    token = set_scope(request_id=_request_id)
    try:
        logging.getLogger("decidealot.test").info(
            "provider request completed",
            extra={
                "api_key": "must-not-leak",
                "nested": {"token": "must-not-leak", "values": [{"secret": "must-not-leak"}]},
            },
        )
    finally:
        reset_scope(token)

    record = json.loads(log_file.read_text(encoding="utf-8"))

    assert record["msg"] == "provider request completed"
    assert record["request_id"] == _request_id
    assert record["api_key"] == _redacted_value
    assert record["nested"]["token"] == _redacted_value
    assert record["nested"]["values"][0]["secret"] == _redacted_value
    assert record["time"].endswith("Z")
    assert "%f" not in record["time"]
