import pytest
from httpx import AsyncClient


@pytest.mark.anyio
async def test_orphan_unreachable_node(async_client: AsyncClient):
    agent = {
        "id": "agent-1",
        "kind": "agent.codeless",
        "label": "Agent",
        "data": {
            "systemInstructions": "Assist.",
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
    }
    tool = {
        "id": "tool-1",
        "kind": "tool",
        "label": "Tool",
        "data": {"name": "tool:echo", "argsSchema": {"type": "object"}},
    }
    orphan = {
        "id": "orphan",
        "kind": "tool",
        "label": "Orphan",
        "data": {"name": "tool:noop", "argsSchema": {"type": "object"}},
    }
    pkg = {
        "meta": {"id": "m", "name": "pkg", "version": "1.0.0"},
        "nodes": [agent, tool, orphan],
        "edges": [{"id": "edge-1", "from": "agent-1", "to": "tool-1"}],
        "entryId": "agent-1",
    }

    response = await async_client.post("/v1/validate", json=pkg)

    payload = response.json()
    assert payload["ok"] is False
    assert "Unreachable nodes from entryId" in " ".join(payload["errors"])
