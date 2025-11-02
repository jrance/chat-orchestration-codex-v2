import asyncio

import httpx
import pytest

from app.http.client_factory import _sync_close_async_client


def test_sync_close_async_client_without_loop():
    client = httpx.AsyncClient()
    _sync_close_async_client(client)
    assert client.is_closed


@pytest.mark.anyio
async def test_sync_close_async_client_with_running_loop():
    client = httpx.AsyncClient()
    _sync_close_async_client(client)
    await asyncio.sleep(0)
    assert client.is_closed
