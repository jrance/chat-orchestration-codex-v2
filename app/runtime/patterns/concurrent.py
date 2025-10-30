"""Executor and merge strategies for concurrent orchestration nodes."""

from __future__ import annotations

import asyncio
import copy
import math
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, Mapping, MutableMapping, Optional, Sequence

from app.compiler.types import OrchestratorState
from app.providers.openai_like.synth import compose_synthesis, score_candidates
from app.runtime.agents import codeless as codeless_agent
from app.runtime.context import RuntimeContext, get_runtime_context
from app.runtime.types import ChildResult, ConcurrentResult
from app.telemetry.events import (
    CONCURRENT_CHILD_CANCELLED,
    CONCURRENT_CHILD_COMPLETED,
    CONCURRENT_CHILD_ERROR,
    CONCURRENT_CHILD_STARTED,
    CONCURRENT_CHILD_TIMEOUT,
    CONCURRENT_MERGE_DECISION,
    CONCURRENT_RUN_COMPLETED,
    CONCURRENT_RUN_STARTED,
    make_event,
)
from app.telemetry.streamer import TelemetryStreamer
from app.tools.types import ToolSpec

ChildRunner = Callable[[], Awaitable[ChildResult | Mapping[str, object]]]


@dataclass(slots=True)
class ConcurrentChildSpec:
    """Configuration describing a single concurrent child invocation."""

    node_id: str
    label: str
    runner: ChildRunner
    timeout_seconds: float | None = None
    metadata: Mapping[str, object] | None = None


@dataclass(slots=True)
class ConcurrentConfig:
    """Execution settings for a concurrent orchestration node."""

    node_id: str
    label: str
    strategy: str
    timeout_seconds: float
    max_parallelism: int
    cancel_remaining_on_decision: bool
    synth_prompt: str | None = None
    first_best_threshold: float | None = None


class _ChildTimeoutError(Exception):
    """Internal sentinel for child timeouts."""


@dataclass(slots=True)
class _ChildInfo:
    """Snapshot of per-child settings used to build runner closures."""

    node_id: str
    label: str
    node: Mapping[str, Any]
    prompt: str
    tools: Sequence[ToolSpec]
    timeout_seconds: float | None = None


def _as_float(value: object | None) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number):
        return None
    return number


def _normalize_child_result(spec: ConcurrentChildSpec, payload: Mapping[str, object] | ChildResult | None) -> ChildResult:
    result: Dict[str, object] = {}
    if isinstance(payload, Mapping):
        result.update(payload)
    elif payload is not None:
        result["output_text"] = payload  # type: ignore[assignment]

    result.setdefault("nodeId", spec.node_id)
    result.setdefault("label", spec.label)
    status = result.get("status")
    if status not in {"completed", "timeout", "error", "cancelled"}:
        result["status"] = "completed"

    if spec.metadata and not result.get("meta"):
        result["meta"] = dict(spec.metadata)

    confidence = _as_float(result.get("confidence"))
    result["confidence"] = confidence
    usage = result.get("usage")
    if not isinstance(usage, Mapping):
        result["usage"] = None

    output = result.get("output_text")
    if output is not None and not isinstance(output, str):
        result["output_text"] = str(output)

    error = result.get("error")
    if error is not None and not isinstance(error, str):
        result["error"] = str(error)

    meta = result.get("meta")
    if meta is not None and not isinstance(meta, Mapping):
        result["meta"] = {}

    return ChildResult(result)  # type: ignore[arg-type]


def _timeout_child_result(spec: ConcurrentChildSpec) -> ChildResult:
    return ChildResult(
        {
            "nodeId": spec.node_id,
            "label": spec.label,
            "status": "timeout",
            "output_text": None,
            "confidence": None,
            "usage": None,
            "error": "timeout",
        }
    )


def _error_child_result(spec: ConcurrentChildSpec, exc: Exception) -> ChildResult:
    return ChildResult(
        {
            "nodeId": spec.node_id,
            "label": spec.label,
            "status": "error",
            "output_text": None,
            "confidence": None,
            "usage": None,
            "error": str(exc),
        }
    )


def _cancelled_child_result(spec: ConcurrentChildSpec) -> ChildResult:
    return ChildResult(
        {
            "nodeId": spec.node_id,
            "label": spec.label,
            "status": "cancelled",
            "output_text": None,
            "confidence": None,
            "usage": None,
        }
    )


