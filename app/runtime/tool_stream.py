"""Streaming helpers for assembling tool-call argument payloads."""

from __future__ import annotations

from typing import Any, Dict, List


class ToolArgAggregator:
    """Aggregate argument deltas per tool_call_id and emit the full string on completion."""

    def __init__(self) -> None:
        self._buffers: Dict[str, List[str]] = {}
        self._completed: set[str] = set()

    def _coerce_chunk(self, value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        return str(value)

    def add_delta(self, tool_call_id: str, chunk: Any) -> None:
        """Append a delta chunk for the given tool call."""
        call_id = (tool_call_id or "").strip()
        if not call_id:
            raise ValueError("tool_call_id is required for arguments.delta events")
        if call_id in self._completed:
            raise ValueError(f"arguments.delta received after completion for '{call_id}'")
        text = self._coerce_chunk(chunk)
        if text:
            self._buffers.setdefault(call_id, []).append(text)

    def finalize(self, tool_call_id: str, final_chunk: Any) -> str:
        """Return the fully concatenated arguments for the tool call."""
        call_id = (tool_call_id or "").strip()
        if not call_id:
            raise ValueError("tool_call_id is required for arguments.done events")
        if call_id in self._completed:
            raise ValueError(f"duplicate arguments.done received for '{call_id}'")
        parts = self._buffers.pop(call_id, [])
        tail = self._coerce_chunk(final_chunk)
        if tail:
            parts.append(tail)
        full = "".join(parts)
        self._completed.add(call_id)
        return full


__all__ = ["ToolArgAggregator"]
