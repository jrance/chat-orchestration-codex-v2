"""Shared logger access helpers."""

from __future__ import annotations

import structlog
from structlog.stdlib import BoundLogger


def get_logger(name: str | None = None) -> BoundLogger:
    """
    Return a bound structlog logger.

    Using this helper ensures all modules rely on the centrally configured logger.
    """
    return structlog.get_logger(name) if name else structlog.get_logger()


# Convenience logger for modules that don't need a custom name.
logger = get_logger("app")

__all__ = ["get_logger", "logger"]
