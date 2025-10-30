import copy

import pytest

from app.api.deps import ExecutionContext
from app.runtime.agents.codeless import LLMResult
from app.runtime.engine import run_once
from tests.runtime._groupchat_utils import build_groupchat_ir, default_headers


@pytest.fixture
def groupchat_allagree_stubs(monkeypatch: pytest.MonkeyPatch):
    call_counts: dict[str, int] = {"alpha": 0, "beta": 0, "gamma": 0}

    async def fake_invoke_llm(state, agent_node, prompt, **_kwargs):
        del prompt
        new_state = copy.deepcopy(state)
        messages = list(new_state.get("messages") or [])
        agent_id = agent_node.get("id")
        count = call_counts.get(agent_id, 0) + 1
        call_counts[agent_id] = count
        output = f"{agent_node.get('label')} perspective {count}"
        messages.append({"role": "assistant", "content": output})
        new_state["messages"] = messages
        usage = {"output_tokens": len(output.split()), "input_tokens": 6}
        return LLMResult(state=new_state, response={}, output_text=output, usage=usage)

    monkeypatch.setattr("app.runtime.agents.codeless.invoke_llm", fake_invoke_llm)

    rotation = ["alpha", "beta", "gamma"]
    chooser_state = {"idx": 0}

    async def fake_choose_next_speaker(*_args, **_kwargs):
        idx = chooser_state["idx"]
        chooser_state["idx"] = idx + 1
        speaker = rotation[idx % len(rotation)]
        return {"speaker": speaker, "instruction": f"{speaker} weigh in.", "usage": {}}

    async def fake_is_satisfied(*_args, **_kwargs):
        return {"satisfied": False, "rationale": "", "usage": {}}

    consensus_calls = {"count": 0}

    async def fake_has_consensus(*_args, **_kwargs):
        consensus_calls["count"] += 1
        done = consensus_calls["count"] >= 2
        return {"allAgree": done, "summary": "Aligned", "confidence": 0.8 if done else 0.3, "usage": {}}

    async def fake_generate_synthesis(*_args, **_kwargs):
        return {"answer": "Moderator consensus summary.", "confidence": 0.85, "usage": {"output_tokens": 5}}

    monkeypatch.setattr("app.runtime.patterns.groupchat.choose_next_speaker", fake_choose_next_speaker)
    monkeypatch.setattr("app.runtime.patterns.groupchat.is_satisfied", fake_is_satisfied)
    monkeypatch.setattr("app.runtime.patterns.groupchat.has_consensus", fake_has_consensus)
    monkeypatch.setattr("app.runtime.patterns.groupchat.generate_synthesis", fake_generate_synthesis)

    return consensus_calls


@pytest.mark.anyio
async def test_groupchat_allagree(groupchat_allagree_stubs):
    context = ExecutionContext(headers=default_headers())
    ir = build_groupchat_ir(["alpha", "beta", "gamma"], max_turns=4, stop_when="AllAgree")

    result = await run_once(ir, "Share best practice", context, None)

    metadata = result.response["metadata"]["groupchat"]
    assert metadata["turns"] == 2
    assert metadata["stopWhen"] == "AllAgree"
    assert metadata["finalSpeaker"] == "Moderator"
    assert result.output_text == "Moderator consensus summary."
    assert groupchat_allagree_stubs["count"] == 2
