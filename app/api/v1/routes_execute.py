"""Execution endpoints including streaming and telemetry support."""

from __future__ import annotations

import copy
from collections.abc import AsyncIterator
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from app.api.deps import ExecutionContext, ensure_tenant_matches, parse_execution_headers
from app.api.models import TelemetryLevel
from app.api.schemas import RunRequest, RunStatus, ResumeRequest
from app.config.settings import settings
from app.ir.loader import build_runtime_plan
from app.runtime.engine import (
    ExecutionResult,
    RunStreamEvent,
    get_run_status,
    resume_run,
    run_once,
    run_stream,
)
from app.runtime.state_store import RunStateRecord, get_run_state_store
from app.sse.streams import SSEMessage, message_stream
from app.telemetry.models import TelemetryEvent
from app.telemetry.streamer import (
    TelemetryStreamConfig,
    TelemetryStreamer,
    get_streamer,
    pop_streamer,
    register_streamer,
)

router = APIRouter(prefix="/v1", tags=["v1"])

_RUN_STATE_STORE = get_run_state_store()


async def _create_context(
    request: Request,
    req: RunRequest,
) -> tuple[ExecutionContext, Optional[TelemetryStreamer]]:
    headers = parse_execution_headers(request)
    ensure_tenant_matches(req.orchestration, headers.tenant_id)

    context = ExecutionContext(headers=headers)
    if req.thread_id:
        context.thread_id = req.thread_id
    context.ensure_run_ids()

    telemetry: Optional[TelemetryStreamer] = None
    if headers.telemetry is not TelemetryLevel.NONE:
        config = TelemetryStreamConfig(
            run_id=context.run_id or "",
            level=headers.telemetry,
            redact=settings.log_redaction_enabled,
        )
        telemetry = TelemetryStreamer(config)
        await register_streamer(telemetry)

    return context, telemetry


async def _finalize_telemetry(
    context: ExecutionContext,
    telemetry: Optional[TelemetryStreamer],
    *,
    remove: bool = True,
) -> None:
    if telemetry is None:
        return
    await telemetry.publish(
        TelemetryEvent(event="telemetry.stream_closed", payload={"runId": context.run_id}),
    )
    await telemetry.close()
    if remove:
        await pop_streamer(context.run_id or "")


async def _persist_run_state(result: ExecutionResult) -> None:
    if not result.run_id:
        return

    prior = _RUN_STATE_STORE.get_state(result.run_id)
    metadata: Dict[str, Any] = {}
    if prior:
        metadata.update(copy.deepcopy(prior.metadata))
    metadata["thread_id"] = result.thread_id
    status_value = str(
        result.response.get("status")
        or metadata.get("status")
        or "completed"
    )
    metadata["status"] = status_value
    if result.usage:
        metadata["usage"] = dict(result.usage)
    metadata["response"] = copy.deepcopy(result.response)
    metadata["invocations"] = int(metadata.get("invocations", 0)) + 1

    state_payload: Dict[str, Any] = {}
    if prior:
        state_payload = copy.deepcopy(prior.state)

    state_payload["run_id"] = result.run_id
    state_payload["status"] = status_value
    state_payload["result"] = {
        "output_text": result.output_text,
        "usage": dict(result.usage),
    }

    payload = RunStateRecord(
        run_id=result.run_id,
        state=state_payload,
        metadata=metadata,
    )
    _RUN_STATE_STORE.put_state(payload)


@router.post(
    "/execute",
    response_model=RunStatus,
    response_model_exclude_none=True,
    status_code=status.HTTP_201_CREATED,
    tags=["execute"],
)
async def execute(request: Request, req: RunRequest) -> RunStatus:
    context, telemetry = await _create_context(request, req)
    try:
        result = await run_once(req.orchestration, req.input, context, telemetry)
    except ValueError as exc:
        await _finalize_telemetry(context, telemetry)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    await _persist_run_state(result)
    await _finalize_telemetry(context, telemetry)

    payload = get_run_status(result.run_id)
    return RunStatus.model_validate(payload)


async def _stream_response_events(
    req: RunRequest,
    context: ExecutionContext,
    telemetry: Optional[TelemetryStreamer],
) -> AsyncIterator[SSEMessage]:
    last_completed: Optional[RunStreamEvent] = None
    async for event in run_stream(req.orchestration, req.input, context, telemetry):
        if event.event == "response.completed":
            last_completed = event
        yield SSEMessage(event=event.event, data=event.data)

    if last_completed is not None:
        payload = last_completed.data
        result = ExecutionResult(
            run_id=context.run_id or "",
            thread_id=context.thread_id or "",
            output_text=str(payload.get("output_text", "")),
            usage=dict(payload.get("usage", {})),
            response=payload,
        )
        await _persist_run_state(result)

    await _finalize_telemetry(context, telemetry, remove=False)


