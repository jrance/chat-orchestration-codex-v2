import pytest
from httpx import AsyncClient


@pytest.mark.anyio
async def test_stream_sse(async_client: AsyncClient):
    payload = {"package": {"meta": {}, "nodes": [], "edges": []}, "input": {}}
    response = await async_client.post("/v1/execute/stream", json=payload)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    body = response.text
    assert "event: response.created" in body
    assert "event: response.completed" in body
