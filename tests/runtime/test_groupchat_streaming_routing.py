import copy

import pytest

from app.api.deps import ExecutionContext
from app.runtime.agents.codeless import LLMResult
from app.runtime.engine import run_stream
from app.telemetry.events import GROUPCHAT_TURN_COMPLETED, GROUPCHAT_TURN_DELTA
from tests.runtime._groupchat_utils import build_groupchat_ir, default_headers


@pytest.fixture
def groupchat_streaming_stubs(monkeypatch: pytest.MonkeyPatch):
    telemetry_events: list[tuple[str, dict]] = []

    async def capture_publish(_telemetry, event: str, payload: dict):
        telemetry_events.append((event, dict(payload)))

    monkeypatch.setattr("app.runtime.patterns.groupchat._publish", capture_publish)

    call_counts: dict[str, int] = {"alpha": 0, "beta": 0}

    async def fake_invoke_llm(state, agent_node, prompt, **_kwargs):
        del prompt
        new_state = copy.deepcopy(state)
        messages = list(new_state.get("messages") or [])
        agent_id = agent_node.get("id")
        count = call_counts.get(agent_id, 0) + 1
        call_counts[agent_id] = count
        output = f"{agent_node.get('label')} insight {count}"
        messages.append({"role": "assistant", "content": output})
        new_state["messages"] = messages
        usage = {"output_tokens": len(output.split()), "input_tokens": 4}
        return LLMResult(state=new_state, response={}, output_text=output, usage=usage)

    monkeypatch.setattr("app.runtime.agents.codeless.invoke_llm", fake_invoke_llm)

    order = ["alpha", "beta"]
    chooser_state = {"idx": 0}

    async def fake_choose_next_speaker(*_args, **_kwargs):
        idx = chooser_state["idx"]
        chooser_state["idx"] = idx + 1
        speaker = order[idx % len(order)]
        return {"speaker": speaker, "instruction": f"{speaker} respond.", "usage": {}}

    async def fake_is_satisfied(*_args, **_kwargs):
        # stop after the second participant
        count = chooser_state["idx"]
        return {"satisfied": count >= 2, "rationale": "", "usage": {}}

    async def fake_generate_synthesis(*_args, **_kwargs):
        return {"answer": "Moderator wraps up.", "confidence": 0.75, "usage": {"output_tokens": 3}}

    async def fake_has_consensus(*_args, **_kwargs):
        return {"allAgree": False, "summary": "", "confidence": 0.0, "usage": {}}

    monkeypatch.setattr("app.runtime.patterns.groupchat.choose_next_speaker", fake_choose_next_speaker)
    monkeypatch.setattr("app.runtime.patterns.groupchat.is_satisfied", fake_is_satisfied)
    monkeypatch.setattr("app.runtime.patterns.groupchat.generate_synthesis", fake_generate_synthesis)
    monkeypatch.setattr("app.runtime.patterns.groupchat.has_consensus", fake_has_consensus)

    return telemetry_events


@pytest.mark.anyio
async def test_groupchat_streaming_routing(groupchat_streaming_stubs):
    context = ExecutionContext(headers=default_headers())
    ir = build_groupchat_ir(["alpha", "beta"], max_turns=3, stop_when="ModeratorSatisfied")

    events = []
    async for event in run_stream(ir, "Share status", context, None):
        events.append(event)

    delta_events = [evt for evt in events if evt.event == "response.output_text.delta"]
    assert len(delta_events) == 1
    assert delta_events[0].data["delta"] == "Moderator wraps up."

    completed = next(evt for evt in events if evt.event == "response.completed")
    metadata = completed.data["response"]["metadata"]["groupchat"]
    assert metadata["turns"] == 2
    assert metadata["participants"] == ["Alpha", "Beta"]

    telemetry_events = groupchat_streaming_stubs
    delta_payloads = [payload for event, payload in telemetry_events if event == GROUPCHAT_TURN_DELTA]
    completed_payloads = [payload for event, payload in telemetry_events if event == GROUPCHAT_TURN_COMPLETED]
    assert len(delta_payloads) == 2
    assert len(completed_payloads) == 2
    assert all(payload["delta"].startswith(("Alpha", "Beta")) for payload in delta_payloads)
