import json

import pytest


@pytest.mark.anyio
async def test_health_request_generates_json_log(caplog, async_client) -> None:
    caplog.set_level("INFO")
    caplog.clear()

    response = await async_client.get(
        "/health",
        headers={"X-Tenant-Id": "qa-tenant"},
    )

    assert response.status_code == 200

    parsed = None
    for record in reversed(caplog.records):
        if isinstance(record.msg, dict):
            payload = record.msg
        else:
            try:
                payload = json.loads(record.message)
            except json.JSONDecodeError:
                continue
        if payload.get("event") == "healthcheck":
            parsed = payload
            break

    assert parsed is not None, "Expected a structured log entry for /health"
    assert parsed.get("status") == "ok"
    assert parsed.get("env") == response.json()["env"]
    assert parsed.get("correlation_id")
    assert parsed.get("request_id")
    assert parsed.get("tenant_id") == "qa-tenant"
    assert "timestamp" in parsed
