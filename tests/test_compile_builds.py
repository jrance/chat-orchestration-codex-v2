import pytest

from app.compiler import registry
from app.compiler.builder import GraphBuilder
from app.ir.loader import build_runtime_plan


def pkg_router_flow():
    router = {
        "id": "router",
        "kind": "router",
        "label": "Router",
        "data": {
            "routeSchema": {"type": "object"},
            "minConfidence": 0.0,
            "prompt": "Decide next step",
            "targets": ["seq", "missing"],
        },
    }
    sequential = {"id": "seq", "kind": "sequential", "label": "Sequence", "data": {}}
    agent_a = {
        "id": "agent_a",
        "kind": "agent.codeless",
        "label": "AgentA",
        "data": {
            "systemInstructions": "Respond to the user.",
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
    concurrent = {"id": "cc", "kind": "concurrent", "label": "Concurrent", "data": {}}
    agent_b = {
        "id": "agent_b",
        "kind": "agent.codeless",
        "label": "AgentB",
        "data": {
            "systemInstructions": "Wrap up.",
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
    edges = [
        {"id": "e1", "from": "router", "to": "seq"},
        {"id": "e2", "from": "seq", "to": "agent_a"},
        {"id": "e3", "from": "agent_a", "to": "cc"},
        {"id": "e4", "from": "cc", "to": "agent_b"},
    ]
    return {
        "meta": {"id": "pkg", "name": "RouterFlow", "version": "1.0.0"},
        "nodes": [router, sequential, agent_a, concurrent, agent_b],
        "edges": edges,
        "entryId": "router",
    }


def pkg_unknown_passthrough():
    tool_node = {
        "id": "tool",
        "kind": "tool",
        "label": "Tool",
        "data": {"name": "noop", "argsSchema": {}},
    }
    return {
        "meta": {"id": "pkg2", "name": "Unknown", "version": "1.0.0"},
        "nodes": [tool_node],
        "edges": [],
        "entryId": "tool",
    }


@pytest.mark.anyio
async def test_graph_compiles_and_routes(stub_llm):
    ok, plan, errs = build_runtime_plan(pkg_router_flow(), {})
    assert ok, errs

    builder = GraphBuilder(plan)
    app = builder.build()
    state = await app.ainvoke({"messages": [], "scratch": {}})

    assert state["route"]["target"] == "seq"
    assistant_msgs = [m for m in state.get("messages", []) if m.get("role") == "assistant"]
    assert len(assistant_msgs) == 2

    registry.put("test-graph", app)
    try:
        assert registry.get("test-graph") is app
    finally:
        registry.clear()


@pytest.mark.anyio
async def test_unknown_kind_compiles_to_passthrough():
    ok, plan, errs = build_runtime_plan(pkg_unknown_passthrough(), {})
    assert ok, errs

    app = GraphBuilder(plan).build()
    start_state = {"scratch": {"value": 42}}
    final_state = app.invoke(start_state)

    assert final_state.get("scratch", {}).get("value") == 42
