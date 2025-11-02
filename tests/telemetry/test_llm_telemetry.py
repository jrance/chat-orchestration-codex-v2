import pytest

from app.api.deps import ExecutionContext, ExecutionHeaders, build_telemetry_context
from app.api.models import TelemetryLevel, TelemetryRedactionMode
from app.config.settings import settings
from app.runtime.engine import run_stream
from app.telemetry.streamer import TelemetryStreamConfig, TelemetryStreamer


def _sample_ir() -> dict:
    return {
        "meta": {"id": "telemetry-ir", "name": "TelemetryTest", "version": "1.0.0"},
        "nodes": [
            {
                "id": "agent",
                "kind": "agent.codeless",
                "label": "Agent",
                "data": {
                    "systemInstructions": "Answer briefly.",
                    "model": {"provider": "openai", "modelId": "gpt-4o-mini"},
                    "context": {"historyWindow": {"mode": "LastN", "n": 3}},
                    "tools": {"policy": "Disabled", "attached": []},
                },
            }
        ],
        "edges": [],
        "entryId": "agent",
    }


def _build_context(level: TelemetryLevel) -> tuple[ExecutionContext, TelemetryStreamer]:
    headers = ExecutionHeaders(
        tenant_id="tenant-telemetry",
        telemetry=level,
        telemetry_override=True,
        telemetry_redaction=TelemetryRedactionMode.SAFE,
        telemetry_redaction_override=True,
        correlation_id="corr-test",
        request_id="req-test",
        timestamp="2025-11-02T10:00:00Z",
    )
    context = ExecutionContext(headers=headers)
    context.ensure_run_ids()
    context.telemetry = build_telemetry_context(headers)
    telemetry_cfg = TelemetryStreamConfig(
        run_id=context.run_id or "run-test",
        level=context.telemetry.level,
        redaction=context.telemetry.redaction,
        payload_max_chars=context.telemetry.payload_max_chars,
    )
    streamer = TelemetryStreamer(telemetry_cfg)
    return context, streamer


async def _fake_stream_response(body, *, context_headers=None, **_kwargs):
    yield {"type": "response.created"}
    yield {"type": "response.output_text.delta", "delta": "Hello!"}
    yield {"type": "response.output_text.done"}
    yield {"type": "response.completed", "usage": {"output_tokens": 4}}


@pytest.mark.anyio
async def test_verbose_stream_emits_request_and_response(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("app.providers.openai_like.responses.stream_response", _fake_stream_response)
    monkeypatch.setattr(settings, "telemetry_enabled", True, raising=False)

    context, streamer = _build_context(TelemetryLevel.VERBOSE)

    events = []
    async for event in run_stream(_sample_ir(), "latest telemetry news", context, streamer):
        events.append(event)
    await streamer.close()

    request_event = next(
        evt for evt in events if evt.event == "response.telemetry.delta" and evt.data.get("direction") == "request"
    )
    response_event = next(
        evt for evt in events if evt.event == "response.telemetry.delta" and evt.data.get("direction") == "response"
    )
    done_event = next(evt for evt in events if evt.event == "response.telemetry.done")

    assert request_event.data["headers"]["X-Tenant-Id"] == "tenant-telemetry"
    assert "body_preview" in request_event.data
    assert "latest telemetry news" in request_event.data["body_preview"]

    assert response_event.data["status_code"] == 200
    assert "body_preview" in response_event.data
    assert response_event.data["usage"]["output_tokens"] == 4
    assert done_event.data["channel"] == "llm"


@pytest.mark.anyio
async def test_basic_stream_omits_bodies(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("app.providers.openai_like.responses.stream_response", _fake_stream_response)
    monkeypatch.setattr(settings, "telemetry_enabled", True, raising=False)

    context, streamer = _build_context(TelemetryLevel.BASIC)

    events = []
    async for event in run_stream(_sample_ir(), "basic telemetry", context, streamer):
        events.append(event)
    await streamer.close()

    request = next(
        evt for evt in events if evt.event == "response.telemetry.delta" and evt.data.get("direction") == "request"
    )
    response = next(
        evt for evt in events if evt.event == "response.telemetry.delta" and evt.data.get("direction") == "response"
    )

    assert "body" not in request.data
    assert "body_preview" not in request.data
    assert "body" not in response.data
    assert "body_preview" not in response.data


@pytest.mark.anyio
async def test_telemetry_off_emits_no_events(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("app.providers.openai_like.responses.stream_response", _fake_stream_response)

    context, streamer = _build_context(TelemetryLevel.NONE)

    events = []
    async for event in run_stream(_sample_ir(), "no telemetry", context, streamer):
        events.append(event)
    await streamer.close()

    telemetry_events = [evt for evt in events if evt.event.startswith("response.telemetry")]
    assert telemetry_events == []
