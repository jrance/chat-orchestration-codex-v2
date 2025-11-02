import asyncio

import pytest

from app.sse.streams import SSEMessage, encode_message, message_stream


pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def test_normal_end_without_timeout():
    messages = [
        SSEMessage(event="delta", data={"step": 1}),
        SSEMessage(event="delta", data={"step": 2}),
    ]

    async def producer():
        for message in messages:
            yield message
            await asyncio.sleep(0)

    collected: list[str] = []

    async def consume():
        async for payload in message_stream(producer(), heartbeat_interval=0.5):
            collected.append(payload)

    await asyncio.wait_for(consume(), timeout=0.2)
    assert collected == [encode_message(message) for message in messages]


async def test_client_cancel_midstream():
    first = SSEMessage(event="delta", data="first")
    second = SSEMessage(event="delta", data="second")

    async def producer():
        yield first
        await asyncio.sleep(0.05)
        yield second

    stream = message_stream(producer(), heartbeat_interval=0.5)

    first_frame = await asyncio.wait_for(stream.__anext__(), timeout=0.2)
    assert first_frame == encode_message(first)

    pending = asyncio.create_task(stream.__anext__())
    await asyncio.sleep(0)

    pending.cancel()
    with pytest.raises((asyncio.CancelledError, StopAsyncIteration)):
        await asyncio.wait_for(pending, timeout=0.2)

    await asyncio.wait_for(stream.aclose(), timeout=0.2)

    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(stream.__anext__(), timeout=0.2)


async def test_heartbeat_present_and_stops_after_completion():
    heartbeat_interval = 0.05
    ready = asyncio.Event()

    first = SSEMessage(event="delta", data="one")
    second = SSEMessage(event="delta", data="two")

    async def producer():
        yield first
        await ready.wait()
        yield second

    stream = message_stream(producer(), heartbeat_interval=heartbeat_interval)

    first_frame = await asyncio.wait_for(stream.__anext__(), timeout=0.2)
    assert first_frame == encode_message(first)

    heartbeat_frame = await asyncio.wait_for(stream.__anext__(), timeout=0.2)
    assert heartbeat_frame == encode_message(SSEMessage(event="ping", data={}))

    ready.set()

    second_frame = await asyncio.wait_for(stream.__anext__(), timeout=0.2)
    assert second_frame == encode_message(second)

    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(stream.__anext__(), timeout=0.2)
