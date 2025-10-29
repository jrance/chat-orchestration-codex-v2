from app.ir.loader import build_runtime_plan


def _package_min() -> dict:
    agent_node = {
        "id": "a",
        "kind": "agent.codeless",
        "label": "Agent A",
        "data": {
            "systemInstructions": "Hi {tenant_id}",
            "styleGuide": "Friendly",
            "model": {
                "provider": "openai",
                "modelId": "gpt-4o",
                "temperature": 0.3,
                "topP": 1,
                "maxTokens": 64,
            },
            "context": {
                "historyWindow": {"mode": "LastN", "n": 5},
                "injectOrgPreamble": False,
            },
            "tools": {"policy": "Disabled", "attached": []},
        },
    }
    return {
        "meta": {"id": "m", "name": "pkg", "version": "1.0.0"},
        "nodes": [agent_node],
        "edges": [],
        "entryId": "a",
    }


def test_build_runtime_plan_success() -> None:
    ok, plan, errors = build_runtime_plan(_package_min(), {"tenant_id": "t-123"})

    assert ok is True
    assert errors == []
    assert plan["entryId"] == "a"
    assert "a" in plan["agentPrompts"]
    assert "t-123" in plan["agentPrompts"]["a"]
    assert plan["warnings"] == []
