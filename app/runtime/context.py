"""Context propagation for runtime agent invocations."""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from typing import Optional

from app.api.deps import ExecutionContext
from app.telemetry.streamer import TelemetryStreamer


@dataclass(slots=True)
class RuntimeContext:
    """Per-run context shared with agent invocations."""

    execution: ExecutionContext | None
    telemetry: Optional[TelemetryStreamer]

    def http_headers(self) -> dict[str, str]:
        if self.execution is None:
            return {}
        return self.execution.to_http_headers()

    @property
    def run_id(self) -> str:
        if self.execution and self.execution.run_id:
            return self.execution.run_id
        return ""


_runtime_context: ContextVar[RuntimeContext | None] = ContextVar("runtime_context", default=None)


def set_runtime_context(ctx: RuntimeContext | None):
    """Push the runtime context and return the context token."""
    return _runtime_context.set(ctx)


def get_runtime_context() -> RuntimeContext | None:
    """Return the current runtime context if present."""
    return _runtime_context.get()


def reset_runtime_context(token) -> None:
    """Reset the runtime context to a previous value."""
    _runtime_context.reset(token)


__all__ = ["RuntimeContext", "get_runtime_context", "reset_runtime_context", "set_runtime_context"]
