from collections.abc import AsyncIterator
import copy

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.runtime.agents.codeless import LLMResult


@pytest.fixture
async def async_client() -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture
def stub_llm(monkeypatch: pytest.MonkeyPatch):
    async def _invoke(state, agent_node, prompt):
        new_state = copy.deepcopy(state)
        messages = list(new_state.get("messages") or [])
        messages.append({"role": "assistant", "content": "stub-response"})
        new_state["messages"] = messages
        return LLMResult(
            state=new_state,
            response={"output_text": "stub-response", "usage": {"output_tokens": 1}},
            output_text="stub-response",
            usage={"output_tokens": 1},
        )

    async def _stream(state, agent_node, prompt):
        yield {"type": "response.created", "status": "in_progress"}
        yield {"type": "response.output_text.delta", "delta": "stub "}
        yield {
            "type": "response.completed",
            "output_text": "stub-response",
            "usage": {"output_tokens": 1},
        }

    monkeypatch.setattr("app.runtime.agents.codeless.invoke_llm", _invoke)
    monkeypatch.setattr("app.runtime.engine.stream_codeless", _stream)

    return _invoke