@router.post("/execute/stream", tags=["execute"])
async def execute_stream(request: Request, req: RunRequest) -> StreamingResponse:
    context, telemetry = await _create_context(request, req)

    ok, _, errors = build_runtime_plan(req.orchestration, runtime_vars={})
    if not ok:
        await _finalize_telemetry(context, telemetry)
        message = "; ".join(errors) if errors else "Invalid orchestration package"
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=message)

    headers: Dict[str, str] = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
    }
    if telemetry and telemetry.enabled():
        headers["X-Telemetry-Stream-Url"] = f"/v1/telemetry/stream?runId={context.run_id}"

    iterator = _stream_response_events(req, context, telemetry)
    return StreamingResponse(
        message_stream(iterator, heartbeat_interval=10.0),
        media_type="text/event-stream",
        headers=headers,
    )


@router.post(
    "/execute/{runId}/resume",
    response_model=RunStatus,
    response_model_exclude_none=True,
    status_code=status.HTTP_200_OK,
    tags=["execute"],
)
async def resume_execution(request: Request, runId: str, body: ResumeRequest) -> RunStatus:
    headers = parse_execution_headers(request)
    payload = body.model_dump(exclude_none=True)
    try:
        result = await resume_run(
            run_id=runId,
            resume_kind=body.kind.value,
            payload=payload,
            tenant_id=headers.tenant_id or settings.default_tenant_id,
            request_id=headers.request_id,
            correlation_id=headers.correlation_id,
        )
    except ValueError as exc:
        error_code = str(exc)
        status_map = {
            "RUN_NOT_FOUND": status.HTTP_404_NOT_FOUND,
            "RUN_NOT_PAUSED": status.HTTP_409_CONFLICT,
            "RESUME_CHOICE_REQUIRED": status.HTTP_400_BAD_REQUEST,
            "RESUME_CHOICE_TARGET_REQUIRED": status.HTTP_400_BAD_REQUEST,
            "RESUME_CHOICE_TARGET_NOT_FOUND": status.HTTP_400_BAD_REQUEST,
            "RESUME_ROUTER_NOT_FOUND": status.HTTP_409_CONFLICT,
            "RESUME_MESSAGE_REQUIRED": status.HTTP_400_BAD_REQUEST,
            "RESUME_MESSAGE_EMPTY": status.HTTP_400_BAD_REQUEST,
            "RESUME_KIND_UNSUPPORTED": status.HTTP_400_BAD_REQUEST,
            "RESUME_GRAPH_NOT_FOUND": status.HTTP_409_CONFLICT,
        }
        message_map = {
            "RUN_NOT_FOUND": "Unknown run",
            "RUN_NOT_PAUSED": "Run is not paused",
            "RESUME_CHOICE_REQUIRED": "Router choice payload is required",
            "RESUME_CHOICE_TARGET_REQUIRED": "Router choice target is required",
            "RESUME_CHOICE_TARGET_NOT_FOUND": "Router choice target does not match any branch",
            "RESUME_ROUTER_NOT_FOUND": "Paused router context missing for run",
            "RESUME_MESSAGE_REQUIRED": "Resume message payload is required",
            "RESUME_MESSAGE_EMPTY": "Resume message must not be empty",
            "RESUME_KIND_UNSUPPORTED": "Resume kind is not supported",
            "RESUME_GRAPH_NOT_FOUND": "Compiled graph for run is unavailable",
        }
        http_status = status_map.get(error_code, status.HTTP_400_BAD_REQUEST)
        detail_message = message_map.get(error_code, "Resume request invalid")
        raise HTTPException(
            status_code=http_status,
            detail={"error": {"code": error_code, "message": detail_message}},
        ) from exc
    return RunStatus.model_validate(result)


@router.get("/telemetry/stream")
async def telemetry_stream(request: Request, runId: str) -> StreamingResponse:
    parse_execution_headers(request)  # Validation only
    streamer = await get_streamer(runId)
    if streamer is None or not streamer.enabled():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Telemetry stream not available",
        )

    async def iterator() -> AsyncIterator[SSEMessage]:
        async for event in streamer.stream():
            yield SSEMessage(event=event.event, data=event.payload)
        await pop_streamer(runId)

    return StreamingResponse(
        message_stream(iterator(), heartbeat_interval=10.0),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )
