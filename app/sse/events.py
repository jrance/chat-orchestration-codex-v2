"""Helpers for building SSE payloads with consistent tool metadata."""

from __future__ import annotations

from typing import Any, Dict

from app.telemetry.streams import response_event


def tool_event(
    event_type: str,
    *,
    index: int,
    tool_call_id: str | None,
    tool_name: str | None = None,
    run_id: str | None = None,
    thread_id: str | None = None,
    data: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Return a tool-related SSE payload that always includes index metadata."""

    payload = response_event(event_type, run_id=run_id, thread_id=thread_id, data=data or {})
    payload["index"] = int(index)
    if tool_call_id is not None:
        payload.setdefault("tool_call_id", tool_call_id)
    if tool_name:
        payload.setdefault("name", tool_name)
    return payload


def tool_result_event(
    event_type: str,
    *,
    index: int,
    tool_call_id: str,
    tool_name: str,
    run_id: str | None = None,
    thread_id: str | None = None,
    output: Any | None = None,
    redacted: bool = False,
    error: bool = False,
    extra: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Return a tool result SSE payload with optional output metadata."""

    data: Dict[str, Any] = {
        "tool_call_id": tool_call_id,
        "name": tool_name,
    }
    if output is not None:
        data["output"] = output
    if redacted:
        data["redacted"] = True
    if error:
        data["error"] = True
    if extra:
        data.update(extra)
    return tool_event(
        event_type,
        index=index,
        tool_call_id=tool_call_id,
        tool_name=tool_name,
        run_id=run_id,
        thread_id=thread_id,
        data=data,
    )


__all__ = ["tool_event", "tool_result_event"]
