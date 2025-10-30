"""Telemetry event helpers for orchestration runtime components."""

from __future__ import annotations

from typing import Any, Mapping

from .models import TelemetryEvent

CONCURRENT_RUN_STARTED = "telemetry.concurrent.start"
CONCURRENT_RUN_COMPLETED = "telemetry.concurrent.completed"
CONCURRENT_CHILD_STARTED = "telemetry.concurrent.child.start"
CONCURRENT_CHILD_COMPLETED = "telemetry.concurrent.child.completed"
CONCURRENT_CHILD_TIMEOUT = "telemetry.concurrent.child.timeout"
CONCURRENT_CHILD_CANCELLED = "telemetry.concurrent.child.cancelled"
CONCURRENT_CHILD_ERROR = "telemetry.concurrent.child.error"
CONCURRENT_CHILD_DELTA = "telemetry.concurrent.child.delta"
CONCURRENT_MERGE_DECISION = "telemetry.concurrent.merge.decision"


def make_event(event: str, payload: Mapping[str, Any]) -> TelemetryEvent:
    """Create a telemetry event with a shallow-copied payload."""

    return TelemetryEvent(event=event, payload=dict(payload))


__all__ = [
    "CONCURRENT_CHILD_CANCELLED",
    "CONCURRENT_CHILD_COMPLETED",
    "CONCURRENT_CHILD_DELTA",
    "CONCURRENT_CHILD_ERROR",
    "CONCURRENT_CHILD_STARTED",
    "CONCURRENT_CHILD_TIMEOUT",
    "CONCURRENT_MERGE_DECISION",
    "CONCURRENT_RUN_COMPLETED",
    "CONCURRENT_RUN_STARTED",
    "make_event",
]
