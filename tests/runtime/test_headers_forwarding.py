import pytest

from app.api.deps import ExecutionContext, ExecutionHeaders, build_telemetry_context
from app.runtime.agents import codeless as codeless_mod
from app.runtime.context import RuntimeContext, reset_runtime_context, set_runtime_context


@pytest.mark.anyio
async def test_execution_headers_forwarded_to_llm(monkeypatch: pytest.MonkeyPatch):
    captured_headers = {}

    async def _fake_stream_response(body, *, context_headers=None, **_kwargs):
        captured_headers.update(context_headers or {})
        yield {"type": "response.created"}
        yield {"type": "response.completed"}

    monkeypatch.setattr("app.providers.openai_like.responses.stream_response", _fake_stream_response)

    headers = ExecutionHeaders(
        tenant_id="tenant-forward",
        correlation_id="corr-forward",
        request_id="req-forward",
        timestamp="2025-11-02T08:00:00Z",
    )
    context = ExecutionContext(headers=headers)
    context.ensure_run_ids()
    context.telemetry = build_telemetry_context(headers)

    runtime_ctx = RuntimeContext(execution=context, telemetry=None)
    token = set_runtime_context(runtime_ctx)

    try:
        agent_node = {
            "data": {
                "model": {"provider": "openai", "modelId": "gpt-4o"},
                "tools": {"policy": "Disabled", "attached": []},
            }
        }

        async for _ in codeless_mod.stream_codeless({"messages": []}, agent_node, "test prompt"):
            pass
    finally:
        reset_runtime_context(token)

    assert captured_headers["X-Tenant-Id"] == "tenant-forward"
    assert captured_headers["X-Correlation-Id"] == "corr-forward"
    assert captured_headers["X-Request-Id"] == "req-forward"
    assert captured_headers["X-Timestamp"] == "2025-11-02T08:00:00Z"
    assert "X-Telemetry" not in captured_headers
