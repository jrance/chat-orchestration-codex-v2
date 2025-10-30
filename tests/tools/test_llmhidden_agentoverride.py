import pytest

from app.tools import ArgMode, clear_tools, invoke_tool, register_tool


@pytest.fixture(autouse=True)
def _reset_tools() -> None:
    clear_tools()
    from app.tools.builtin import register_all

    register_all()
    yield
    clear_tools()
    register_all()


@pytest.mark.anyio
async def test_llmhidden_and_agentoverride_are_applied() -> None:
    captured = {}

    async def _handler(args):
        captured.update(args)
        return {"ok": True}

    register_tool(
        {
            "id": "tool:echo",
            "name": "Echo",
            "args_schema": {"type": "object", "properties": {"text": {"type": "string"}, "topN": {"type": "number"}}},
            "arg_behaviors": {
                "apiKey": {"mode": ArgMode.LLM_HIDDEN, "default": "abc"},
                "topN": {"mode": ArgMode.AGENT_OVERRIDE, "default": 5},
            },
            "handler": _handler,
        }
    )

    await invoke_tool("tool:echo", {"text": "hi", "topN": 1})

    assert captured["apiKey"] == "abc"
    assert captured["topN"] == 5
    assert captured["text"] == "hi"
