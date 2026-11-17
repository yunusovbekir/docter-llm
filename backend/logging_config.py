"""Stdlib logging setup: console and rotating file under ``logs_dir``."""

import logging
import sys
from logging.handlers import RotatingFileHandler

from backend.config import Settings


def configure_logging(settings: Settings) -> None:
    """Configure the root logger with console and file handlers.

    Safe to call repeatedly: handlers installed by a previous call are replaced.
    """
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, settings.HANDLER_MARKER, False):
            root.removeHandler(handler)
            handler.close()

    formatter = logging.Formatter(settings.LOG_FORMAT)
    console = logging.StreamHandler(sys.stderr)
    settings.logs_dir.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(
        settings.logs_dir / settings.LOG_FILE_NAME,
        maxBytes=settings.LOG_MAX_BYTES,
        backupCount=settings.LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    for handler in (console, file_handler):
        handler.setFormatter(formatter)
        setattr(handler, settings.HANDLER_MARKER, True)
        root.addHandler(handler)

    root.setLevel(settings.log_level)
