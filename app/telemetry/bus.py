"""Centralized telemetry publishing helpers with gating logic."""

from __future__ import annotations

from typing import Callable, Mapping, Optional

from app.api.deps import ExecutionContext
from app.api.models import TelemetryLevel

Publisher = Callable[[Mapping[str, object]], None]
_publisher: Publisher | None = None


def configure_publisher(publisher: Publisher | None) -> None:
    """Configure a sink used for telemetry emission (primarily for tests)."""

    global _publisher
    _publisher = publisher


def publish_telemetry(
    ctx: Optional[ExecutionContext],
    event: Mapping[str, object],
    *,
    level: TelemetryLevel = TelemetryLevel.BASIC,
) -> None:
    """Publish a telemetry event when enabled for the current execution context."""

    if ctx is None:
        return
    headers = getattr(ctx, "headers", None)
    if headers is None:
        return

    target = headers.telemetry or TelemetryLevel.NONE
    if target is TelemetryLevel.NONE:
        return
    if target is TelemetryLevel.BASIC and level is TelemetryLevel.VERBOSE:
        return

    if _publisher is not None:
        _publisher(event)


__all__ = ["configure_publisher", "publish_telemetry"]
