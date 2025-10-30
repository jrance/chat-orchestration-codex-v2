import pytest

from app.api.deps import ExecutionContext, ExecutionHeaders
from app.runtime.engine import run_once
from app.runtime.state_store import get_run_state_store


def _headers() -> ExecutionHeaders:
    return ExecutionHeaders(
        tenant_id="tenant-1",
        telemetry=None,
        correlation_id="corr",
        request_id="req",
        timestamp="2024-01-01T00:00:00Z",
        client_id=None,
        extra_headers={},
    )


def _router_ir(min_confidence: float = 0.5, fallback: dict | None = None) -> dict:
    fallback_cfg = fallback or {"mode": "Error"}
    return {
        "meta": {"id": "pkg", "name": "Router", "version": "1.0.0"},
        "nodes": [
            {
                "id": "router",
                "kind": "router",
                "label": "Supervisor",
                "data": {
                    "prompt": "Route to <targets>.",
                    "minConfidence": min_confidence,
                    "tieBreak": "HighestConfidence",
                    "fallback": fallback_cfg,
                    "allowBelowMinForTieBreak": False,
                    "routeSchema": {
                        "type": "object",
                        "properties": {
                            "target": {"type": "string"},
                            "confidence": {"type": "number"},
                        },
                        "required": ["target"],
                    },
                    "targets": ["Policy Agent", "M365 Agent"],
                    "autoSyncEnum": True,
                },
            },
            {
                "id": "policy_agent",
                "kind": "agent.codeless",
                "label": "Policy Agent",
                "data": {
                    "systemInstructions": "Answer policy questions.",
                    "model": {
                        "provider": "openai",
                        "modelId": "gpt-4o",
                        "temperature": 0.1,
                        "topP": 1,
                        "maxTokens": 64,
                    },
                    "context": {"historyWindow": {"mode": "LastN", "n": 1}},
                    "tools": {"policy": "Disabled", "attached": []},
                },
            },
            {
                "id": "m365_agent",
                "kind": "agent.codeless",
                "label": "M365 Agent",
                "data": {
                    "systemInstructions": "Answer M365 questions.",
                    "model": {
                        "provider": "openai",
                        "modelId": "gpt-4o",
                        "temperature": 0.1,
                        "topP": 1,
                        "maxTokens": 64,
                    },
                    "context": {"historyWindow": {"mode": "LastN", "n": 1}},
                    "tools": {"policy": "Disabled", "attached": []},
                },
            },
        ],
        "edges": [
            {"id": "e1", "from": "router", "to": "policy_agent"},
            {"id": "e2", "from": "router", "to": "m365_agent"},
        ],
        "entryId": "router",
    }


@pytest.mark.anyio
async def test_router_highest_conf_selects_llm_target(stub_llm, monkeypatch: pytest.MonkeyPatch):
    async def _fake_router_llm(*_args, **_kwargs):
        return {}, {"output_text": '{"target":"M365 Agent","confidence":0.9}'}

    monkeypatch.setattr("app.runtime.patterns.router.run_router_llm", _fake_router_llm)

    context = ExecutionContext(headers=_headers())
    result = await run_once(_router_ir(), "Where is the vacation policy?", context)

    router_meta = result.response.get("metadata", {}).get("router", {})
    assert router_meta["targetLabel"] == "M365 Agent"
    assert router_meta["targetNodeId"] == "m365_agent"
    assert router_meta["fallbackMode"] is None
    assert router_meta["source"] == "llm"
    assert result.output_text.startswith("stub")
    store = get_run_state_store()
    record = store.get_state(result.run_id)
    assert record.metadata["status"] == "completed"
    store.delete_state(result.run_id)


@pytest.mark.anyio
async def test_router_highest_conf_uses_default_child_fallback(stub_llm, monkeypatch: pytest.MonkeyPatch):
    async def _fake_router_llm(*_args, **_kwargs):
        return {}, {"output_text": '{"target":"M365 Agent","confidence":0.9}'}

    monkeypatch.setattr("app.runtime.patterns.router.run_router_llm", _fake_router_llm)

    fallback = {"mode": "DefaultChild", "defaultChild": "Policy Agent"}
    context = ExecutionContext(headers=_headers())
    result = await run_once(_router_ir(min_confidence=0.95, fallback=fallback), "Need help", context)

    router_meta = result.response.get("metadata", {}).get("router", {})
    assert router_meta["targetLabel"] == "Policy Agent"
    assert router_meta["targetNodeId"] == "policy_agent"
    assert router_meta["fallbackMode"] == "DefaultChild"
    assert router_meta["source"] == "fallback"
    store = get_run_state_store()
    record = store.get_state(result.run_id)
    assert record.metadata["status"] == "completed"
    store.delete_state(result.run_id)
