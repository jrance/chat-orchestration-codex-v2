"""Runtime engine adapters that produce OpenAI Responses-compatible events."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, AsyncIterator, Dict, Iterable, List, Optional

from app.api.deps import ExecutionContext
from app.telemetry.models import TelemetryEvent, TelemetryLevel
from app.telemetry.streamer import TelemetryStreamer


@dataclass(slots=True)
class RunStreamEvent:
    """Event yielded from the runtime streaming adapter."""

    event: str
    data: Dict[str, Any]


@dataclass(slots=True)
class ExecutionResult:
    """Final execution output for synchronous runs."""

    run_id: str
    thread_id: str
    output_text: str
    usage: Dict[str, int]
    response: Dict[str, Any]


def _flatten_input(user_input: Any) -> str:
    if user_input is None:
        return ""
    if isinstance(user_input, str):
        return user_input
    if isinstance(user_input, dict):
        return " ".join(f"{k}:{_flatten_input(v)}" for k, v in user_input.items())
    if isinstance(user_input, Iterable) and not isinstance(user_input, (bytes, bytearray)):
        return " ".join(_flatten_input(item) for item in user_input)
    return str(user_input)


def _chunk_output(text: str) -> List[str]:
    if not text:
        return []
    words = text.split()
    chunks: List[str] = []
    buffer: List[str] = []
    for word in words:
        buffer.append(word)
        if len(buffer) >= 3:
            chunks.append(" ".join(buffer) + " ")
            buffer.clear()
    if buffer:
        suffix = " ".join(buffer)
        if text.endswith(" "):
            suffix += " "
        chunks.append(suffix)
    return chunks


def _usage(text_in: str, text_out: str) -> Dict[str, int]:
    return {
        "input_tokens": max(1, len(text_in.split())) if text_in else 0,
        "output_tokens": max(1, len(text_out.split())) if text_out else 0,
    }


async def run_stream(
    ir: Dict[str, Any],
    user_input: Any,
    context: ExecutionContext,
    telemetry: Optional[TelemetryStreamer] = None,
) -> AsyncIterator[RunStreamEvent]:
    """Synthesize a stream of responses events for the provided IR."""

    context.ensure_run_ids()
    run_id = context.run_id or ""
    thread_id = context.thread_id or ""

    flattened_input = _flatten_input(user_input)
    output_text = f"Echo: {flattened_input}".strip()
    usage = _usage(flattened_input, output_text)

    if telemetry and telemetry.enabled():
        await telemetry.publish(
            TelemetryEvent(
                event="telemetry.run_started",
                payload={"runId": run_id, "nodeCount": len(ir.get("nodes", []))},
            )
        )

    yield RunStreamEvent(
        event="response.created",
        data={
            "run_id": run_id,
            "thread_id": thread_id,
            "status": "in_progress",
        },
    )

    for chunk in _chunk_output(output_text):
        await asyncio.sleep(0)
        yield RunStreamEvent(
            event="response.output_text.delta",
            data={
                "run_id": run_id,
                "thread_id": thread_id,
                "delta": chunk,
                "role": "assistant",
            },
        )

    completed_payload = {
        "run_id": run_id,
        "thread_id": thread_id,
        "output_text": output_text,
        "usage": usage,
        "status": "completed",
    }
    yield RunStreamEvent(event="response.completed", data=completed_payload)

    if telemetry and telemetry.enabled():
        await telemetry.publish(
            TelemetryEvent(
                event="telemetry.run_completed",
                payload={"runId": run_id, "status": "completed"},
            )
        )


async def run_once(
    ir: Dict[str, Any],
    user_input: Any,
    context: ExecutionContext,
    telemetry: Optional[TelemetryStreamer] = None,
) -> ExecutionResult:
    """Execute the orchestration once and return the final output."""

    context.ensure_run_ids()
    final_event: RunStreamEvent | None = None

    async for event in run_stream(ir, user_input, context, telemetry):
        if event.event == "response.completed":
            final_event = event

    if final_event is None:  # pragma: no cover - defensive
        raise RuntimeError("run_stream did not emit completion event")

    payload = final_event.data
    output_text = str(payload.get("output_text", ""))
    usage = payload.get("usage", _usage(_flatten_input(user_input), output_text))

    if telemetry and telemetry.enabled():
        await telemetry.publish(
            TelemetryEvent(
                event="telemetry.run_summary",
                payload={"runId": context.run_id, "outputTokens": usage.get("output_tokens", 0)},
            )
        )

    return ExecutionResult(
        run_id=context.run_id or "",
        thread_id=context.thread_id or "",
        output_text=output_text,
        usage=dict(usage),
        response=payload,
    )

