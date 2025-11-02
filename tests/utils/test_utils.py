import asyncio
import uuid

import pytest

from app.utils import format_sse, new_id, sse_stream


def test_new_id_returns_uuid():
    generated = new_id()
    assert uuid.UUID(generated)


def test_format_sse_produces_event_frame():
    frame = format_sse("payload", event="update", id="123")
    assert "event: update" in frame
    assert "data: payload" in frame
    assert "id: 123" in frame
    assert frame.endswith("\n\n")


@pytest.mark.anyio
async def test_sse_stream_encodes_messages():
    async def _gen():
        yield {"event": "ping", "data": "pong", "id": "abc"}
        yield {"data": "fallback"}

    frames = []
    async for chunk in sse_stream(_gen()):
        frames.append(chunk)

    assert frames[0].startswith("id: abc")
    assert "\nevent: ping\n" in frames[0]
    assert "data: pong" in frames[0]
    assert "id: abc" in frames[0]
    assert "event: message" in frames[1]
    assert "data: fallback" in frames[1]
