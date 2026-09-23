"""Production CLI entry point."""

import logging

import uvicorn

from decidealot.app import create_app
from decidealot.logging_config import configure_logging
from decidealot.settings import Settings

logger = logging.getLogger(__name__)


def main() -> None:
    """Load validated configuration, then run the public HTTP server."""

    settings = Settings()
    configure_logging(settings.log_level, settings.log_file)
    logger.info(
        "configuration loaded",
        extra={
            "listen_host": settings.listen_host,
            "listen_port": settings.listen_port,
            "device": settings.device,
            "image_variant": settings.image_variant,
            "provider_idle_unload_seconds": settings.provider_idle_unload_seconds,
            "auth_enabled": settings.api_key is not None,
        },
    )
    uvicorn.run(
        create_app(settings),
        host=settings.listen_host,
        port=settings.listen_port,
        log_config=None,
    )