async def _publish(telemetry: TelemetryStreamer | None, event: str, payload: Mapping[str, object]) -> None:
    if telemetry is None:
        return
    await telemetry.publish(make_event(event, payload))


async def _run_child(
    spec: ConcurrentChildSpec,
    cfg: ConcurrentConfig,
    semaphore: asyncio.Semaphore | None,
    telemetry: TelemetryStreamer | None,
) -> ChildResult:
    base_payload = {"parentId": cfg.node_id, "nodeId": spec.node_id, "label": spec.label}
    await _publish(telemetry, CONCURRENT_CHILD_STARTED, base_payload)

    async def _invoke() -> ChildResult:
        timeout = spec.timeout_seconds if spec.timeout_seconds is not None else cfg.timeout_seconds
        try:
            if timeout and timeout > 0:
                raw = await asyncio.wait_for(spec.runner(), timeout=timeout)
            else:
                raw = await spec.runner()
        except asyncio.TimeoutError as exc:
            raise _ChildTimeoutError() from exc
        return _normalize_child_result(spec, raw)

    try:
        if semaphore is None:
            result = await _invoke()
        else:
            async with semaphore:
                result = await _invoke()
    except _ChildTimeoutError:
        await _publish(telemetry, CONCURRENT_CHILD_TIMEOUT, base_payload)
        return _timeout_child_result(spec)
    except asyncio.CancelledError:
        await _publish(telemetry, CONCURRENT_CHILD_CANCELLED, base_payload)
        raise
    except Exception as exc:  # pylint: disable=broad-except
        await _publish(telemetry, CONCURRENT_CHILD_ERROR, {**base_payload, "error": str(exc)})
        return _error_child_result(spec, exc)

    await _publish(
        telemetry,
        CONCURRENT_CHILD_COMPLETED,
        {**base_payload, "status": result.get("status", "completed"), "confidence": result.get("confidence")},
    )
    return result


async def _collect_task(
    task: asyncio.Task[ChildResult],
    spec: ConcurrentChildSpec,
) -> ChildResult:
    try:
        return await task
    except asyncio.CancelledError:
        return _cancelled_child_result(spec)


def _highest_confidence(results: Sequence[ChildResult]) -> ChildResult | None:
    candidates = [res for res in results if res.get("confidence") is not None]
    if not candidates:
        return None
    best = max(candidates, key=lambda item: (item.get("confidence") or 0.0, -results.index(item)))
    return best


