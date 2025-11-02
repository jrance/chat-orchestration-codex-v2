import anyio
import pytest
from collections.abc import AsyncIterator

from app.sse.streams import SSEMessage, message_stream


pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def _single_response() -> AsyncIterator[SSEMessage]:
    yield SSEMessage(event="response.created", data={})
    await anyio.sleep(0.12)
    yield SSEMessage(event="response.completed", data={"ok": True})


async def test_heartbeat_quiet_shutdown():
    frames: list[str] = []

    with anyio.fail_after(2):
        async for frame in message_stream(
            _single_response(),
            heartbeat_interval=0.05,
        ):
            frames.append(frame)
            if "event: response.completed" in frame:
                break

    assert any("event: ping" in frame for frame in frames)
    assert any("event: response.completed" in frame for frame in frames)
    assert frames[-1].startswith("event: response.completed")
