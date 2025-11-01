import copy
import json
from urllib.parse import parse_qs, urlparse

import pytest
from httpx import AsyncClient

from app.telemetry.models import TelemetryLevel
from app.runtime.agents import codeless as codeless_mod
from app.runtime.agents.codeless import LLMResult


def _sample_ir(tenant: str = "tenant-1") -> dict:
    return {
        "meta": {"id": "pkg-1", "name": "Test", "version": "1.0.0", "tenantId": tenant},
        "nodes": [
            {
                "id": "agent",
                "kind": "agent.codeless",
                "label": "Agent",
                "data": {
                    "systemInstructions": "Respond cheerfully.",
                    "model": {"provider": "openai", "modelId": "gpt-4o", "temperature": 0.1, "topP": 1, "maxTokens": 64, "stop": []},
                    "context": {"historyWindow": {"mode": "LastN", "n": 5}},
                    "tools": {"policy": "Disabled", "attached": []},
                },
            }
        ],
        "edges": [],
        "entryId": "agent",
    }


def _assert_event(sequence: list[str], event_name: str) -> bool:
    return any(f"event: {event_name}" in chunk for chunk in sequence)


@pytest.fixture(autouse=True)
def _stub_llm(monkeypatch: pytest.MonkeyPatch):
    async def _invoke_stub(state, agent_node, prompt, **_kwargs):
        new_state = copy.deepcopy(state)
        messages = list(new_state.get("messages") or [])
        messages.append({"role": "assistant", "content": "stub response"})
        new_state["messages"] = messages
        return LLMResult(
            state=new_state,
            response={"output_text": "stub response"},
            output_text="stub response",
            usage={"output_tokens": 2},
        )

    async def _stream_stub(state, agent_node, prompt, **_kwargs):
        yield {"type": "response.created", "status": "in_progress"}
        yield {"type": "response.output_text.delta", "delta": "stub "}
        yield {
            "type": "response.completed",
            "output_text": "stub response",
            "usage": {"output_tokens": 2},
        }

    monkeypatch.setattr(codeless_mod, "invoke_llm", _invoke_stub)
    monkeypatch.setattr("app.runtime.engine.stream_codeless", _stream_stub)
    yield


@pytest.mark.anyio
async def test_execute_requires_tenant(async_client: AsyncClient):
    response = await async_client.post("/v1/execute", json={"orchestration": _sample_ir(), "input": "hi"})
    assert response.status_code == 400
    assert "X-Tenant-Id" in response.text


@pytest.mark.anyio
async def test_execute_returns_result(async_client: AsyncClient):
    body = {"orchestration": _sample_ir(), "input": "hello world"}
    response = await async_client.post(
        "/v1/execute",
        json=body,
        headers={"X-Tenant-Id": "tenant-1"},
    )

    data = response.json()
    assert response.status_code == 201
    assert data["runId"]
    assert data["status"] in {"running", "completed"}
    assert data["sse"]["url"].endswith(data["runId"])
    # NOTE: Only `response.output_text.delta` chunks contribute to `output_tokens`.
    # The sentinel `response.output_text.done` event finalizes the stream without
    # incrementing usage to stay aligned with the Responses API accounting.
    assert data.get("usage", {}).get("output_tokens") == 2


@pytest.mark.anyio
async def test_stream_emits_response_events(async_client: AsyncClient):
    body = {"orchestration": _sample_ir(), "input": "stream me"}
    response = await async_client.post(
        "/v1/execute/stream",
        json=body,
        headers={"X-Tenant-Id": "tenant-1"},
    )

    payload = (await response.aread()).decode()
    frames = [frame for frame in payload.split("\n\n") if frame.strip()]

    assert _assert_event(frames, "response.created")
    assert _assert_event(frames, "response.output_text.delta")
    assert _assert_event(frames, "response.output_text.done")
    assert _assert_event(frames, "response.completed")


