"""Header policy helpers for outbound OpenAI-compatible calls."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from app.config.settings import settings


H_CORRELATION_ID = "X-Correlation-Id"
H_REQUEST_ID = "X-Request-Id"
H_TIMESTAMP = "X-Timestamp"
H_TENANT_ID = "X-Tenant-Id"
H_CLIENT_ID = "X-Client-Id"
H_TELEMETRY = "X-Telemetry"


def _utc_timestamp() -> str:
    return (
        datetime.now(tz=timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def build_default_headers(
    tenant_id: Optional[str],
    correlation_id: Optional[str],
    request_id: Optional[str],
    telemetry: Optional[str] = None,
) -> dict[str, str]:
    """Build the core headers expected by the Apigee gateway."""
    headers: dict[str, str] = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        H_TIMESTAMP: _utc_timestamp(),
    }

    if correlation_id:
        headers[H_CORRELATION_ID] = correlation_id
    if request_id:
        headers[H_REQUEST_ID] = request_id
    if tenant_id:
        headers[H_TENANT_ID] = tenant_id
    if settings.apigee_client_id:
        headers[H_CLIENT_ID] = settings.apigee_client_id
    if telemetry:
        headers[H_TELEMETRY] = telemetry

    # Merge optional static headers from configuration without clobbering explicit inputs.
    for key, value in settings.apigee_extra_headers().items():
        headers.setdefault(key, str(value))

    return headers
