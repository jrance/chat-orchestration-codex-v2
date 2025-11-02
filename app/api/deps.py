"""Shared FastAPI dependencies for request header parsing and validation."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from fastapi import HTTPException, Request, status

from app.api.models import ExecutionHeaders, TelemetryLevel, TelemetryRedactionMode
from app.config.settings import settings


@dataclass(slots=True)
class ExecutionContext:
    """Execution context shared with runtime components."""

    headers: ExecutionHeaders = field(default_factory=ExecutionHeaders)
    run_id: Optional[str] = None
    thread_id: Optional[str] = None
    telemetry: Optional["TelemetryContext"] = None

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

        headers.update(self.headers.extra_headers)
        return headers


@dataclass(slots=True)
class TelemetryContext:
    """Per-request telemetry policy derived from headers/env."""

    level: TelemetryLevel
    redaction: TelemetryRedactionMode
    payload_max_chars: int
    result_max_chars: int
    enabled: bool = True

    def allows_payload_previews(self) -> bool:
        return self.enabled and self.level.allows_payloads()

    def allows_full_payloads(self) -> bool:
        return self.enabled and self.level.allows_full_payloads()


def build_telemetry_context(headers: ExecutionHeaders) -> TelemetryContext:
    """Merge header overrides with environment defaults to derive telemetry policy."""

    if not settings.telemetry_enabled:
        return TelemetryContext(
            level=TelemetryLevel.NONE,
            redaction=TelemetryRedactionMode.SAFE,
            payload_max_chars=int(settings.telemetry_payload_max_chars),
            result_max_chars=int(settings.tool_result_max_chars),
            enabled=False,
        )

    default_level = TelemetryLevel.from_raw(settings.telemetry_level)
    if headers.telemetry_override:
        level = headers.telemetry
    else:
        level = default_level
    enabled = level is not TelemetryLevel.NONE

    default_redaction = TelemetryRedactionMode.from_raw(settings.telemetry_redaction)
    redaction = headers.telemetry_redaction if headers.telemetry_redaction_override else default_redaction
    payload_limit = max(0, int(settings.telemetry_payload_max_chars))
    result_limit = max(0, int(settings.tool_result_max_chars))

    return TelemetryContext(
        level=level if enabled else TelemetryLevel.NONE,
        redaction=redaction,
        payload_max_chars=payload_limit,
        result_max_chars=result_limit,
        enabled=enabled,
    )


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
