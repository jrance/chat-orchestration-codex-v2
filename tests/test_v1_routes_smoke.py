import pytest
from httpx import AsyncClient

PKG = {"meta": {"id": "x", "name": "y", "version": "1.0.0"}, "nodes": [], "edges": []}


@pytest.mark.anyio
async def test_validate_stub(async_client: AsyncClient):
    response = await async_client.post("/v1/validate", json=PKG)
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["message"] == "Invalid IR"
    assert isinstance(body["errors"], list)


@pytest.mark.anyio
async def test_compile_stub(async_client: AsyncClient):
    response = await async_client.post("/v1/compile", json=PKG)
    assert response.status_code == 501
    assert response.json()["ok"] is False


@pytest.mark.anyio
async def test_execute_endpoint(async_client: AsyncClient):
    body = {"ir": PKG, "input": "hello"}
    response = await async_client.post(
        "/v1/execute",
        json=body,
        headers={"X-Tenant-Id": "tenant-1"},
    )
    payload = response.json()
    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["runId"]
    assert payload["message"] == "completed"

@pytest.mark.anyio
async def test_resume_missing_run(async_client: AsyncClient):
    response = await async_client.post(
        "/v1/execute/run-123/resume",
        json={"input": "hi"},
        headers={"X-Tenant-Id": "tenant-1"},
    )
    assert response.status_code == 404
