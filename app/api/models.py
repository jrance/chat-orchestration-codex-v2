"""Shared API models across v1 endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict

from fastapi import Request

from pydantic import BaseModel, Field, field_validator


def _default_timestamp() -> str:
    return (
        datetime.now(tz=timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


class TelemetryLevel(str, Enum):
    """Supported telemetry verbosity levels."""

    NONE = "none"
    BASIC = "basic"
    VERBOSE = "verbose"

    @classmethod
    def from_raw(cls, raw: str | None) -> "TelemetryLevel":
        if not raw:
            return cls.NONE
        try:
            return cls(raw.strip().lower())
        except ValueError:
            return cls.NONE


class ExecutionHeaders(BaseModel):
    """Canonical representation of the inbound execution headers."""

    tenant_id: str | None = None
    telemetry: TelemetryLevel = Field(default=TelemetryLevel.NONE)
    correlation_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    request_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = Field(default_factory=_default_timestamp)
    client_id: str | None = None
    extra_headers: Dict[str, str] = Field(default_factory=dict)

    @field_validator("telemetry", mode="before")
    @classmethod
    def _coerce_telemetry(cls, value: Any) -> TelemetryLevel:
        if value is None or value == "":
            return TelemetryLevel.NONE
        if isinstance(value, TelemetryLevel):
            return value
        return TelemetryLevel.from_raw(str(value))

    @classmethod
    def from_request(cls, request: Request) -> "ExecutionHeaders":
        headers = request.headers
        data: Dict[str, Any] = {
            "tenant_id": headers.get("X-Tenant-Id") or headers.get("X-Tenant-ID"),
            "telemetry": TelemetryLevel.from_raw(headers.get("X-Telemetry")),
            "client_id": headers.get("X-Client-Id") or headers.get("X-Client-ID"),
            "extra_headers": {
                name: value
                for name, value in headers.items()
                if name.startswith("X-Extra-")
            },
        }

        correlation = headers.get("X-Correlation-Id") or headers.get("X-Correlation-ID")
        if correlation:
            data["correlation_id"] = correlation

        request_id = headers.get("X-Request-Id") or headers.get("X-Request-ID")
        if request_id:
            data["request_id"] = request_id

        timestamp = headers.get("X-Timestamp")
        if timestamp:
            data["timestamp"] = timestamp

        return cls(**data)


class ToolCallPayload(BaseModel):
    """Normalized tool call envelope."""

    id: str
    name: str
    arguments: Dict[str, Any] = Field(default_factory=dict)


class ToolResultPayload(BaseModel):
    """Normalized tool result envelope."""

    id: str
    name: str
    output: Any


__all__ = ["ExecutionHeaders", "TelemetryLevel", "ToolCallPayload", "ToolResultPayload"]
