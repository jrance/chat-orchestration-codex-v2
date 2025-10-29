"""Execution endpoints, including SSE streaming stub."""

import asyncio
from collections.abc import AsyncIterator
from typing import Any, Mapping

from fastapi import APIRouter, Header
from fastapi.responses import StreamingResponse

from .models import ExecuteRequest, ExecuteResponse
from ...utils.sse import sse_stream

router = APIRouter(prefix="/v1", tags=["v1"])


@router.post("/execute", response_model=ExecuteResponse, status_code=501)
async def execute(req: ExecuteRequest) -> ExecuteResponse:  # pragma: no cover - simple stub
    """Stub synchronous execution endpoint."""
    return ExecuteResponse(ok=False, message="Execute not yet implemented")

# Future transport usage (PR-08/09 will wire orchestration to the client):
# from app.http.openai_client import OpenAICompatibleClient
# await OpenAICompatibleClient().post_responses(..., tenant_id="...", correlation_id="...", request_id="...")


@router.get("/execute/{runId}/resume", response_model=ExecuteResponse, status_code=501)
async def resume(runId: str) -> ExecuteResponse:  # pragma: no cover - simple stub
    """Stub resume endpoint for later human-in-the-loop flows."""
    return ExecuteResponse(ok=False, runId=runId, message="Resume not yet implemented")


@router.post("/execute/stream")
async def execute_stream(
    req: ExecuteRequest,
    x_telemetry: str | None = Header(default=None, alias="X-Telemetry"),
) -> StreamingResponse:
    """Stub SSE endpoint to verify streaming plumbing."""

    async def gen() -> AsyncIterator[Mapping[str, Any]]:
        yield {"event": "response.created", "data": '{"status":"starting"}'}
        await asyncio.sleep(0.01)
        yield {"event": "response.output_text.delta", "data": "streaming ready..."}
        await asyncio.sleep(0.01)
        yield {"event": "response.completed", "data": '{"status":"done"}'}

    return StreamingResponse(
        sse_stream(gen()),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )
