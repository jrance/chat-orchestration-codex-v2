import asyncio

import pytest

from app.tools import clear_tools, invoke_tool, register_tool


@pytest.fixture(autouse=True)
def _reset_tools() -> None:
    clear_tools()
    from app.tools.builtin import register_all

    register_all()
    yield
    clear_tools()
    register_all()


@pytest.mark.anyio
async def test_invoke_tool_times_out() -> None:
    async def _slow_handler(args):
        await asyncio.sleep(0.05)
        return {"ok": True}

    register_tool(
        {
            "id": "tool:slow",
            "name": "Slow Tool",
            "args_schema": {"type": "object", "properties": {}},
            "arg_behaviors": {},
            "handler": _slow_handler,
        }
    )

    with pytest.raises(asyncio.TimeoutError):
        await invoke_tool("tool:slow", {}, per_call_timeout_ms=10)
