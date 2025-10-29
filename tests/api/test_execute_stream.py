import asyncio
import json
from urllib.parse import parse_qs, urlparse

import pytest
from httpx import AsyncClient

from app.telemetry.models import TelemetryLevel


def _sample_ir(tenant: str = "tenant-1") -> dict:
    return {
        "meta": {"id": "pkg-1", "name": "Test", "version": "1.0.0", "tenantId": tenant},
        "nodes": [],
        "edges": [],
    }


def _assert_event(sequence: list[str], event_name: str) -> bool:
    return any(f"event: {event_name}" in chunk for chunk in sequence)


@pytest.mark.anyio
async def test_execute_requires_tenant(async_client: AsyncClient):
    response = await async_client.post("/v1/execute", json={"ir": _sample_ir(), "input": "hi"})
    assert response.status_code == 400
    assert "X-Tenant-Id" in response.text


@pytest.mark.anyio
async def test_execute_returns_result(async_client: AsyncClient):
    body = {"ir": _sample_ir(), "input": "hello world"}
    response = await async_client.post(
        "/v1/execute",
        json=body,
        headers={"X-Tenant-Id": "tenant-1"},
    )

    data = response.json()
    assert response.status_code == 200
    assert data["ok"] is True
    assert data["runId"]
    assert data["output_text"].startswith("Echo:")
    assert data["usage"]["output_tokens"] >= 1


@pytest.mark.anyio
async def test_stream_emits_response_events(async_client: AsyncClient):
    body = {"ir": _sample_ir(), "input": "stream me"}
    response = await async_client.post(
        "/v1/execute/stream",
        json=body,
        headers={"X-Tenant-Id": "tenant-1"},
    )

    payload = (await response.aread()).decode()
    frames = [frame for frame in payload.split("\n\n") if frame.strip()]

    assert _assert_event(frames, "response.created")
    assert _assert_event(frames, "response.output_text.delta")
    assert _assert_event(frames, "response.completed")


@pytest.mark.anyio
async def test_tenant_mismatch_rejected(async_client: AsyncClient):
    body = {"ir": _sample_ir(tenant="other-tenant"), "input": "hi"}
    response = await async_client.post(
        "/v1/execute",
        json=body,
        headers={"X-Tenant-Id": "tenant-1"},
    )
    assert response.status_code == 400
    assert "Tenant mismatch" in response.text


@pytest.mark.anyio
async def test_stream_returns_telemetry_header(async_client: AsyncClient):
    body = {"ir": _sample_ir(), "input": "telemetry please"}
    response = await async_client.post(
        "/v1/execute/stream",
        json=body,
        headers={"X-Tenant-Id": "tenant-1", "X-Telemetry": TelemetryLevel.BASIC.value},
    )

    assert response.status_code == 200
    telemetry_header = response.headers.get("X-Telemetry-Stream-Url")
    assert telemetry_header

    payload = (await response.aread()).decode()
    frames = [frame for frame in payload.split("\n\n") if frame.strip()]
    assert _assert_event(frames, "response.completed")

    run_id = parse_qs(urlparse(telemetry_header).query).get("runId", [""])[0]
    telemetry_response = await async_client.get(
        f"/v1/telemetry/stream?runId={run_id}",
        headers={"X-Tenant-Id": "tenant-1", "X-Telemetry": TelemetryLevel.BASIC.value},
    )
    telemetry_payload = (await telemetry_response.aread()).decode()
    assert "event: telemetry.run_started" in telemetry_payload
    assert "event: telemetry.run_completed" in telemetry_payload