def build_concurrent_runner(
    node: Mapping[str, Any],
    *,
    config: Mapping[str, Any],
    child_ids: Sequence[str],
    node_by_id: Mapping[str, Mapping[str, Any]],
    agent_prompts: Mapping[str, str],
    agent_tool_specs: Mapping[str, Sequence[ToolSpec]],
    mcp_servers: Mapping[str, Mapping[str, Any]],
) -> Callable[[OrchestratorState], Awaitable[OrchestratorState]]:
    """Construct a LangGraph node runner that executes children concurrently."""

    node_id = node["id"]
    label = node.get("label") or node_id

    strategy = str(config.get("strategy") or "FirstBest")
    synth_prompt = config.get("synth_prompt")
    first_best_threshold = config.get("first_best_threshold")
    try:
        timeout_seconds = float(config.get("timeout_seconds") or 120.0)
    except (TypeError, ValueError):
        timeout_seconds = 120.0
    if timeout_seconds <= 0:
        timeout_seconds = 120.0
    try:
        max_parallelism = int(config.get("max_parallelism") or 4)
    except (TypeError, ValueError):
        max_parallelism = 4
    if max_parallelism <= 0:
        max_parallelism = 4

    cfg_obj = ConcurrentConfig(
        node_id=node_id,
        label=label,
        strategy=strategy,
        timeout_seconds=timeout_seconds,
        max_parallelism=max_parallelism,
        cancel_remaining_on_decision=bool(config.get("cancel_remaining_on_decision", True)),
        synth_prompt=synth_prompt if isinstance(synth_prompt, str) and synth_prompt.strip() else None,
        first_best_threshold=float(first_best_threshold) if isinstance(first_best_threshold, (int, float)) else None,
    )

    child_infos: list[_ChildInfo] = []
    for child_id in child_ids:
        child_node = node_by_id.get(child_id)
        if not isinstance(child_node, Mapping):
            continue
        if child_node.get("kind") != "agent.codeless":
            raise ValueError(f"Unsupported child kind '{child_node.get('kind')}' for concurrent node '{node_id}'")
        prompt = agent_prompts.get(child_id, "")
        tools = agent_tool_specs.get(child_id, [])
        timeout_override = child_node.get("data", {}).get("timeoutSeconds")
        try:
            timeout_override_value = float(timeout_override) if timeout_override is not None else None
        except (TypeError, ValueError):
            timeout_override_value = None
        if timeout_override_value is not None and timeout_override_value <= 0:
            timeout_override_value = None
        child_infos.append(
            _ChildInfo(
                node_id=child_id,
                label=child_node.get("label") or child_id,
                node=child_node,
                prompt=prompt,
                tools=tools,
                timeout_seconds=timeout_override_value,
            )
        )

    if not child_infos:
        raise ValueError(f"Concurrent node '{node_id}' must have at least one valid child")

    async def _run(state: OrchestratorState) -> OrchestratorState:
        base_state = copy.deepcopy(state)
        runtime_ctx = get_runtime_context()
        telemetry = runtime_ctx.telemetry if runtime_ctx else None

        child_specs: list[ConcurrentChildSpec] = []

        for info in child_infos:

            async def _runner(info: _ChildInfo = info) -> ChildResult:
                child_state = copy.deepcopy(base_state)
                llm_result = await codeless_agent.invoke_llm(
                    child_state,
                    info.node,
                    info.prompt,
                    attached_tools=info.tools,
                    mcp_servers=mcp_servers,
                )
                response = llm_result.response if isinstance(llm_result.response, Mapping) else {}
                confidence_value = response.get("confidence") if isinstance(response, Mapping) else None
                if isinstance(confidence_value, (int, float)):
                    confidence = float(confidence_value)
                else:
                    confidence = None
                usage_payload = llm_result.usage if isinstance(llm_result.usage, Mapping) else None
                meta_payload: Dict[str, Any] = {"response": response}

                return ChildResult(
                    {
                        "nodeId": info.node_id,
                        "label": info.label,
                        "status": "completed",
                        "output_text": llm_result.output_text,
                        "confidence": confidence,
                        "usage": dict(usage_payload) if usage_payload else None,
                        "error": None,
                        "meta": meta_payload,
                    }
                )

            child_specs.append(
                ConcurrentChildSpec(
                    node_id=info.node_id,
                    label=info.label,
                    runner=_runner,
                    timeout_seconds=info.timeout_seconds,
                )
            )

        concurrent_result = await run_concurrent(
            child_specs,
            cfg_obj,
            runtime_ctx=runtime_ctx,
            telemetry=telemetry,
        )

        scratch = state.setdefault("scratch", {})
        agents_store = scratch.setdefault("agents", {})
        concurrent_store = scratch.setdefault("concurrent", {})

        concurrent_payload = copy.deepcopy(concurrent_result)
        concurrent_store[node_id] = concurrent_payload

        final_text = ""
        usage_summary: Dict[str, Any] | None = None
        if cfg_obj.strategy == "Synthesize":
            merged = concurrent_result.get("merged_text")
            if isinstance(merged, str):
                final_text = merged
        else:
            chosen = concurrent_result.get("chosen")
            if isinstance(chosen, Mapping):
                final_text = str(chosen.get("output_text") or "")
                usage_value = chosen.get("usage")
                if isinstance(usage_value, Mapping):
                    usage_summary = dict(usage_value)

        if final_text:
            messages = list(state.get("messages") or [])
            messages.append({"role": "assistant", "content": final_text})
            state["messages"] = messages

        agent_entry: Dict[str, Any] = {
            "last_output_text": final_text,
            "last_response": copy.deepcopy(concurrent_result),
            "strategy": cfg_obj.strategy,
            "children": copy.deepcopy(concurrent_result.get("children") or []),
        }
        if usage_summary:
            agent_entry["usage"] = usage_summary

        agents_store[node_id] = agent_entry
        return state

    return _run


