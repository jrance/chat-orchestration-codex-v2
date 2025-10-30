import copy

import pytest

from app.api.deps import ExecutionContext
from app.runtime.agents.codeless import LLMResult
from app.runtime.engine import run_stream
from tests.runtime._groupchat_utils import build_groupchat_ir, default_headers


@pytest.fixture
def groupchat_stubs(monkeypatch: pytest.MonkeyPatch):
    call_counts: dict[str, int] = {"alpha": 0, "beta": 0}

    async def fake_invoke_llm(state, agent_node, prompt, **_kwargs):
        del prompt
        new_state = copy.deepcopy(state)
        messages = list(new_state.get("messages") or [])
        agent_id = agent_node.get("id")
        count = call_counts.get(agent_id, 0) + 1
        call_counts[agent_id] = count
        output = f"{agent_node.get('label')} turn {count}"
        messages.append({"role": "assistant", "content": output})
        new_state["messages"] = messages
        usage = {"output_tokens": len(output.split()), "input_tokens": 5}
        return LLMResult(state=new_state, response={}, output_text=output, usage=usage)

    monkeypatch.setattr("app.runtime.agents.codeless.invoke_llm", fake_invoke_llm)

    turn_sequence = ["alpha", "beta"]
    chooser_state = {"idx": 0}

    async def fake_choose_next_speaker(*_args, **_kwargs):
        idx = chooser_state["idx"]
        chooser_state["idx"] = idx + 1
        speaker = turn_sequence[idx % len(turn_sequence)]
        return {"speaker": speaker, "instruction": f"{speaker} go next.", "usage": {}}

    satisfaction_calls = {"count": 0}

    async def fake_is_satisfied(*_args, **_kwargs):
        satisfaction_calls["count"] += 1
        return {"satisfied": satisfaction_calls["count"] >= 2, "rationale": "", "usage": {}}

    async def fake_generate_synthesis(*_args, **_kwargs):
        return {
            "answer": "Moderator final answer.",
            "confidence": 0.9,
            "usage": {"output_tokens": 4},
        }

    async def fake_has_consensus(*_args, **_kwargs):
        return {"allAgree": False, "summary": "", "confidence": 0.0, "usage": {}}

    monkeypatch.setattr("app.runtime.patterns.groupchat.choose_next_speaker", fake_choose_next_speaker)
    monkeypatch.setattr("app.runtime.patterns.groupchat.is_satisfied", fake_is_satisfied)
    monkeypatch.setattr("app.runtime.patterns.groupchat.generate_synthesis", fake_generate_synthesis)
    monkeypatch.setattr("app.runtime.patterns.groupchat.has_consensus", fake_has_consensus)

    return satisfaction_calls


@pytest.mark.anyio
async def test_groupchat_turns_and_stop(groupchat_stubs):
    context = ExecutionContext(headers=default_headers())
    ir = build_groupchat_ir(["alpha", "beta"], max_turns=3, stop_when="ModeratorSatisfied")

    events = []
    async for event in run_stream(ir, "Need guidance", context, None):
        events.append(event)

    delta_events = [evt for evt in events if evt.event == "response.output_text.delta"]
    assert len(delta_events) == 1, "Only the final answer should appear on the main stream"

    completed = next(evt for evt in events if evt.event == "response.completed")
    metadata = completed.data["response"]["metadata"]["groupchat"]
    assert metadata["turns"] == 2
    assert metadata["stopWhen"] == "ModeratorSatisfied"
    assert metadata["finalSpeaker"] == "Moderator"
    assert completed.data["output_text"] == "Moderator final answer."
    assert groupchat_stubs["count"] == 2
