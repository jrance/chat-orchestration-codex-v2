import asyncio
import pytest

from app.api.deps import ExecutionContext, ExecutionHeaders, build_telemetry_context
from app.api.models import TelemetryLevel, TelemetryRedactionMode
from app.config.settings import settings
from app.runtime.agents import codeless as codeless_mod
from app.runtime.context import RuntimeContext, reset_runtime_context, set_runtime_context
from app.telemetry.streamer import TelemetryStreamConfig, TelemetryStreamer


@pytest.mark.anyio
async def test_llm_request_response_emitted_with_previews(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "telemetry_payload_max_chars", 32, raising=False)

    headers = ExecutionHeaders(
        tenant_id="tenant-telemetry",
        telemetry=TelemetryLevel.VERBOSE,
        telemetry_override=True,
        telemetry_redaction=TelemetryRedactionMode.SAFE,
        telemetry_redaction_override=True,
        correlation_id="corr-1",
        request_id="req-1",
        timestamp="2025-11-02T08:00:00Z",
    )
    context = ExecutionContext(headers=headers)
    context.ensure_run_ids()
    context.telemetry = build_telemetry_context(headers)

    streamer = TelemetryStreamer(
        TelemetryStreamConfig(
            run_id=context.run_id or "run-test",
            level=TelemetryLevel.VERBOSE,
            redaction=TelemetryRedactionMode.SAFE,
            payload_max_chars=settings.telemetry_payload_max_chars,
        )
    )

    runtime_ctx = RuntimeContext(execution=context, telemetry=streamer)
    token = set_runtime_context(runtime_ctx)

    captured_headers = {}
    captured_body = {}

    async def _fake_stream_response(body, *, context_headers=None, **_kwargs):
        captured_headers.update(context_headers or {})
        captured_body.update(body)
        yield {"type": "response.created"}
        yield {"type": "response.completed", "usage": {"output_tokens": 3}}

    monkeypatch.setattr("app.providers.openai_like.responses.stream_response", _fake_stream_response)

    agent_node = {
        "data": {
            "model": {"provider": "openai", "modelId": "gpt-4o"},
            "tools": {"policy": "Disabled", "attached": []},
        }
    }

    # Collect telemetry asynchronously while streaming requests.
    events = []

    async def _consume():
        async for event in streamer.stream():
            events.append(event)

    consumer = asyncio.create_task(_consume())

    try:
        async for _ in codeless_mod.stream_codeless({"messages": []}, agent_node, "latest news"):
            pass
    finally:
        await streamer.close()
        await consumer
        reset_runtime_context(token)

    request_event = next(evt for evt in events if evt.event == "telemetry.llm.request")
    response_event = next(evt for evt in events if evt.event == "telemetry.llm.response")

    assert captured_headers["X-Tenant-Id"] == "tenant-telemetry"
    assert "Authorization" not in captured_headers

    assert request_event.payload["provider"] == "openai"
    assert request_event.payload["model"] == "gpt-4o"
    assert request_event.payload["headers"]["X-Tenant-Id"] == "tenant-telemetry"
    assert "body_preview" in request_event.payload
    assert "latest news" in request_event.payload["body_preview"]

    assert response_event.payload["status_code"] == 200
    assert response_event.payload["usage"]["output_tokens"] == 3
    assert "body_preview" in response_event.payload
