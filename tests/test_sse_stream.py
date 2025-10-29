import pytest
from httpx import AsyncClient


@pytest.mark.anyio
async def test_stream_sse(async_client: AsyncClient):
    payload = {"ir": {"meta": {"id": "x", "name": "y", "version": "1.0.0", "tenantId": "tenant-1"}, "nodes": [], "edges": []}, "input": "stream"}
    response = await async_client.post(
        "/v1/execute/stream",
        json=payload,
        headers={"X-Tenant-Id": "tenant-1"},
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    body = (await response.aread()).decode()
    assert "event: response.created" in body
    assert "event: response.completed" in body
