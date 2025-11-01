"""Runtime engine adapters that produce OpenAI Responses-compatible events."""

from __future__ import annotations

import asyncio
import copy
import json
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, AsyncIterator, Dict, Iterable, List, Optional

from app.api.deps import ExecutionContext
from app.api.models import ExecutionHeaders
from app.compiler.registry import get as get_compiled_graph
from app.compiler.types import OrchestratorState
from app.ir.loader import build_runtime_plan
from app.runtime.agents import codeless as codeless_mod
from app.runtime.agents.codeless import stream_codeless
from app.runtime.context import (
    RuntimeContext,
    get_runtime_context,
    reset_runtime_context,
    set_runtime_context,
)
from app.runtime.patterns.concurrent import build_concurrent_runner
from app.runtime.patterns.groupchat import build_groupchat_runner
from app.runtime.state import Checkpoint, RunStatus, get_checkpointer
from app.runtime.state_store import RunStateRecord, get_run_state_store
from app.telemetry.models import TelemetryEvent, TelemetryLevel
from app.telemetry.streams import alias_event_types, canonical_event_type, response_event
from app.sse.events import tool_event, tool_result_event
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


def _coerce_thread_id(
    run_id: str,
    state: Mapping[str, Any],
    metadata: Mapping[str, Any],
    checkpoint: Checkpoint | None = None,
) -> str:
    thread_id = metadata.get("thread_id")
    if isinstance(thread_id, str) and thread_id:
        return thread_id
    state_thread = state.get("thread_id")
    if isinstance(state_thread, str) and state_thread:
        return state_thread
    if checkpoint and checkpoint.thread_id:
        return str(checkpoint.thread_id)
    return run_id


def _pause_payload(
    state: Mapping[str, Any],
    metadata: Mapping[str, Any],
) -> Dict[str, Any] | None:
    status_raw = state.get("status") or metadata.get("status")
    status = RunStatus.from_raw(
        str(status_raw) if status_raw is not None else None,
        RunStatus.RUNNING,
    )
    if status is not RunStatus.PAUSED:
        return None

    pause_reason = metadata.get("pause_reason") or state.get("pause_reason")
    pause_meta = metadata.get("pause_metadata") or state.get("pause_metadata")
    router_meta = metadata.get("router")
    router_hitl = router_meta.get("hitl") if isinstance(router_meta, Mapping) else None
    top_level_hitl = metadata.get("hitl") if isinstance(metadata.get("hitl"), Mapping) else None
    response_meta = metadata.get("response")
    response_hitl = None
    if isinstance(response_meta, Mapping):
        response_metadata = response_meta.get("metadata")
        if isinstance(response_metadata, Mapping):
            candidate = response_metadata.get("hitl")
            if isinstance(candidate, Mapping):
                response_hitl = candidate
    if not isinstance(pause_meta, Mapping):
        pause_meta = None
    if router_hitl and not pause_meta:
        pause_meta = {"hitl": router_hitl}
    if top_level_hitl and not pause_meta:
        pause_meta = {"hitl": top_level_hitl}
    if response_hitl and not pause_meta:
        pause_meta = {"hitl": response_hitl}
    if not pause_reason:
        candidate_hitl = router_hitl or top_level_hitl or response_hitl
        if isinstance(candidate_hitl, Mapping):
            pause_reason = candidate_hitl.get("reason")
    if not pause_reason and not pause_meta:
        return None
    payload: Dict[str, Any] = {
        "reason": pause_reason if isinstance(pause_reason, str) else None,
        "prompt": None,
        "targets": None,
        "metadata": None,
    }
    if isinstance(pause_meta, Mapping):
        hitl = pause_meta.get("hitl") if isinstance(pause_meta.get("hitl"), Mapping) else pause_meta
        if isinstance(hitl, Mapping):
            prompt = hitl.get("prompt")
            targets = hitl.get("targets")
            payload["prompt"] = prompt if isinstance(prompt, str) else None
            if isinstance(targets, list):
                payload["targets"] = [str(item) for item in targets]
            payload["metadata"] = copy.deepcopy(dict(hitl))
        else:
            payload["metadata"] = copy.deepcopy(dict(pause_meta))
    return payload


def _extract_usage_metadata(state: Mapping[str, Any], metadata: Mapping[str, Any]) -> Dict[str, Any] | None:
    usage = metadata.get("usage")
    if isinstance(usage, Mapping):
        return dict(usage)
    response = metadata.get("response")
    if isinstance(response, Mapping):
        usage_payload = response.get("usage")
        if isinstance(usage_payload, Mapping):
            return dict(usage_payload)
    scratch = state.get("scratch")
    if isinstance(scratch, Mapping):
        agents = scratch.get("agents")
        if isinstance(agents, Mapping) and agents:
            for entry in agents.values():
                if isinstance(entry, Mapping):
                    usage_payload = entry.get("usage")
                    if isinstance(usage_payload, Mapping):
                        return dict(usage_payload)
    usage_state = state.get("usage")
    if isinstance(usage_state, Mapping):
        return dict(usage_state)
    return None


