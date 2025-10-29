import pytest
from httpx import AsyncClient

PKG = {"meta": {"id": "x", "name": "y", "version": "1.0.0"}, "nodes": [], "edges": []}


@pytest.mark.anyio
async def test_validate_stub(async_client: AsyncClient):
    response = await async_client.post("/v1/validate", json=PKG)
    assert response.status_code == 501
    assert response.json()["ok"] is False


@pytest.mark.anyio
async def test_compile_stub(async_client: AsyncClient):
    response = await async_client.post("/v1/compile", json=PKG)
    assert response.status_code == 501
    assert response.json()["ok"] is False


@pytest.mark.anyio
async def test_execute_stub(async_client: AsyncClient):
    response = await async_client.post("/v1/execute", json={"package": PKG, "input": {}})
    assert response.status_code == 501
    assert response.json()["ok"] is False


@pytest.mark.anyio
async def test_resume_stub(async_client: AsyncClient):
    response = await async_client.get("/v1/execute/run-123/resume")
    body = response.json()
    assert response.status_code == 501
    assert body["ok"] is False
    assert body["runId"] == "run-123"
