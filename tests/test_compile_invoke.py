import time

import pytest

from app.compiler.builder import GraphBuilder
from app.ir.loader import build_runtime_plan


def pkg_single_agent():
    agent = {
        "id": "agent",
        "kind": "agent.codeless",
        "label": "Agent",
        "data": {
            "systemInstructions": "Say hi",
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
    return {
        "meta": {"id": "simple", "name": "SingleAgent", "version": "1.0.0"},
        "nodes": [agent],
        "edges": [],
        "entryId": "agent",
    }


@pytest.mark.anyio
async def test_stub_agent_invocation_produces_message(stub_llm):
    ok, plan, errs = build_runtime_plan(pkg_single_agent(), {})
    assert ok, errs

    app = GraphBuilder(plan).build()
    state = await app.ainvoke({"messages": [{"role": "user", "content": "hello", "ts": time.time()}]})

    assistant_msgs = [m for m in state.get("messages", []) if m.get("role") == "assistant"]
    assert assistant_msgs, "Expected stub agent to emit an assistant message"