def _build_run_status_payload(
    run_id: str,
    state: Mapping[str, Any],
    metadata: Mapping[str, Any],
    *,
    checkpoint: Checkpoint | None = None,
) -> Dict[str, Any]:
    status_raw = state.get("status") or metadata.get("status")
    status = RunStatus.from_raw(
        str(status_raw) if status_raw is not None else None,
        RunStatus.RUNNING,
    )
    thread_id = _coerce_thread_id(run_id, state, metadata, checkpoint)
    pause_payload = _pause_payload(state, metadata)
    usage = _extract_usage_metadata(state, metadata)
    metadata_payload = copy.deepcopy(dict(metadata))
    metadata_payload.pop("pause_metadata", None)
    metadata_payload.pop("pause_reason", None)
    payload: Dict[str, Any] = {
        "runId": run_id,
        "threadId": thread_id,
        "status": status.value,
        "sse": {"url": f"/v1/execute/stream?runId={run_id}"},
        "usage": usage,
        "metadata": metadata_payload or None,
        "pause": pause_payload,
    }
    if pause_payload is None:
        payload.pop("pause")
    if usage is None:
        payload.pop("usage")
    if not metadata_payload:
        payload.pop("metadata")
    return payload


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
    checkpoint: Checkpoint | None,
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
    elif checkpoint is not None:
        metadata.update(copy.deepcopy(checkpoint.metadata))
        created_at = metadata.get("created_at") or checkpoint.created_at.timestamp()
        invocations = int(metadata.get("invocations", 0))

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


def _user_message_from_input(user_input: Any) -> Dict[str, Any]:
    content = _flatten_input(user_input)
    if not content:
        return {}
    return {"role": "user", "content": content, "ts": time.time()}


def _last_assistant_text(state: Mapping[str, Any]) -> str:
    messages = state.get("messages")
    if not isinstance(messages, list):
        return ""
    for message in reversed(messages):
        if isinstance(message, Mapping) and message.get("role") == "assistant":
            return _flatten_input(message.get("content"))
    return ""


def _agent_scratch(state: Mapping[str, Any], node_id: str) -> Dict[str, Any]:
    scratch = state.get("scratch")
    if not isinstance(scratch, Mapping):
        return {}
    agents = scratch.get("agents")
    if not isinstance(agents, Mapping):
        return {}
    payload = agents.get(node_id)
    if isinstance(payload, Mapping):
        return dict(payload)
    return {}


def _router_state(state: Mapping[str, Any], node_id: str) -> Dict[str, Any]:
    scratch = state.get("scratch")
    if not isinstance(scratch, Mapping):
        return {}
    router_store = scratch.get("router")
    if not isinstance(router_store, Mapping):
        return {}
    payload = router_store.get(node_id)
    if isinstance(payload, Mapping):
        return dict(payload)
    return {}