async def run_concurrent(
    children: Sequence[ConcurrentChildSpec],
    cfg: ConcurrentConfig,
    *,
    runtime_ctx: RuntimeContext | None = None,
    telemetry: TelemetryStreamer | None = None,
    synthesizer: Callable[..., Awaitable[tuple[str, str]]] = compose_synthesis,
    scorer: Callable[..., Awaitable[Mapping[str, float]]] = score_candidates,
) -> ConcurrentResult:
    """Execute concurrent child runners and merge results according to the configured strategy."""

    if not children:
        raise ValueError("Concurrent node requires at least one child")

    runtime_ctx = runtime_ctx or get_runtime_context()
    telemetry = telemetry or (runtime_ctx.telemetry if runtime_ctx else None)

    await _publish(
        telemetry,
        CONCURRENT_RUN_STARTED,
        {"nodeId": cfg.node_id, "label": cfg.label, "strategy": cfg.strategy, "childCount": len(children)},
    )

    limit = cfg.max_parallelism if cfg.max_parallelism and cfg.max_parallelism > 0 else len(children)
    semaphore = asyncio.Semaphore(limit) if limit < len(children) else None

    tasks: MutableMapping[asyncio.Task[ChildResult], ConcurrentChildSpec] = {}
    for spec in children:
        task = asyncio.create_task(_run_child(spec, cfg, semaphore, telemetry))
        tasks[task] = spec

    results: list[ChildResult] = []
    chosen: Optional[ChildResult] = None

    if cfg.strategy == "FirstBest":
        threshold = cfg.first_best_threshold
        pending: MutableMapping[asyncio.Task[ChildResult], ConcurrentChildSpec] = dict(tasks)
        while pending:
            done, _ = await asyncio.wait(list(pending.keys()), return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                spec = pending.pop(task)
                result = await _collect_task(task, spec)
                results.append(result)
                if result.get("status") == "completed":
                    confidence = result.get("confidence")
                    if threshold is None or (confidence is not None and confidence >= threshold):
                        chosen = result
                        if cfg.cancel_remaining_on_decision and pending:
                            for cancel_task in pending.keys():
                                cancel_task.cancel()
                            for cancel_task, cancel_spec in list(pending.items()):
                                pending.pop(cancel_task, None)
                                cancel_result = await _collect_task(cancel_task, cancel_spec)
                                results.append(cancel_result)
                        pending.clear()
                        break
            if chosen:
                break

        for task, spec in list(pending.items()):
            pending.pop(task, None)
            results.append(await _collect_task(task, spec))

        if chosen is None and results:
            chosen = next((res for res in results if res.get("status") == "completed"), results[0])

        merged_text = chosen.get("output_text") if chosen else None
        rationale = None
    else:
        for task, spec in list(tasks.items()):
            results.append(await _collect_task(task, spec))

        if cfg.strategy == "HighestScore":
            completed = [res for res in results if res.get("status") == "completed"]
            if completed:
                missing_conf = [res for res in completed if res.get("confidence") is None]
                if missing_conf:
                    score_map = await scorer(completed, runtime_ctx=runtime_ctx)
                    for res in completed:
                        if res.get("confidence") is None:
                            res["confidence"] = score_map.get(res["nodeId"], None)
                chosen = _highest_confidence(completed)
            merged_text = chosen.get("output_text") if chosen else None
            rationale = None
        elif cfg.strategy == "Synthesize":
            merged_text, rationale = await synthesizer(results, cfg.synth_prompt, runtime_ctx=runtime_ctx)
            chosen = None
        else:
            raise ValueError(f"Unsupported strategy '{cfg.strategy}'")

    await _publish(
        telemetry,
        CONCURRENT_MERGE_DECISION,
        {
            "nodeId": cfg.node_id,
            "strategy": cfg.strategy,
            "children": [
                {"nodeId": res.get("nodeId"), "status": res.get("status"), "confidence": res.get("confidence")}
                for res in results
            ],
            "chosenNodeId": chosen.get("nodeId") if chosen else None,
        },
    )

    await _publish(
        telemetry,
        CONCURRENT_RUN_COMPLETED,
        {"nodeId": cfg.node_id, "strategy": cfg.strategy, "status": "completed"},
    )

    return ConcurrentResult(
        {
            "strategy": cfg.strategy,
            "chosen": chosen,
            "children": results,
            "merged_text": merged_text,
            "rationale": rationale,
        }
    )


__all__ = ["ConcurrentChildSpec", "ConcurrentConfig", "build_concurrent_runner", "run_concurrent"]
