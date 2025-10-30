import copy
import time

import pytest

from app.compiler import registry
from app.compiler.builder import GraphBuilder
from app.ir.loader import build_runtime_plan
from app.runtime.agents import codeless as codeless_mod
from app.runtime.agents.codeless import LLMResult
from app.runtime.checkpointer import reset_checkpointer
from app.runtime.engine import execute_once, resume_run
from app.runtime.state_store import get_run_state_store, reset_run_state_store


def pkg_two_agents():
    agent_a = {
        "id": "agent_a",
        "kind": "agent.codeless",
        "label": "Agent A",
        "data": {
            "systemInstructions": "Step one",
            "model": {
                "provider": "openai",
                "modelId": "gpt-4o",
                "temperature": 0.1,
                "topP": 1,
                "maxTokens": 64,
            },
            "context": {"historyWindow": {"mode": "LastN", "n": 3}},
            "tools": {"policy": "Disabled", "attached": []},
        },
    }
    agent_b = {
        "id": "agent_b",
        "kind": "agent.codeless",
        "label": "Agent B",
        "data": {
            "systemInstructions": "Step two",
            "model": {
                "provider": "openai",
                "modelId": "gpt-4o",
                "temperature": 0.1,
                "topP": 1,
                "maxTokens": 64,
            },
            "context": {"historyWindow": {"mode": "LastN", "n": 3}},
            "tools": {"policy": "Disabled", "attached": []},
        },
    }
    return {
        "meta": {"id": "pkg", "name": "TwoAgentFlow", "version": "1.0.0"},
        "nodes": [agent_a, agent_b],
        "edges": [{"id": "edge", "from": "agent_a", "to": "agent_b"}],
        "entryId": "agent_a",
    }


@pytest.fixture(autouse=True)
def _reset_runtime_state():
    registry.clear()
    reset_run_state_store()
    reset_checkpointer()
    yield
    registry.clear()
    reset_run_state_store()
    reset_checkpointer()


@pytest.fixture(autouse=True)
def _stub_llm(monkeypatch: pytest.MonkeyPatch):
    async def _invoke_stub(state, agent_node, prompt):
        new_state = copy.deepcopy(state)
        messages = list(new_state.get("messages") or [])
        messages.append({"role": "assistant", "content": f"stub-{agent_node.get('id')}"})
        new_state["messages"] = messages
        return LLMResult(
            state=new_state,
            response={"output_text": "stub"},
            output_text="stub",
            usage={"output_tokens": 1},
        )

    monkeypatch.setattr(codeless_mod, "invoke_llm", _invoke_stub)
    yield


@pytest.mark.anyio
async def test_resume_accumulates_messages():
    ok, plan, errs = build_runtime_plan(pkg_two_agents(), {})
    assert ok, errs

    app = GraphBuilder(plan).build()
    graph_id = "graph-test"
    registry.put(graph_id, app)

    run_id = "run-1"
    first_state = await execute_once(
        graph_id,
        run_id,
        {"messages": [{"role": "user", "content": "hello", "ts": time.time()}]},
    )
    assert len(first_state.get("messages", [])) >= 1

    second_state = await resume_run(
        graph_id,
        run_id,
        {"messages": [{"role": "user", "content": "again", "ts": time.time()}]},
    )
    assert len(second_state.get("messages", [])) >= len(first_state.get("messages", []))

    store = get_run_state_store()
    record = store.get_state(run_id)
    assert record is not None
    assert record.metadata.get("invocations") == 2
