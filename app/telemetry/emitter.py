"""Telemetry emission helpers that respect per-request policies."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping, MutableMapping, Optional

from app.api.deps import TelemetryContext
from app.telemetry.models import TelemetryEvent
from app.telemetry.redaction import redact_headers, redact_text
from app.telemetry.streamer import TelemetryStreamer


def _serialize_payload(payload: Any) -> str:
    if isinstance(payload, str):
        return payload
    try:
        return json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    except (TypeError, ValueError):
        return repr(payload)


@dataclass(slots=True)
class TelemetryEmitter:
    """Helper bound to a streamer/context to emit sanitized telemetry events."""

    streamer: Optional[TelemetryStreamer]
    context: Optional[TelemetryContext]

    def _enabled(self) -> bool:
        return (
            self.streamer is not None
            and self.context is not None
            and self.context.enabled
            and self.streamer.enabled()
        )

    def enabled(self) -> bool:
        return self._enabled()

    def _redacted_text(self, value: Any) -> str:
        ctx = self.context
        assert ctx is not None
        serialized = _serialize_payload(value)
        return redact_text(serialized, ctx.redaction, ctx.payload_max_chars)

    async def _publish(self, event_type: str, payload: Mapping[str, Any]) -> None:
        if not self._enabled():
            return
        await self.streamer.publish(TelemetryEvent(event=event_type, payload=dict(payload)))

    async def emit_span_start(
        self,
        span_id: str,
        name: str,
        *,
        metadata: Mapping[str, Any] | None = None,
        start_time_ms: float | None = None,
    ) -> None:
        if not self._enabled():
            return
        payload: MutableMapping[str, Any] = {
            "span_id": span_id,
            "name": name,
        }
        if start_time_ms is not None:
            payload["start_ms"] = float(start_time_ms)
        if metadata:
            payload["metadata"] = dict(metadata)
        await self._publish("telemetry.span.start", payload)

    async def emit_span_end(
        self,
        span_id: str,
        *,
        duration_ms: float | None = None,
        status: str | None = None,
        usage: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        if not self._enabled():
            return
        payload: MutableMapping[str, Any] = {"span_id": span_id}
        if duration_ms is not None:
            payload["duration_ms"] = float(duration_ms)
        if status:
            payload["status"] = status
        if usage:
            payload["usage"] = dict(usage)
        if metadata:
            payload["metadata"] = dict(metadata)
        await self._publish("telemetry.span.end", payload)

    async def emit_llm_request(
        self,
        *,
        provider: str,
        model: str,
        endpoint: str,
        headers: Mapping[str, str],
        body: Any,
        request_id: str | None = None,
    ) -> None:
        if not self._enabled():
            return
        ctx = self.context
        assert ctx is not None
        payload: MutableMapping[str, Any] = {
            "provider": provider,
            "model": model,
            "endpoint": endpoint,
            "headers": redact_headers(headers),
        }
        if request_id:
            payload["request_id"] = request_id
        if ctx.level.allows_full_payloads():
            payload["body"] = self._redacted_text(body)
        elif ctx.allows_payload_previews():
            payload["body_preview"] = self._redacted_text(body)
        await self._publish("telemetry.llm.request", payload)

    async def emit_llm_response(
        self,
        *,
        provider: str,
        model: str,
        request_id: str | None,
        status_code: int,
        latency_ms: float | None,
        body: Any,
        usage: Mapping[str, Any] | None = None,
        tool_calls: Any | None = None,
    ) -> None:
        if not self._enabled():
            return
        ctx = self.context
        assert ctx is not None
        payload: MutableMapping[str, Any] = {
            "provider": provider,
            "model": model,
            "status_code": int(status_code),
        }
        if request_id:
            payload["request_id"] = request_id
        if latency_ms is not None:
            payload["latency_ms"] = float(latency_ms)
        if usage:
            payload["usage"] = dict(usage)
        if tool_calls is not None:
            payload["tool_calls_preview"] = self._redacted_text(tool_calls)
        if ctx.level.allows_full_payloads():
            payload["body"] = self._redacted_text(body)
        elif ctx.allows_payload_previews():
            payload["body_preview"] = self._redacted_text(body)
        await self._publish("telemetry.llm.response", payload)

    async def emit_note(
        self,
        message: str,
        *,
        detail: Mapping[str, Any] | None = None,
        level: str = "info",
    ) -> None:
        if not self._enabled():
            return
        payload: MutableMapping[str, Any] = {
            "level": level,
            "message": message,
        }
        if detail:
            payload["detail"] = dict(detail)
        await self._publish("telemetry.note", payload)


__all__ = ["TelemetryEmitter"]
