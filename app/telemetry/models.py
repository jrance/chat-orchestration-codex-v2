"""Telemetry models and helpers."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping


class TelemetryLevel(str, Enum):
    """Supported telemetry verbosity levels."""

    NONE = "none"
    BASIC = "basic"
    VERBOSE = "verbose"


@dataclass(slots=True)
class TelemetryEvent:
    """Canonical telemetry event representation."""

    event: str
    payload: Mapping[str, Any]


def redact_payload(payload: Mapping[str, Any], redact_enabled: bool) -> dict[str, Any]:
    """Apply best-effort redaction when enabled."""
    if not redact_enabled:
        return dict(payload)

    redacted: dict[str, Any] = {}
    for key, value in payload.items():
        if isinstance(value, str) and value:
            redacted[key] = "[redacted]"
        elif isinstance(value, (int, float, bool)) or value is None:
            redacted[key] = value
        else:
            redacted[key] = "[redacted]"
    return redacted

