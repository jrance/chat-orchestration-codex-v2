"""Tests for CORS preflight handling on streaming endpoints."""

import pytest
from httpx import AsyncClient


@pytest.mark.anyio
async def test_preflight_execute_stream(async_client: AsyncClient):
    """Test OPTIONS preflight request to /v1/execute/stream returns proper CORS headers."""
    headers = {
        "Origin": "http://localhost:5173",
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "authorization,x-telemetry,x-tenant-id,x-request-id,x-correlation-id",
    }
    
    response = await async_client.options("/v1/execute/stream", headers=headers)
    
    # Should return 200 or 204
    assert response.status_code in (200, 204), f"Expected 200 or 204, got {response.status_code}"
    
    # Check for required CORS headers (case-insensitive)
    header_keys_lower = {k.lower() for k in response.headers.keys()}
    
    assert "access-control-allow-origin" in header_keys_lower, "Missing Access-Control-Allow-Origin header"
    assert "access-control-allow-methods" in header_keys_lower, "Missing Access-Control-Allow-Methods header"
    assert "access-control-allow-headers" in header_keys_lower, "Missing Access-Control-Allow-Headers header"
    
    # Verify origin is allowed
    allow_origin = response.headers.get("access-control-allow-origin")
    assert allow_origin in ("http://localhost:5173", "*"), f"Origin not properly allowed: {allow_origin}"


@pytest.mark.anyio
async def test_preflight_with_credentials(async_client: AsyncClient):
    """Test that CORS allows credentials when configured."""
    headers = {
        "Origin": "http://localhost:3000",
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "authorization,content-type",
    }
    
    response = await async_client.options("/v1/execute/stream", headers=headers)
    
    assert response.status_code in (200, 204)
    
    # Check if credentials are allowed (when CORS_ALLOW_CREDENTIALS=true)
    header_keys_lower = {k.lower() for k in response.headers.keys()}
    if "access-control-allow-credentials" in header_keys_lower:
        credentials_value = response.headers.get("access-control-allow-credentials")
        assert credentials_value.lower() == "true", "Credentials should be allowed"


@pytest.mark.anyio
async def test_preflight_custom_headers(async_client: AsyncClient):
    """Test that custom headers used by the engine are properly allowed in preflight."""
    custom_headers = [
        "authorization",
        "x-tenant-id",
        "x-request-id",
        "x-correlation-id",
        "x-telemetry",
        "x-run-id",
    ]
    
    headers = {
        "Origin": "http://localhost:5173",
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": ",".join(custom_headers),
    }
    
    response = await async_client.options("/v1/execute/stream", headers=headers)
    
    assert response.status_code in (200, 204)
    
    # Verify allowed headers includes our custom headers or uses wildcard
    allowed_headers = response.headers.get("access-control-allow-headers", "")
    assert allowed_headers == "*" or all(
        header in allowed_headers.lower() for header in custom_headers
    ), f"Not all custom headers are allowed. Allowed: {allowed_headers}"


@pytest.mark.anyio
async def test_execute_stream_post_still_works(async_client: AsyncClient, stub_llm):
    """Verify that POST to /v1/execute/stream endpoint exists and is accessible."""
    # This is a simple smoke test to ensure the OPTIONS handler doesn't interfere
    # The actual execute stream functionality is tested in test_execute_stream.py
    
    # Just test with a minimal payload to see if the endpoint responds
    # We expect a 400 if payload is invalid, but not a 404 or 405
    response = await async_client.post(
        "/v1/execute/stream",
        json={},
        headers={"X-Tenant-ID": "test-tenant"},
    )
    
    # Should not be 404 (not found) or 405 (method not allowed)
    # May be 400 (bad request) due to invalid payload, which is fine
    assert response.status_code != 404, "Endpoint should exist"
    assert response.status_code != 405, "POST method should be allowed"
    
    # Verify CORS headers are present (since we're sending Origin header)
    response_with_origin = await async_client.post(
        "/v1/execute/stream",
        json={},
        headers={"Origin": "http://localhost:5173", "X-Tenant-ID": "test-tenant"},
    )
    
    header_keys_lower = {k.lower() for k in response_with_origin.headers.keys()}
    assert "access-control-allow-origin" in header_keys_lower, "CORS headers should be present"


@pytest.mark.anyio
async def test_preflight_different_origins(async_client: AsyncClient):
    """Test preflight works with different allowed origins."""
    test_origins = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:8080",
    ]
    
    for origin in test_origins:
        headers = {
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization",
        }
        
        response = await async_client.options("/v1/execute/stream", headers=headers)
        
        assert response.status_code in (200, 204), f"Failed for origin {origin}"
        
        # Either the specific origin is allowed or wildcard is used
        allow_origin = response.headers.get("access-control-allow-origin")
        assert allow_origin in (origin, "*"), f"Origin {origin} not allowed. Got: {allow_origin}"
