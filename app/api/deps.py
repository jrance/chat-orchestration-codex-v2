"""Shared FastAPI dependencies for request header parsing and validation."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from fastapi import HTTPException, Request, status

from app.api.models import ExecutionHeaders, TelemetryLevel


@dataclass(slots=True)
class ExecutionContext:
    """Execution context shared with runtime components."""

    headers: ExecutionHeaders = field(default_factory=ExecutionHeaders)
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
        headers: Dict[str, str] = {
            "X-Correlation-Id": self.headers.correlation_id,
            "X-Request-Id": self.headers.request_id,
            "X-Timestamp": self.headers.timestamp,
        }
        if self.headers.tenant_id:
            headers["X-Tenant-Id"] = self.headers.tenant_id
        if self.headers.client_id:
            headers["X-Client-Id"] = self.headers.client_id

        telemetry = self.headers.telemetry or TelemetryLevel.NONE
        if telemetry is not TelemetryLevel.NONE:
            headers["X-Telemetry"] = telemetry.value

        headers.update(self.headers.extra_headers)
        return headers


def parse_execution_headers(request: Request) -> ExecutionHeaders:
    """Extract execution headers and provide sane defaults."""

    headers = ExecutionHeaders.from_request(request)
    if not headers.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-Tenant-Id header is required",
        )
    return headers


def ensure_tenant_matches(ir: Dict[str, Any], tenant_id: str | None) -> None:
    """Validate that the inbound header matches the IR's declared tenant."""
    if tenant_id is None:
        return
    meta = ir.get("meta") if isinstance(ir, dict) else None
    ir_tenant = meta.get("tenantId") if isinstance(meta, dict) else None
    if ir_tenant is None:
        return
    if ir_tenant != tenant_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Tenant mismatch: header '{tenant_id}' does not match IR meta '{ir_tenant}'",
        )
