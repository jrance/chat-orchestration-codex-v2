"""Shared FastAPI dependencies for request header parsing and validation."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import HTTPException, Request, status

from app.telemetry.models import TelemetryLevel


def _default_timestamp() -> str:
    return (
        datetime.now(tz=timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


@dataclass(slots=True)
class ExecutionHeaders:
    """Canonical representation of the inbound execution headers."""

    tenant_id: str
    telemetry: TelemetryLevel
    correlation_id: str
    request_id: str
    timestamp: str
    client_id: Optional[str]
    extra_headers: Dict[str, str]


@dataclass(slots=True)
class ExecutionContext:
    """Execution context shared with runtime components."""

    headers: ExecutionHeaders
    run_id: Optional[str] = None
    thread_id: Optional[str] = None

    def ensure_run_ids(self) -> None:
        """Generate run/thread identifiers if not already populated."""
        if not self.run_id:
            self.run_id = uuid.uuid4().hex
        if not self.thread_id:
            self.thread_id = f"thread-{self.run_id}"

    def to_http_headers(self) -> Dict[str, str]:
        """Return headers appropriate for downstream HTTP calls."""
        headers = {
            "X-Tenant-Id": self.headers.tenant_id,
            "X-Correlation-Id": self.headers.correlation_id,
            "X-Request-Id": self.headers.request_id,
            "X-Timestamp": self.headers.timestamp,
        }
        if self.headers.client_id:
            headers["X-Client-Id"] = self.headers.client_id
        if self.headers.telemetry is not TelemetryLevel.NONE:
            headers["X-Telemetry"] = self.headers.telemetry.value
        headers.update(self.headers.extra_headers)
        return headers


def _parse_telemetry(raw: str | None) -> TelemetryLevel:
    if raw is None:
        return TelemetryLevel.NONE
    try:
        return TelemetryLevel(raw.lower())
    except ValueError as exc:  # pragma: no cover - defensive branch
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid X-Telemetry header (expected none|basic|verbose)",
        ) from exc


def parse_execution_headers(request: Request) -> ExecutionHeaders:
    """Extract execution headers and provide sane defaults."""

    headers = request.headers
    tenant_id = headers.get("X-Tenant-Id")
    if not tenant_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-Tenant-Id header is required",
        )

    telemetry = _parse_telemetry(headers.get("X-Telemetry"))
    correlation_id = headers.get("X-Correlation-Id", str(uuid.uuid4()))
    request_id = headers.get("X-Request-Id", str(uuid.uuid4()))
    timestamp = headers.get("X-Timestamp", _default_timestamp())
    client_id = headers.get("X-Client-Id")

    extra: Dict[str, str] = {}
    for name, value in headers.items():
        if not name.startswith("X-Extra-"):
            continue
        extra[name] = value

    return ExecutionHeaders(
        tenant_id=tenant_id,
        telemetry=telemetry,
        correlation_id=correlation_id,
        request_id=request_id,
        timestamp=timestamp,
        client_id=client_id,
        extra_headers=extra,
    )


def ensure_tenant_matches(ir: Dict[str, Any], tenant_id: str) -> None:
    """Validate that the inbound header matches the IR's declared tenant."""
    meta = ir.get("meta") if isinstance(ir, dict) else None
    ir_tenant = meta.get("tenantId") if isinstance(meta, dict) else None
    if ir_tenant is None:
        return
    if ir_tenant != tenant_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Tenant mismatch: header '{tenant_id}' does not match IR meta '{ir_tenant}'",
        )

