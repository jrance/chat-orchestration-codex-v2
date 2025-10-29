"""Execution endpoints including streaming and telemetry support."""

from __future__ import annotations

import copy
from collections.abc import AsyncIterator
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from app.api.deps import ExecutionContext, ensure_tenant_matches, parse_execution_headers
from app.config.settings import settings
from app.runtime.engine import ExecutionResult, RunStreamEvent, run_once, run_stream
from app.runtime.state_store import RunStateRecord, get_run_state_store
from app.sse.streams import SSEMessage, message_stream
from app.telemetry.models import TelemetryEvent, TelemetryLevel
from app.telemetry.streamer import (
    TelemetryStreamConfig,
    TelemetryStreamer,
    get_streamer,
    pop_streamer,
    register_streamer,
)

from .models import ExecuteRequest, ExecuteResponse, ResumeRequest

router = APIRouter(prefix="/v1", tags=["v1"])

_RUN_STATE_STORE = get_run_state_store()


async def _create_context(
    request: Request,
    req: ExecuteRequest,
) -> tuple[ExecutionContext, Optional[TelemetryStreamer]]:
    headers = parse_execution_headers(request)
    ensure_tenant_matches(req.ir, headers.tenant_id)

    context = ExecutionContext(headers=headers)
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
    metadata["response"] = copy.deepcopy(result.response)
    metadata["invocations"] = int(metadata.get("invocations", 0)) + 1

    payload = RunStateRecord(
        run_id=result.run_id,
        state={
            "run_id": result.run_id,
            "result": {
                "output_text": result.output_text,
                "usage": dict(result.usage),
            },
        },
        metadata=metadata,
    )
    _RUN_STATE_STORE.put_state(payload)


@router.post("/execute", response_model=ExecuteResponse)
async def execute(request: Request, req: ExecuteRequest) -> ExecuteResponse:
    context, telemetry = await _create_context(request, req)
    result = await run_once(req.ir, req.input, context, telemetry)
    await _persist_run_state(result)
    await _finalize_telemetry(context, telemetry)

    return ExecuteResponse(
        ok=True,
        runId=result.run_id,
        threadId=result.thread_id,
        output_text=result.output_text,
        usage=result.usage,
        message="completed",
    )


async def _stream_response_events(
    req: ExecuteRequest,
    context: ExecutionContext,
    telemetry: Optional[TelemetryStreamer],
) -> AsyncIterator[SSEMessage]:
    last_completed: Optional[RunStreamEvent] = None
    async for event in run_stream(req.ir, req.input, context, telemetry):
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


@router.post("/execute/stream")
async def execute_stream(request: Request, req: ExecuteRequest) -> StreamingResponse:
    context, telemetry = await _create_context(request, req)

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


@router.post("/execute/{runId}/resume", response_model=ExecuteResponse)
async def resume(request: Request, runId: str, body: ResumeRequest) -> ExecuteResponse:
    parse_execution_headers(request)  # Ensures required headers are present
    record = _RUN_STATE_STORE.get_state(runId)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")

    result_block = record.state.get("result", {})
    output_text = str(result_block.get("output_text", ""))
    if body.input:
        supplement = _flatten_input(body.input)
        if supplement:
            output_text = f"{output_text}\n{supplement}".strip()

    usage = dict(result_block.get("usage", {}))

    return ExecuteResponse(
        ok=True,
        runId=runId,
        threadId=str(record.metadata.get("thread_id", "")),
        output_text=output_text,
        usage=usage,
        message="resumed",
    )


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


def _flatten_input(user_input: Any) -> str:
    if user_input is None:
        return ""
    if isinstance(user_input, str):
        return user_input
    if isinstance(user_input, dict):
        return " ".join(f"{key}:{_flatten_input(value)}" for key, value in user_input.items())
    if isinstance(user_input, (list, tuple, set)):
        return " ".join(_flatten_input(item) for item in user_input)
    return str(user_input)
