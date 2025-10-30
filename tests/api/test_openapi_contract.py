import pytest
from httpx import AsyncClient


@pytest.mark.anyio
async def test_openapi_execute_contract(async_client: AsyncClient):
    response = await async_client.get("/openapi.json")
    assert response.status_code == 200
    spec = response.json()

    components = spec.get("components", {})
    schemas = components.get("schemas", {})
    assert "RunRequest" in schemas
    assert "ResumeRequest" in schemas
    assert "RunStatus" in schemas
    assert "ErrorEnvelope" in schemas

    execute_post = spec["paths"]["/v1/execute"]["post"]
    request_schema = execute_post["requestBody"]["content"]["application/json"]["schema"]["$ref"]
    assert request_schema.endswith("/RunRequest")
    response_schema = execute_post["responses"]["201"]["content"]["application/json"]["schema"]["$ref"]
    assert response_schema.endswith("/RunStatus")

    resume_post = spec["paths"]["/v1/execute/{runId}/resume"]["post"]
    resume_request = resume_post["requestBody"]["content"]["application/json"]["schema"]["$ref"]
    assert resume_request.endswith("/ResumeRequest")
    resume_response = resume_post["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
    assert resume_response.endswith("/RunStatus")

    expected_headers = {
        "#/components/parameters/AuthorizationHeader",
        "#/components/parameters/XRequestIDHeader",
        "#/components/parameters/XCorrelationIDHeader",
        "#/components/parameters/XTenantIDHeader",
        "#/components/parameters/XTelemetryHeader",
        "#/components/parameters/XTimestampHeader",
    }
    execute_params = {param["$ref"] for param in execute_post.get("parameters", []) if "$ref" in param}
    resume_params = {param["$ref"] for param in resume_post.get("parameters", []) if "$ref" in param}
    assert expected_headers.issubset(execute_params)
    assert expected_headers.issubset(resume_params)
