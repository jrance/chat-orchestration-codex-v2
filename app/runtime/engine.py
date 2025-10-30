"""Runtime engine adapters that produce OpenAI Responses-compatible events."""

from __future__ import annotations

import copy
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, AsyncIterator, Dict, Iterable, List, Optional

from app.api.deps import ExecutionContext
from app.compiler.registry import get as get_compiled_graph
from app.compiler.types import OrchestratorState
from app.ir.loader import build_runtime_plan
from app.runtime.agents.codeless import stream_codeless
from app.runtime.context import RuntimeContext, reset_runtime_context, set_runtime_context
from app.runtime.patterns.concurrent import build_concurrent_runner
from app.runtime.patterns.groupchat import build_groupchat_runner
from app.runtime.state import Checkpoint, RunStatus, get_checkpointer
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
    pause_metadata = output_state.get("pause_metadata") if run_status_enum is RunStatus.PAUSED else None
    pause_reason = output_state.get("pause_reason") if run_status_enum is RunStatus.PAUSED else None
    if pause_metadata is not None and not isinstance(pause_metadata, Mapping):
        pause_metadata = None

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


async def resume_run(
    graph_id: str,
    run_id: str,
    state_in: Mapping[str, Any] | None = None,
    *,
    metadata: Mapping[str, Any] | None = None,
    context: ExecutionContext | None = None,
    telemetry: TelemetryStreamer | None = None,
) -> OrchestratorState:
    """Resume a previously-started run by replaying the compiled graph."""

    store = get_run_state_store()
    record = store.get_state(run_id)
    if record is None and get_checkpointer().load_checkpoint(run_id) is None:
        raise ValueError(f"Run '{run_id}' not found")
    return await execute_once(
        graph_id,
        run_id,
        state_in,
        metadata=metadata,
        context=context,
        telemetry=telemetry,
    )


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
            async for payload in stream_codeless(
                initial_state,
                node,
                prompt,
                attached_tools=attached_tools,
                mcp_servers=mcp_servers,
            ):
                data = dict(payload)
                data.setdefault("run_id", context.run_id)
                data.setdefault("thread_id", context.thread_id)
                event_type = str(data.get("type", payload.get("type", "message")))
                yield RunStreamEvent(event=event_type, data=data)
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
    pause_metadata = final_state.get("pause_metadata") if run_status == "paused" else {}
    if not isinstance(pause_metadata, Mapping):
        pause_metadata = {}

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
    if pause_metadata:
        metadata_block["hitl"] = copy.deepcopy(pause_metadata.get("hitl", pause_metadata))
    response_payload["status"] = run_status

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
