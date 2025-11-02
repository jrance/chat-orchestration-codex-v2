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
    policy = getattr(ctx, "telemetry", None)
    if policy is not None:
        target = policy.level if policy.enabled else TelemetryLevel.NONE
    else:
        headers = getattr(ctx, "headers", None)
        if headers is None:
            return
        target = headers.telemetry or TelemetryLevel.NONE
    order = {
        TelemetryLevel.NONE: 0,
        TelemetryLevel.BASIC: 1,
        TelemetryLevel.VERBOSE: 2,
        TelemetryLevel.TRACE: 3,
    }
    target_rank = order.get(target, 0)
    event_rank = order.get(level, 1)

    if target_rank < event_rank or target_rank == 0:
        return

    if _publisher is not None:
        _publisher(event)


__all__ = ["configure_publisher", "publish_telemetry"]
