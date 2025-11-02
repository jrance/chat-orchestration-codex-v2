"""Helpers for formatting tool outputs prior to streaming."""

from __future__ import annotations

import json
from typing import Any, Dict, Mapping, Sequence, Tuple

from app.api.deps import TelemetryContext
from app.telemetry.redaction import redact_text


def stringify_tool_output(output: Any) -> str:
    """Return a stable string representation of a tool result."""
    if output is None:
        return ""
    if isinstance(output, str):
        return output
    if isinstance(output, (bytes, bytearray)):
        try:
            return output.decode("utf-8", errors="ignore")
        except Exception:
            return ""
    try:
        return json.dumps(output, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    except (TypeError, ValueError):
        return str(output)


def truncate_text(text: str, max_chars: int) -> Tuple[str, int]:
    """Return a truncated string and the number of removed characters."""
    if max_chars <= 0:
        return "", len(text)
    if len(text) <= max_chars:
        return text, 0
    removed = len(text) - max_chars
    return f"{text[:max_chars]}[truncated {removed} chars]", removed


def tool_result_preview(output: Any, max_chars: int) -> Dict[str, Any]:
    """Build a lightweight preview summary for structured tool outputs."""
    if isinstance(output, Mapping):
        keys = list(output.keys())
        summary_keys = keys[:5]
        return {
            "type": "object",
            "count": len(keys),
            "keys": summary_keys,
        }
    if isinstance(output, Sequence) and not isinstance(output, (str, bytes, bytearray)):
        return {
            "type": "list",
            "count": len(output),
        }
    text = stringify_tool_output(output)
    preview, removed = truncate_text(text, max_chars)
    summary: Dict[str, Any] = {"type": "text", "preview": preview}
    if removed:
        summary["truncated"] = removed
    return summary


def safe_tool_result_excerpt(output: Any, telemetry: TelemetryContext | None) -> str | None:
    """Return a telemetry-safe excerpt of the tool output when allowed."""
    if telemetry is None or not telemetry.allows_payload_previews():
        return None
    text = stringify_tool_output(output)
    if not text:
        return None
    limit = max(0, int(telemetry.result_max_chars))
    if limit <= 0:
        return None
    return redact_text(text, telemetry.redaction, limit)


__all__ = [
    "safe_tool_result_excerpt",
    "stringify_tool_output",
    "tool_result_preview",
    "truncate_text",
]
