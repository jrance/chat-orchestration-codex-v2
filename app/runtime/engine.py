"""Runtime engine adapters that produce OpenAI Responses-compatible events."""

from __future__ import annotations

import asyncio
import copy
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, AsyncIterator, Dict, Iterable, List, Optional

from app.api.deps import ExecutionContext
from app.compiler.registry import get as get_compiled_graph
from app.compiler.types import OrchestratorState
from app.runtime.state_store import RunStateRecord, get_run_state_store
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


def _copy_sequence(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [copy.deepcopy(item) for item in value]
    return [copy.deepcopy(value)]


def _coerce_message(item: Any) -> Dict[str, Any]:
    if isinstance(item, dict):
        return item
    return {"content": item}


def _merge_messages(existing: Any, incoming: Any) -> List[Dict[str, Any]]:
    merged: List[Dict[str, Any]] = []
    for message in _copy_sequence(existing):
        merged.append(_coerce_message(message))
    for message in _copy_sequence(incoming):
        merged.append(_coerce_message(message))
    return merged


def _merge_mapping(existing: Any, incoming: Any) -> Dict[str, Any]:
    merged: Dict[str, Any] = {}
    if isinstance(existing, Mapping):
        merged.update(copy.deepcopy(existing))
    if incoming is None:
        return merged
    if isinstance(incoming, Mapping):
        merged.update(copy.deepcopy(incoming))
        return merged
    raise TypeError("Expected mapping value while merging orchestrator state")


def _merge_state(
    previous: OrchestratorState | None,
    incoming: Mapping[str, Any] | None,
) -> OrchestratorState:
    base: OrchestratorState = copy.deepcopy(previous) if previous is not None else {}  # type: ignore[assignment]
    if incoming:
        for key, value in incoming.items():
            if key == "messages":
                base["messages"] = _merge_messages(base.get("messages"), value)
            elif key == "input":
                base["input"] = _merge_mapping(base.get("input"), value)
            elif key == "scratch":
                base["scratch"] = _merge_mapping(base.get("scratch"), value)
            else:
                base[key] = copy.deepcopy(value)
    base.setdefault("messages", [])
    base.setdefault("input", {})
    base.setdefault("scratch", {})
    return base


def _resolve_metadata(
    previous: RunStateRecord | None,
    new_metadata: Mapping[str, Any] | None,
    state_in: Mapping[str, Any] | None,
) -> Dict[str, Any]:
    metadata: Dict[str, Any] = {}
    created_at: float | None = None
    invocations = 0

    if previous is not None:
        metadata.update(copy.deepcopy(previous.metadata))
        created_at = previous.metadata.get("created_at")
        invocations = int(previous.metadata.get("invocations", 0))

    if new_metadata:
        metadata.update(copy.deepcopy(dict(new_metadata)))

    metadata["invocations"] = invocations + 1

    if created_at is None:
        created_at = time.time()
    metadata.setdefault("created_at", created_at)
    metadata["updated_at"] = time.time()

    if state_in:
        metadata["last_input"] = copy.deepcopy(dict(state_in))

    return metadata


def execute_once(
    graph_id: str,
    run_id: str,
    state_in: Mapping[str, Any] | None = None,
    *,
    metadata: Mapping[str, Any] | None = None,
) -> OrchestratorState:
    """Invoke a compiled graph exactly once and persist the resulting state."""

    if not run_id:
        raise ValueError("run_id must be provided")

    app = get_compiled_graph(graph_id)
    if app is None:
        raise ValueError(f"Unknown graph_id '{graph_id}'")

    store = get_run_state_store()
    previous = store.get_state(run_id)
    previous_state = previous.state if previous else None

    merged_state = _merge_state(previous_state, state_in)
    merged_state["run_id"] = run_id

    config = {"configurable": {"thread_id": run_id}}
    output_state = app.invoke(copy.deepcopy(merged_state), config=config)  # type: ignore[arg-type]
    if not isinstance(output_state, dict):
        raise RuntimeError("Compiled graph returned a non-dict state")

    output_state = copy.deepcopy(output_state)
    output_state.setdefault("run_id", run_id)

    record_metadata = _resolve_metadata(previous, metadata, state_in)
    store.put_state(RunStateRecord(run_id=run_id, state=output_state, metadata=record_metadata))
    return output_state


def resume_run(
    graph_id: str,
    run_id: str,
    state_in: Mapping[str, Any] | None = None,
    *,
    metadata: Mapping[str, Any] | None = None,
) -> OrchestratorState:
    """Resume a previously-started run by replaying the compiled graph."""

    store = get_run_state_store()
    if store.get_state(run_id) is None:
        raise ValueError(f"Run '{run_id}' not found")
    return execute_once(graph_id, run_id, state_in, metadata=metadata)


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
