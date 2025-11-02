"""Telemetry redaction helpers used for payload/header sanitization."""

from __future__ import annotations

import hashlib
import re
from typing import Mapping

from app.api.models import TelemetryRedactionMode

EMAIL_PATTERN = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_PATTERN = re.compile(r"\+?[0-9][0-9\-\s]{7,}[0-9]")
TOKEN_PATTERN = re.compile(r"\b(sk-[A-Za-z0-9]{16,}|[A-Za-z0-9]{24,})\b")
BASE64_PATTERN = re.compile(r"\b[A-Za-z0-9+/]{32,}={0,2}\b")

HEADER_WHITELIST = {
    "x-tenant-id",
    "x-correlation-id",
    "x-request-id",
    "x-timestamp",
    "x-openai-org",
}


def _truncate(text: str, max_chars: int) -> str:
    if max_chars <= 0:
        return "[truncated]"
    if len(text) <= max_chars:
        return text
    removed = len(text) - max_chars
    return f"{text[:max_chars]}[truncated {removed} chars]"


def _mask_common_secrets(text: str) -> str:
    masked = TOKEN_PATTERN.sub("[token]", text)
    masked = BASE64_PATTERN.sub("[base64]", masked)
    masked = EMAIL_PATTERN.sub("[email]", masked)
    masked = PHONE_PATTERN.sub("[phone]", masked)
    masked = re.sub(r"Bearer\s+[A-Za-z0-9\-\._=]{10,}", "Bearer [token]", masked, flags=re.IGNORECASE)
    masked = re.sub(r"api[-_ ]?key\s*[:=]\s*[A-Za-z0-9]{8,}", "api-key: [token]", masked, flags=re.IGNORECASE)
    return masked


def redact_text(
    text: str,
    mode: TelemetryRedactionMode,
    max_chars: int,
) -> str:
    """Redact a text payload according to the provided mode."""
    normalized = text or ""

    if mode is TelemetryRedactionMode.FULL:
        return "[redacted]"

    if mode is TelemetryRedactionMode.SAFE:
        sanitized = _mask_common_secrets(normalized)
    else:
        # Minimum guard rails even in NONE mode.
        sanitized = TOKEN_PATTERN.sub("[token]", normalized)
        sanitized = sanitized.replace("Bearer ", "Bearer [token]")

    return _truncate(sanitized, max_chars)


def redact_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """Redact headers while keeping tenant/correlation identifiers visible."""

    sanitized: dict[str, str] = {}
    for name, value in headers.items():
        lowered = name.lower()
        if lowered in HEADER_WHITELIST:
            sanitized[name] = value
            continue
        if lowered in {"authorization", "proxy-authorization"}:
            sanitized[name] = "[redacted]"
            continue
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
        sanitized[name] = f"[hash:{digest}]"
    return sanitized


__all__ = ["redact_headers", "redact_text"]
