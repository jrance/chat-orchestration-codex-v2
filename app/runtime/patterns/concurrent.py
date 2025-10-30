"""Executor and merge strategies for concurrent orchestration nodes."""

from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass
from typing import Awaitable, Callable, Dict, Mapping, MutableMapping, Optional, Sequence

from app.providers.openai_like.synth import compose_synthesis, score_candidates
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


__all__ = ["ConcurrentChildSpec", "ConcurrentConfig", "run_concurrent"]
