import pytest

from app.mcp import call_mcp_tool, clear_servers, get_server, register_server


@pytest.fixture(autouse=True)
def _reset_servers() -> None:
    clear_servers()
    yield
    clear_servers()


@pytest.mark.anyio
async def test_register_and_call_mcp_tool() -> None:
    register_server(
        "srv-1",
        {
            "url": "https://example.mcp",
            "protocol": "mcp/1.0",
            "capabilities": {"tools": True},
        },
    )
    cfg = get_server("srv-1")
    assert cfg is not None
    assert cfg["url"] == "https://example.mcp"

    result = await call_mcp_tool(cfg, "echo", {"text": "hi"})
    assert result == {"mcp_server": "https://example.mcp", "tool": "echo", "args": {"text": "hi"}}
