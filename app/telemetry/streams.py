"""Helpers for normalizing SSE event payloads and emitting aliases."""

from __future__ import annotations

from typing import Any, Dict, Iterable, Tuple

# Legacy runtime events that should be normalized to the official Responses names.
_LEGACY_EVENT_MAP: Dict[str, str] = {
    "response.function_call_arguments.delta": "response.tool_call.arguments.delta",
    "response.tool_calls.arguments.delta": "response.tool_call.arguments.delta",
    "response.function_call_arguments.done": "response.tool_call.arguments.done",
    "response.tool_calls.arguments.done": "response.tool_call.arguments.done",
}

# Aliases we continue to emit for downstream compatibility while clients migrate.
_EVENT_ALIASES: Dict[str, Tuple[str, ...]] = {
    "response.tool_result.created": ("response.tool_call.result.created",),
    "response.tool_result.done": ("response.tool_call.result.done",),
}


def canonical_event_type(event_type: str) -> str:
    """Return the canonical Responses event type for the given event name."""

    normalized = event_type.strip()
    return _LEGACY_EVENT_MAP.get(normalized, normalized)


def alias_event_types(event_type: str) -> Tuple[str, ...]:
    """Return event aliases that should be emitted alongside the canonical form."""

    return _EVENT_ALIASES.get(event_type, ())


def response_event(
    event_type: str,
    *,
    run_id: str | None = None,
    thread_id: str | None = None,
    data: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Build a standard event payload with optional run/thread identifiers."""

    payload: Dict[str, Any] = {"type": event_type}
    if data:
        payload.update(data)
    if run_id is not None:
        payload.setdefault("run_id", run_id)
    if thread_id is not None:
        payload.setdefault("thread_id", thread_id)
    return payload


__all__ = ["alias_event_types", "canonical_event_type", "response_event"]
