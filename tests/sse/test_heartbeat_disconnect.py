import pytest

from app.sse.streams import SSEMessage, message_stream


@pytest.mark.anyio
async def test_message_stream_handles_connection_reset():
    async def _generator():
        yield SSEMessage(event="response.created", data={"ok": True})
        raise ConnectionResetError("client disconnected")

    frames = []
    async for frame in message_stream(_generator()):
        frames.append(frame)

    assert frames
    assert frames[0].startswith("event: response.created")