async def execute_once(
    graph_id: str,
    run_id: str,
    state_in: Mapping[str, Any] | None = None,
    *,
    metadata: Mapping[str, Any] | None = None,
    context: ExecutionContext | None = None,
    telemetry: TelemetryStreamer | None = None,
) -> OrchestratorState:
    """Invoke a compiled graph exactly once and persist the resulting state."""

    if not run_id:
        raise ValueError("run_id must be provided")

    app = get_compiled_graph(graph_id)
    if app is None:
        raise ValueError(f"Unknown graph_id '{graph_id}'")

    checkpointer = get_checkpointer()
    store = get_run_state_store()
    previous = store.get_state(run_id)
    checkpoint = checkpointer.load_checkpoint(run_id)

    previous_state: OrchestratorState | None = None
    if previous and previous.state:
        previous_state = previous.state
    elif checkpoint:
        previous_state = checkpoint.state

    merged_state = _merge_state(previous_state, state_in)
    merged_state["run_id"] = run_id

    config = {"configurable": {"thread_id": run_id}}
    runtime_token = set_runtime_context(RuntimeContext(execution=context, telemetry=telemetry))
    try:
        output_state = await app.ainvoke(copy.deepcopy(merged_state), config=config)  # type: ignore[arg-type]
    finally:
        reset_runtime_context(runtime_token)
    if not isinstance(output_state, dict):
        raise RuntimeError("Compiled graph returned a non-dict state")

    output_state = copy.deepcopy(output_state)
    output_state.setdefault("run_id", run_id)

    raw_status = str(output_state.get("status") or "").strip() or "completed"
    run_status_enum = RunStatus.from_raw(raw_status, RunStatus.COMPLETED)
    output_state["status"] = run_status_enum.value
    pause_metadata = output_state.get("pause_metadata")
    if pause_metadata is not None and not isinstance(pause_metadata, Mapping):
        pause_metadata = None
    if pause_metadata:
        run_status_enum = RunStatus.PAUSED
        output_state["status"] = run_status_enum.value
    pause_reason = output_state.get("pause_reason")

    record_metadata = _resolve_metadata(previous, checkpoint, metadata, state_in)
    record_metadata["status"] = run_status_enum.value
    record_metadata["run_id"] = run_id
    thread_id = context.thread_id if context and context.thread_id else record_metadata.get("thread_id")
    if not thread_id:
        thread_id = run_id
    record_metadata["thread_id"] = thread_id
    if pause_reason:
        record_metadata["pause_reason"] = pause_reason
    if isinstance(pause_metadata, Mapping) and pause_metadata:
        record_metadata["pause_metadata"] = copy.deepcopy(pause_metadata)
    else:
        record_metadata.pop("pause_metadata", None)
        record_metadata.pop("pause_reason", None)

    store.put_state(RunStateRecord(run_id=run_id, state=output_state, metadata=record_metadata))
    checkpointer.save_checkpoint(
        run_id,
        output_state,
        status=run_status_enum,
        thread_id=str(thread_id),
        metadata=record_metadata,
    )
    return output_state


def get_run_status(run_id: str) -> Dict[str, Any]:
    """Return the latest run status payload for ``run_id``."""

    store = get_run_state_store()
    record = store.get_state(run_id)
    checkpoint_manager = get_checkpointer()
    checkpoint = checkpoint_manager.load_checkpoint(run_id)

    if record is None and checkpoint is None:
        raise ValueError(f"Run '{run_id}' not found")

    state: Mapping[str, Any]
    metadata: Mapping[str, Any]
    if record is not None:
        state = copy.deepcopy(record.state)
        metadata = copy.deepcopy(record.metadata)
    elif checkpoint is not None:
        state = copy.deepcopy(checkpoint.state)
        metadata = copy.deepcopy(checkpoint.metadata)
    else:  # pragma: no cover - defensive fallback
        state, metadata = {}, {}

    return _build_run_status_payload(run_id, state, metadata, checkpoint=checkpoint)


