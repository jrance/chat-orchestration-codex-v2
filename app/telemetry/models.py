"""Telemetry models and helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from app.api.models import TelemetryLevel


@dataclass(slots=True)
class TelemetryEvent:
    """Canonical telemetry event representation."""

    event: str
    payload: Mapping[str, object]
