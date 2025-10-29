"""Structured logging configuration utilities."""

from __future__ import annotations

import logging
import re
from functools import partial
from typing import Any, Callable

import structlog

_EMAIL_PATTERN = re.compile(r"(?i)([a-z0-9_.+-]+@[a-z0-9.-]+\.[a-z]{2,})")
_REDACTED = "[REDACTED]"
Processor = Callable[[Any, str, dict[str, Any]], dict[str, Any]]


def _redact_value(value: Any) -> Any:
    """Recursively redact email-like patterns from values."""
    if isinstance(value, str):
        return _EMAIL_PATTERN.sub(_REDACTED, value)
    if isinstance(value, dict):
        return {k: _redact_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        container = type(value)
        return container(_redact_value(v) for v in value)
    return value


def _redactor(
    redact_enabled: bool, _: Any, __: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    """Structlog processor that optionally redacts sensitive values."""
    if not redact_enabled:
        return event_dict

    return {key: _redact_value(val) for key, val in event_dict.items()}


def _setup_stdlib_handler(processor_chain: list[Processor], level: int) -> None:
    """Configure stdlib logging to route through structlog processors."""
    formatter = structlog.stdlib.ProcessorFormatter(
        processor=structlog.processors.JSONRenderer(),
        foreign_pre_chain=processor_chain,
    )

    handler = logging.StreamHandler()
    handler.setFormatter(formatter)

    logging.basicConfig(level=level, handlers=[handler], force=True)


def configure_logging(level: str = "INFO", redact: bool = False) -> None:
    """
    Configure structlog + stdlib logging for JSON output and contextvars support.
    """
    numeric_level = getattr(logging, level.upper(), logging.INFO)

    base_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        partial(_redactor, redact),
    ]

    _setup_stdlib_handler(base_processors.copy(), numeric_level)
    logging.getLogger().setLevel(numeric_level)

    structlog.configure(
        processors=[
            *base_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