@pytest.mark.anyio
async def test_tenant_mismatch_rejected(async_client: AsyncClient):
    body = {"orchestration": _sample_ir(tenant="other-tenant"), "input": "hi"}
    response = await async_client.post(
        "/v1/execute",
        json=body,
        headers={"X-Tenant-Id": "tenant-1"},
    )
    assert response.status_code == 400
    assert "Tenant mismatch" in response.text


@pytest.mark.anyio
async def test_stream_returns_telemetry_header(async_client: AsyncClient):
    body = {"orchestration": _sample_ir(), "input": "telemetry please"}
    response = await async_client.post(
        "/v1/execute/stream",
        json=body,
        headers={"X-Tenant-Id": "tenant-1", "X-Telemetry": TelemetryLevel.BASIC.value},
    )

    assert response.status_code == 200
    telemetry_header = response.headers.get("X-Telemetry-Stream-Url")
    assert telemetry_header

    payload = (await response.aread()).decode()
    frames = [frame for frame in payload.split("\n\n") if frame.strip()]
    assert _assert_event(frames, "response.output_text.done")
    assert _assert_event(frames, "response.completed")

    run_id = parse_qs(urlparse(telemetry_header).query).get("runId", [""])[0]
    telemetry_response = await async_client.get(
        f"/v1/telemetry/stream?runId={run_id}",
        headers={"X-Tenant-Id": "tenant-1", "X-Telemetry": TelemetryLevel.BASIC.value},
    )
    telemetry_payload = (await telemetry_response.aread()).decode()
    assert "event: telemetry.run_started" in telemetry_payload
    assert "event: telemetry.run_completed" in telemetry_payload


@pytest.mark.anyio
async def test_stream_emits_tool_result_events(monkeypatch: pytest.MonkeyPatch, async_client: AsyncClient):
    from app.runtime import engine as engine_mod

    tool_runtime = codeless_mod.ToolRuntime(
        specs={},
        payload=[],
        policy="Enabled",
        max_calls=None,
        timeout_ms=None,
        parallelism=1,
        redact=False,
        enabled=True,
        mcp_servers={},
        name_reverse={},
        sanitized_names={},
    )

    monkeypatch.setattr(engine_mod.codeless_mod, "_prepare_tool_runtime", lambda *args, **kwargs: tool_runtime)

    async def _fake_execute(call, runtime, telemetry):
        return {"id": call.get("id"), "name": call.get("name"), "output": {"echo": call.get("arguments")}}

    monkeypatch.setattr(engine_mod.codeless_mod, "_execute_tool_call", _fake_execute)

    async def _fake_stream(state, agent_node, prompt, **_kwargs):
        yield {"type": "response.created"}
        yield {
            "type": "response.function_call_arguments.delta",
            "id": "call-1",
            "name": "echo",
            "arguments": '{"text":"hi"}',
        }
        yield {
            "type": "response.function_call_arguments.done",
            "id": "call-1",
            "name": "echo",
        }
        yield {"type": "response.completed", "output_text": "", "usage": {"output_tokens": 1}}

    monkeypatch.setattr(engine_mod, "stream_codeless", _fake_stream)

    body = {"orchestration": _sample_ir(), "input": "invoke tool"}
    response = await async_client.post(
        "/v1/execute/stream",
        json=body,
        headers={"X-Tenant-Id": "tenant-1"},
    )

    payload = (await response.aread()).decode()
    frames = [frame for frame in payload.split("\n\n") if frame.strip()]

    def _index(event_name: str) -> int:
        for idx, frame in enumerate(frames):
            if f"event: {event_name}" in frame:
                return idx
        raise AssertionError(f"{event_name} not found in stream: {frames}")

    created_idx = _index("response.tool_result.created")
    done_idx = _index("response.tool_result.done")
    completed_idx = _index("response.completed")

    assert created_idx < done_idx < completed_idx
    assert '"output":' in frames[done_idx]


