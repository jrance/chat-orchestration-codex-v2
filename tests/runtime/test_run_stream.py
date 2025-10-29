import pytest

from app.api.deps import ExecutionContext, ExecutionHeaders
from app.runtime.engine import ExecutionResult, run_once, run_stream
from app.telemetry.models import TelemetryLevel


def _headers() -> ExecutionHeaders:
    return ExecutionHeaders(
        tenant_id="tenant-1",
        telemetry=TelemetryLevel.NONE,
        correlation_id="corr",
        request_id="req",
        timestamp="2024-01-01T00:00:00Z",
        client_id=None,
        extra_headers={},
    )


@pytest.mark.anyio
async def test_run_stream_emits_expected_events():
    context = ExecutionContext(headers=_headers())
    events = []
    async for event in run_stream({"meta": {}, "nodes": [], "edges": []}, "hello runtime", context, None):
        events.append(event)

    assert events[0].event == "response.created"
    assert any(evt.event == "response.output_text.delta" for evt in events)
    assert events[-1].event == "response.completed"
    assert events[-1].data["output_text"].startswith("Echo:")


@pytest.mark.anyio
async def test_run_once_returns_execution_result():
    context = ExecutionContext(headers=_headers())
    result: ExecutionResult = await run_once({"meta": {}, "nodes": [], "edges": []}, "hello sync", context, None)

    assert result.run_id
    assert result.thread_id
    assert result.output_text.startswith("Echo:")
    assert result.usage["output_tokens"] >= 1