async def resume_run(
    run_id: str,
    *,
    resume_kind: str,
    payload: Mapping[str, Any] | None,
    tenant_id: str,
    request_id: str | None = None,
    correlation_id: str | None = None,
) -> Dict[str, Any]:
    """Resume a paused run and return the updated ``RunStatus`` payload."""

    store = get_run_state_store()
    checkpoint_manager = get_checkpointer()
    record = store.get_state(run_id)
    checkpoint = checkpoint_manager.load_checkpoint(run_id)

    if record is None and checkpoint is None:
        raise ValueError("RUN_NOT_FOUND")

    state_source: Mapping[str, Any]
    metadata_source: Mapping[str, Any]
    if record is not None:
        state_source = record.state
        metadata_source = record.metadata
    elif checkpoint is not None:
        state_source = checkpoint.state
        metadata_source = checkpoint.metadata
    else:  # pragma: no cover - defensive
        state_source, metadata_source = {}, {}

    status_raw = state_source.get("status") or metadata_source.get("status")
    status = RunStatus.from_raw(
        str(status_raw) if status_raw is not None else None,
        RunStatus.RUNNING,
    )
    if status is not RunStatus.PAUSED:
        raise ValueError("RUN_NOT_PAUSED")

    resume_state = copy.deepcopy(state_source)
    metadata_update = copy.deepcopy(dict(metadata_source))
    metadata_update["resume_kind"] = resume_kind
    metadata_update["resume_requested_at"] = time.time()
    if payload:
        metadata_update["resume_payload"] = copy.deepcopy(dict(payload))

    kind_lower = str(resume_kind).lower()

    if kind_lower == "router_choice":
        choice = (payload or {}).get("choice") if payload else None
        if not isinstance(choice, Mapping):
            raise ValueError("RESUME_CHOICE_REQUIRED")
        target_value = choice.get("target")
        if not isinstance(target_value, str) or not target_value.strip():
            raise ValueError("RESUME_CHOICE_TARGET_REQUIRED")
        target = target_value.strip()
        scratch = resume_state.get("scratch") or {}
        router_store = scratch.get("router") or {}
        selected_router_id: str | None = None
        selected_entry: Mapping[str, Any] | None = None
        for router_id, router_entry in router_store.items():
            if not isinstance(router_entry, Mapping):
                continue
            decision = router_entry.get("decision")
            if isinstance(decision, Mapping) and decision.get("source") == "paused":
                selected_router_id = str(router_id)
                selected_entry = router_entry
                break
        if selected_router_id is None or selected_entry is None:
            raise ValueError("RESUME_ROUTER_NOT_FOUND")
        target_map = selected_entry.get("target_to_child")
        if not isinstance(target_map, Mapping):
            target_map = {}
        child_labels = selected_entry.get("child_labels")
        if not isinstance(child_labels, Mapping):
            child_labels = {}
        node_id = target_map.get(target)
        label = target if node_id else None
        if node_id is None and target in child_labels:
            node_id = target
            label = child_labels.get(target, target)
        if node_id is None:
            lowered = target.lower()
            for candidate_label, candidate_node in target_map.items():
                if candidate_label.lower() == lowered:
                    node_id = candidate_node
                    label = candidate_label
                    break
        if node_id is None and isinstance(choice.get("targetNodeId"), str):
            node_id = choice["targetNodeId"]
            label = child_labels.get(node_id, target)
        if node_id is None:
            raise ValueError("RESUME_CHOICE_TARGET_NOT_FOUND")
        resume_state.setdefault("pending_router_choice", {})
        pending = resume_state["pending_router_choice"]
        if isinstance(pending, Mapping):
            merged = dict(pending)
        else:
            merged = {}
        merged[selected_router_id] = {
            "target": target,
            "targetNodeId": node_id,
            "targetLabel": label,
        }
        resume_state["pending_router_choice"] = merged
        resume_state["status"] = "running"
        resume_state.pop("pause_metadata", None)
        resume_state.pop("pause_reason", None)
    elif kind_lower == "user_message":
        message = (payload or {}).get("message")
        if isinstance(message, str):
            message_content = message.strip()
            if not message_content:
                raise ValueError("RESUME_MESSAGE_EMPTY")
            messages = list(resume_state.get("messages") or [])
            messages.append({"role": "user", "content": message_content, "ts": time.time()})
            resume_state["messages"] = messages
        elif isinstance(message, Mapping) or isinstance(message, list):
            messages = list(resume_state.get("messages") or [])
            messages.append(message)  # type: ignore[arg-type]
            resume_state["messages"] = messages
        else:
            raise ValueError("RESUME_MESSAGE_REQUIRED")
        resume_state["status"] = "running"
        resume_state.pop("pause_metadata", None)
        resume_state.pop("pause_reason", None)
    elif kind_lower in {"moderation_ack", "continue"}:
        resume_state["status"] = "running"
        resume_state.pop("pause_metadata", None)
        resume_state.pop("pause_reason", None)
    else:
        raise ValueError("RESUME_KIND_UNSUPPORTED")

    graph_id = f"runtime-{run_id}"
    if not get_compiled_graph(graph_id):
        raise ValueError("RESUME_GRAPH_NOT_FOUND")

    headers = ExecutionHeaders(
        tenant_id=tenant_id,
        correlation_id=correlation_id or uuid.uuid4().hex,
        request_id=request_id or uuid.uuid4().hex,
    )
    context = ExecutionContext(headers=headers, run_id=run_id, thread_id=_coerce_thread_id(run_id, resume_state, metadata_update, checkpoint))

    await execute_once(
        graph_id,
        run_id,
        resume_state,
        metadata=metadata_update,
        context=context,
    )
    return get_run_status(run_id)


async def run_stream(
    ir: Dict[str, Any],
    user_input: Any,
    context: ExecutionContext,
    telemetry: Optional[TelemetryStreamer] = None,
) -> AsyncIterator[RunStreamEvent]:
    """Stream Responses API-compatible events by invoking the provider."""

    context.ensure_run_ids()

    ok, plan, errors = build_runtime_plan(ir, runtime_vars={})
    if not ok:
        message = "; ".join(errors) if errors else "Failed to build runtime plan"
        raise ValueError(message)

    entry_id = str(plan.get("entryId") or "")
    node = (plan.get("nodeById") or {}).get(entry_id) or {}
    prompt = (plan.get("agentPrompts") or {}).get(entry_id, "")
    attached_tools = (plan.get("agentToolSpecs") or {}).get(entry_id, [])
    mcp_servers = plan.get("mcpServers") or {}
    runtime_concurrent = plan.get("runtimeConcurrent") or {}
    runtime_groupchat = plan.get("runtimeGroupchat") or {}

    initial_state: OrchestratorState = {"messages": []}
    user_message = _user_message_from_input(user_input)
    if user_message:
        initial_state["messages"] = [user_message]

    if telemetry and telemetry.enabled():
        await telemetry.publish(
            TelemetryEvent(
                event="telemetry.run_started",
                payload={"runId": context.run_id, "nodeCount": len(ir.get("nodes", []))},
            )
        )

    token = set_runtime_context(RuntimeContext(execution=context, telemetry=telemetry))
    try:
        if node.get("kind") == "concurrent":
            concurrent_meta = runtime_concurrent.get(entry_id)
            if not concurrent_meta:
                raise ValueError(f"Concurrent node '{entry_id}' missing runtime metadata")
            runner = build_concurrent_runner(
                node,
                config=concurrent_meta.get("cfg") or {},
                child_ids=concurrent_meta.get("children") or [],
                node_by_id=plan.get("nodeById") or {},
                agent_prompts=plan.get("agentPrompts") or {},
                agent_tool_specs=plan.get("agentToolSpecs") or {},
                mcp_servers=mcp_servers,
            )
            state_after = await runner(initial_state)
            scratch = state_after.get("scratch") or {}
            agents_store = scratch.get("agents") or {}
            agent_entry = agents_store.get(entry_id) or {}
            final_text = str(agent_entry.get("last_output_text") or "")
            usage_payload = agent_entry.get("usage")
            usage = dict(usage_payload) if isinstance(usage_payload, Mapping) else _usage(_flatten_input(user_input), final_text)
            concurrent_payload = (scratch.get("concurrent") or {}).get(entry_id) or {}
            chosen_meta = concurrent_payload.get("chosen") if isinstance(concurrent_payload, Mapping) else None
            if isinstance(concurrent_payload, Mapping):
                chosen_meta = concurrent_payload.get("chosen")
            else:
                chosen_meta = None
            chosen_node_id = None
            if isinstance(chosen_meta, Mapping):
                chosen_node_id = chosen_meta.get("nodeId")
            children_list = []
            if isinstance(concurrent_payload, Mapping):
                children_list = concurrent_payload.get("children") or []
            created = {
                "type": "response.created",
                "run_id": context.run_id,
                "thread_id": context.thread_id,
                "status": "in_progress",
            }
            yield RunStreamEvent(event="response.created", data=created)
            if final_text:
                yield RunStreamEvent(
                    event="response.output_text.delta",
                    data={
                        "type": "response.output_text.delta",
                        "delta": final_text,
                        "run_id": context.run_id,
                        "thread_id": context.thread_id,
                    },
                )
                done_payload = response_event(
                    "response.output_text.done",
                    run_id=context.run_id,
                    thread_id=context.thread_id,
                )
                yield RunStreamEvent(event=done_payload["type"], data=done_payload)
            completed_payload = {
                "type": "response.completed",
                "run_id": context.run_id,
                "thread_id": context.thread_id,
                "output_text": final_text,
                "usage": usage,
                "response": {
                    "id": context.run_id or entry_id,
                    "metadata": {
                        "concurrent": {
                            "strategy": concurrent_payload.get("strategy") if isinstance(concurrent_payload, Mapping) else None,
                            "childrenCount": len(children_list),
                            "chosenNodeId": chosen_node_id,
                        }
                    },
                },
            }
            yield RunStreamEvent(event="response.completed", data=completed_payload)
        elif node.get("kind") == "groupchat":
            groupchat_meta = runtime_groupchat.get(entry_id)
            if not groupchat_meta:
                raise ValueError(f"Groupchat node '{entry_id}' missing runtime metadata")
            runner = build_groupchat_runner(
                node,
                groupchat_meta,
                node_by_id=plan.get("nodeById") or {},
                agent_prompts=plan.get("agentPrompts") or {},
                agent_tool_specs=plan.get("agentToolSpecs") or {},
                mcp_servers=mcp_servers,
            )
            state_after = await runner(initial_state)
            scratch = state_after.get("scratch") or {}
            agents_store = scratch.get("agents") or {}
            agent_entry = agents_store.get(entry_id) or {}
            final_text = str(agent_entry.get("last_output_text") or "")
            usage_payload = agent_entry.get("usage")
            usage = dict(usage_payload) if isinstance(usage_payload, Mapping) else _usage(_flatten_input(user_input), final_text)
            groupchat_payload = agent_entry.get("groupchat")
            if not isinstance(groupchat_payload, Mapping):
                groupchat_payload = (scratch.get("groupchat") or {}).get(entry_id) or {}
            participants_labels: list[str] = []
            if isinstance(groupchat_payload, Mapping):
                participants_labels = list(groupchat_payload.get("participants") or [])
            created = {
                "type": "response.created",
                "run_id": context.run_id,
                "thread_id": context.thread_id,
                "status": "in_progress",
            }
            yield RunStreamEvent(event="response.created", data=created)
            if final_text:
                yield RunStreamEvent(
                    event="response.output_text.delta",
                    data={
                        "type": "response.output_text.delta",
                        "delta": final_text,
                        "run_id": context.run_id,
                        "thread_id": context.thread_id,
                    },
                )
                done_payload = response_event(
                    "response.output_text.done",
                    run_id=context.run_id,
                    thread_id=context.thread_id,
                )
                yield RunStreamEvent(event=done_payload["type"], data=done_payload)
            metadata_groupchat: dict[str, Any] = {}
            if isinstance(groupchat_payload, Mapping):
                metadata_groupchat = {
                    "turns": groupchat_payload.get("turnCount"),
                    "stopWhen": groupchat_payload.get("stopWhen"),
                    "finalSpeaker": groupchat_payload.get("finalSpeaker"),
                    "participants": participants_labels,
                }
                stop_reason = groupchat_payload.get("stopReason")
                if stop_reason:
                    metadata_groupchat["stopReason"] = stop_reason
            completed_payload = {
                "type": "response.completed",
                "run_id": context.run_id,
                "thread_id": context.thread_id,
                "output_text": final_text,
                "usage": usage,
                "response": {
                    "id": context.run_id or entry_id,
                    "metadata": {"groupchat": metadata_groupchat},
                },
            }
            yield RunStreamEvent(event="response.completed", data=completed_payload)
        else:
            tool_runtime = codeless_mod._prepare_tool_runtime(
                node,
                attached_tools,
                mcp_servers,
            )
            runtime_ctx = get_runtime_context()
            agent_data = node.get("data") or {}
            conversation_messages = list(
                codeless_mod._assemble_messages(initial_state, agent_data, prompt)
            )
            current_state: OrchestratorState = {"messages": copy.deepcopy(conversation_messages)}
            tool_calls_executed = 0

            def _result_count(payload: Any) -> int | None:
                if isinstance(payload, Mapping):
                    results = payload.get("results")
                    if isinstance(results, list):
                        return len(results)
                    return 1 if payload else 0
                if isinstance(payload, list):
                    return len(payload)
                if payload in (None, "", {}):
                    return 0
                return 1

            while True:
                pending_calls = codeless_mod.PendingToolCalls()
                output_delta_seen = False
                output_done_emitted = False

                async for payload in stream_codeless(
                    current_state,
                    node,
                    prompt,
                    attached_tools=attached_tools,
                    mcp_servers=mcp_servers,
                ):
                    raw_type = str(payload.get("type") or payload.get("event") or "message")
                    canonical_type = canonical_event_type(raw_type)
                    data = dict(payload)
                    data["type"] = canonical_type
                    data.setdefault("run_id", context.run_id or "")
                    data.setdefault("thread_id", context.thread_id or "")

                    if canonical_type == "response.output_text.delta":
                        output_delta_seen = True
                    elif canonical_type == "response.output_text.done":
                        output_done_emitted = True

                    if canonical_type in {
                        "response.function_call_arguments.delta",
                        "response.function_call_arguments.done",
                    }:
                        pending_calls.upsert(data)

                    events_to_emit: List[Dict[str, Any]] = [data]

                    if (
                        canonical_type == "response.completed"
                        and output_delta_seen
                        and not output_done_emitted
                    ):
                        done_payload = response_event(
                            "response.output_text.done",
                            run_id=context.run_id or "",
                            thread_id=context.thread_id or "",
                        )
                        events_to_emit.append(done_payload)
                        output_done_emitted = True

                    for alias_type in alias_event_types(canonical_type):
                        alias_payload = dict(data)
                        alias_payload["type"] = alias_type
                        events_to_emit.append(alias_payload)

                    for item in events_to_emit:
                        yield RunStreamEvent(event=item["type"], data=item)

                if not tool_runtime.enabled:
                    break

                plan_limit: Optional[int] = None
                if tool_runtime.max_calls is not None:
                    remaining = tool_runtime.max_calls - tool_calls_executed
                    if remaining <= 0:
                        break
                    plan_limit = remaining

                plan_items = pending_calls.to_plan(tool_runtime, limit=plan_limit)
                if not plan_items:
                    break

                for plan_item in plan_items:
                    base_data = {"function_name": plan_item.sanitized_name}
                    created_event = tool_event(
                        "response.tool_call.created",
                        index=plan_item.index,
                        tool_call_id=plan_item.call_id,
                        tool_name=plan_item.internal_name,
                        run_id=context.run_id or "",
                        thread_id=context.thread_id or "",
                        data=dict(base_data),
                    )
                    running_event = tool_event(
                        "response.tool_call.delta",
                        index=plan_item.index,
                        tool_call_id=plan_item.call_id,
                        tool_name=plan_item.internal_name,
                        run_id=context.run_id or "",
                        thread_id=context.thread_id or "",
                        data={**base_data, "status": "running"},
                    )
                    yield RunStreamEvent(event=created_event["type"], data=created_event)
                    yield RunStreamEvent(event=running_event["type"], data=running_event)

                semaphore = asyncio.Semaphore(max(1, tool_runtime.parallelism))
                queue: asyncio.Queue[tuple[int, Dict[str, Any]]] = asyncio.Queue()
                results_buffer: List[Dict[str, Any] | None] = [None] * len(plan_items)

                async def _worker(position: int, item: codeless_mod.ToolCallPlanItem) -> None:
                    call_payload = {
                        "id": item.call_id,
                        "name": item.internal_name,
                        "arguments": item.arguments,
                    }
                    async with semaphore:
                        result = await codeless_mod._execute_tool_call(
                            call_payload,
                            tool_runtime,
                            runtime_ctx.telemetry if runtime_ctx else None,
                            index=item.index,
                        )
                    await queue.put((position, result))

                tasks = [asyncio.create_task(_worker(pos, item)) for pos, item in enumerate(plan_items)]
                pending_results = len(tasks)
                while pending_results:
                    position, result = await queue.get()
                    plan_item = plan_items[position]
                    results_buffer[position] = result
                    raw_output = result.get("output")
                    result_count = _result_count(raw_output)
                    error_flag = isinstance(raw_output, Mapping) and bool(raw_output.get("error"))

                    created_extra: Dict[str, Any] = {"function_name": plan_item.sanitized_name}
                    if result_count is not None:
                        created_extra["result_count"] = result_count
                    created_event = tool_result_event(
                        "response.tool_result.created",
                        index=plan_item.index,
                        tool_call_id=plan_item.call_id,
                        tool_name=plan_item.internal_name,
                        run_id=context.run_id or "",
                        thread_id=context.thread_id or "",
                        output=None,
                        redacted=tool_runtime.redact,
                        error=error_flag,
                        extra=created_extra,
                    )
                    yield RunStreamEvent(event=created_event["type"], data=created_event)

                    done_event = tool_result_event(
                        "response.tool_result.done",
                        index=plan_item.index,
                        tool_call_id=plan_item.call_id,
                        tool_name=plan_item.internal_name,
                        run_id=context.run_id or "",
                        thread_id=context.thread_id or "",
                        output=None if tool_runtime.redact else raw_output,
                        redacted=tool_runtime.redact,
                        error=error_flag,
                        extra={"function_name": plan_item.sanitized_name},
                    )
                    yield RunStreamEvent(event=done_event["type"], data=done_event)

                    status_text = "error" if error_flag else "completed"
                    delta_data: Dict[str, Any] = {
                        "function_name": plan_item.sanitized_name,
                        "status": status_text,
                    }
                    if error_flag:
                        delta_data["error"] = True
                    completion_event = tool_event(
                        "response.tool_call.delta",
                        index=plan_item.index,
                        tool_call_id=plan_item.call_id,
                        tool_name=plan_item.internal_name,
                        run_id=context.run_id or "",
                        thread_id=context.thread_id or "",
                        data=dict(delta_data),
                    )
                    done_call_event = tool_event(
                        "response.tool_call.done",
                        index=plan_item.index,
                        tool_call_id=plan_item.call_id,
                        tool_name=plan_item.internal_name,
                        run_id=context.run_id or "",
                        thread_id=context.thread_id or "",
                        data=dict(delta_data),
                    )
                    yield RunStreamEvent(event=completion_event["type"], data=completion_event)
                    yield RunStreamEvent(event=done_call_event["type"], data=done_call_event)
                    pending_results -= 1

                await asyncio.gather(*tasks)

                assistant_message = {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": item.call_id,
                            "type": "function",
                            "function": {"name": item.sanitized_name, "arguments": item.arguments_json},
                        }
                        for item in plan_items
                    ],
                }
                conversation_messages.append(assistant_message)
                for idx, item in enumerate(plan_items):
                    result = results_buffer[idx]
                    if result is None:
                        continue
                    patched_result = dict(result)
                    patched_result["name"] = item.sanitized_name
                    codeless_mod._append_tool_messages(conversation_messages, [patched_result])

                current_state = {"messages": copy.deepcopy(conversation_messages)}
                tool_calls_executed += len(plan_items)
    finally:
        reset_runtime_context(token)

    if telemetry and telemetry.enabled():
        await telemetry.publish(
            TelemetryEvent(
                event="telemetry.run_completed",
                payload={"runId": context.run_id, "status": "completed"},
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

    ok, plan, errors = build_runtime_plan(ir, runtime_vars={})
    if not ok:
        message = "; ".join(errors) if errors else "Failed to build runtime plan"
        raise ValueError(message)

    from app.compiler.builder import GraphBuilder
    from app.compiler.registry import put as put_compiled_graph

    builder = GraphBuilder(plan)
    app = builder.build()

    graph_id = f"runtime-{context.run_id}"
    put_compiled_graph(graph_id, app)

    state_in: Dict[str, Any] = {}
    user_message = _user_message_from_input(user_input)
    if user_message:
        state_in["messages"] = [user_message]

    final_state = await execute_once(
        graph_id,
        context.run_id or "",
        state_in,
        metadata={"plan_entry": plan.get("entryId")},
        context=context,
        telemetry=telemetry,
    )

    entry_id = str(plan.get("entryId") or "")
    scratch = _agent_scratch(final_state, entry_id)
    router_info = _router_state(final_state, entry_id)
    run_status = str(final_state.get("status") or "completed")
    pause_metadata_raw = final_state.get("pause_metadata")
    pause_metadata = pause_metadata_raw if isinstance(pause_metadata_raw, Mapping) else {}

    output_text = str(scratch.get("last_output_text") or _last_assistant_text(final_state))
    usage_payload = scratch.get("usage")
    response_payload_raw = scratch.get("last_response")
    if isinstance(response_payload_raw, Mapping):
        response_payload: Dict[str, Any] = copy.deepcopy(response_payload_raw)
    else:
        response_payload = {}

    usage = dict(usage_payload) if isinstance(usage_payload, Mapping) else {}
    if run_status != "paused" and not usage:
        usage = _usage(_flatten_input(user_input), output_text)

    if "metadata" not in response_payload or not isinstance(response_payload.get("metadata"), Mapping):
        response_payload["metadata"] = {}
    metadata_block = dict(response_payload["metadata"])
    response_payload["metadata"] = metadata_block

    router_decision = router_info.get("decision") if isinstance(router_info, Mapping) else None
    if isinstance(router_decision, Mapping) and router_decision:
        metadata_block["router"] = copy.deepcopy(router_decision)
        if router_decision.get("source") == "paused" and router_decision.get("hitl"):
            metadata_block["hitl"] = copy.deepcopy(router_decision["hitl"])
            final_state["pause_metadata"] = {"hitl": copy.deepcopy(router_decision["hitl"])}
            final_state["pause_reason"] = router_decision.get("reason") or router_decision.get("fallbackMode") or "router.ask_user"
            run_status = "paused"
    groupchat_meta = scratch.get("groupchat")
    if isinstance(groupchat_meta, Mapping):
        participants_labels = list(groupchat_meta.get("participants") or [])
        groupchat_metadata = {
            "turns": groupchat_meta.get("turnCount"),
            "stopWhen": groupchat_meta.get("stopWhen"),
            "finalSpeaker": groupchat_meta.get("finalSpeaker"),
            "participants": participants_labels,
        }
        stop_reason = groupchat_meta.get("stopReason")
        if stop_reason:
            groupchat_metadata["stopReason"] = stop_reason
        metadata_block["groupchat"] = groupchat_metadata
    if pause_metadata and not metadata_block.get("hitl"):
        metadata_block["hitl"] = copy.deepcopy(pause_metadata.get("hitl", pause_metadata))
        run_status = "paused"
    response_payload["status"] = run_status

    pause_meta_store = metadata_block.get("hitl") if isinstance(metadata_block.get("hitl"), Mapping) else None
    if run_status == "paused":
        store = get_run_state_store()
        record = store.get_state(context.run_id or "")
        if record is not None:
            updated_metadata = copy.deepcopy(record.metadata)
            updated_metadata["status"] = "paused"
            if pause_meta_store:
                updated_metadata["pause_metadata"] = {"hitl": copy.deepcopy(pause_meta_store)}
                updated_metadata["pause_reason"] = pause_meta_store.get("reason") or "router.ask_user"
            pause_state = copy.deepcopy(record.state)
            pause_state["status"] = "paused"
            if pause_meta_store:
                pause_state["pause_metadata"] = {"hitl": copy.deepcopy(pause_meta_store)}
                pause_state["pause_reason"] = pause_meta_store.get("reason") or "router.ask_user"
            store.put_state(
                RunStateRecord(
                    run_id=record.run_id,
                    state=pause_state,
                    metadata=updated_metadata,
                )
            )
        pause_checkpoint_metadata = {"hitl": copy.deepcopy(pause_meta_store)} if pause_meta_store else {}
        get_checkpointer().mark_status(
            context.run_id or "",
            RunStatus.PAUSED,
            metadata={"pause_metadata": pause_checkpoint_metadata},
        )

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
        response=response_payload,
    )
