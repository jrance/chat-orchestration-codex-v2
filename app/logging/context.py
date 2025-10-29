"""Context management helpers for request-scoped logging."""

from __future__ import annotations

import contextvars
from typing import Optional

import structlog

correlation_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "correlation_id", default=None
)
request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "request_id", default=None
)
tenant_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "tenant_id", default=None
)


def bind_request_context(
    correlation_id: str, request_id: str, tenant_id: Optional[str] = None
) -> None:
    """Bind core request identifiers into the current context."""
    correlation_id_var.set(correlation_id)
    request_id_var.set(request_id)
    tenant_id_var.set(tenant_id)
    structlog.contextvars.bind_contextvars(
        correlation_id=correlation_id,
        request_id=request_id,
        tenant_id=tenant_id,
    )


def clear_request_context() -> None:
    """Clear any request context bound to the current task."""
    correlation_id_var.set(None)
    request_id_var.set(None)
    tenant_id_var.set(None)
    structlog.contextvars.clear_contextvars()


def current_context() -> dict[str, str | None]:
    """Return a shallow copy of the current logging context."""
    return {
        "correlation_id": correlation_id_var.get(),
        "request_id": request_id_var.get(),
        "tenant_id": tenant_id_var.get(),
    }
