"""Verify generated artifacts owned by this repository."""

import logging
import tempfile
from pathlib import Path

from decidealot.logging_config import configure_logging

_log_level = "INFO"
_log_file = Path(tempfile.gettempdir()) / "decidealot-generate.log"
logger = logging.getLogger(__name__)


def main() -> None:
    """Report that native TypeSafe contract generation belongs to the providers."""

    configure_logging(_log_level, _log_file)
    logger.info("no generated artifacts are owned by decidealot")


if __name__ == "__main__":
    main()
