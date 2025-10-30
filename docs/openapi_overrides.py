"""Helpers to enrich the generated OpenAPI document for execute endpoints."""

from __future__ import annotations

from typing import Dict, Iterable, List

from fastapi import FastAPI

from app.api.schemas import ErrorEnvelope, RunRequest, RunStatus, ResumeRequest


def _schema_ref(model) -> Dict[str, str]:
    return {"$ref": f"#/components/schemas/{model.__name__}"}


def _ensure_parameters(operation: Dict[str, any], parameter_refs: Iterable[str]) -> None:
    params: List[Dict[str, str]] = list(operation.get("parameters") or [])
    existing_refs = {param.get("$ref") for param in params if isinstance(param, dict)}
    for ref in parameter_refs:
        if ref not in existing_refs:
            params.append({"$ref": ref})
    if params:
        operation["parameters"] = params


def apply_openapi_overrides(app: FastAPI) -> None:
    """Patch FastAPI's OpenAPI schema to expose shared headers and examples."""

    original_openapi = app.openapi

    def custom_openapi() -> Dict[str, any]:
        if app.openapi_schema:
            return app.openapi_schema

        schema = original_openapi()
        components = schema.setdefault("components", {})
        schemas = components.setdefault("schemas", {})

        # Ensure models appear even when FastAPI prunes unused aliases.
        schemas.setdefault("RunRequest", RunRequest.model_json_schema(ref_template="#/components/schemas/{model}"))
        schemas.setdefault("ResumeRequest", ResumeRequest.model_json_schema(ref_template="#/components/schemas/{model}"))
        schemas.setdefault("RunStatus", RunStatus.model_json_schema(ref_template="#/components/schemas/{model}"))
        schemas.setdefault("ErrorEnvelope", ErrorEnvelope.model_json_schema(ref_template="#/components/schemas/{model}"))

        parameters = components.setdefault("parameters", {})
        parameters["AuthorizationHeader"] = {
            "name": "Authorization",
            "in": "header",
            "required": False,
            "schema": {"type": "string"},
            "description": "Bearer token propagated from the Apigee gateway.",
        }
        parameters["XRequestIDHeader"] = {
            "name": "X-Request-ID",
            "in": "header",
            "required": False,
            "schema": {"type": "string"},
            "description": "Caller-supplied request identifier echoed in logs.",
        }
        parameters["XCorrelationIDHeader"] = {
            "name": "X-Correlation-ID",
            "in": "header",
            "required": False,
            "schema": {"type": "string"},
            "description": "Correlation identifier that links retries and tool calls.",
        }
        parameters["XTenantIDHeader"] = {
            "name": "X-Tenant-ID",
            "in": "header",
            "required": True,
            "schema": {"type": "string"},
            "description": "Tenant identifier used for routing to the correct organization context.",
        }
        parameters["XTelemetryHeader"] = {
            "name": "X-Telemetry",
            "in": "header",
            "required": False,
            "schema": {"type": "string", "enum": ["none", "basic", "verbose"]},
            "description": "Controls verbosity of telemetry streaming.",
        }
        parameters["XTimestampHeader"] = {
            "name": "X-Timestamp",
            "in": "header",
            "required": False,
            "schema": {"type": "string", "format": "date-time"},
            "description": "RFC3339 timestamp supplied by the client.",
        }

        shared_parameter_refs = [
            "#/components/parameters/AuthorizationHeader",
            "#/components/parameters/XRequestIDHeader",
            "#/components/parameters/XCorrelationIDHeader",
            "#/components/parameters/XTenantIDHeader",
            "#/components/parameters/XTelemetryHeader",
            "#/components/parameters/XTimestampHeader",
        ]

        paths = schema.get("paths", {})
        for path, methods in paths.items():
            if not path.startswith("/v1/execute"):
                continue
            if not isinstance(methods, dict):
                continue
            for http_method, operation in methods.items():
                if not isinstance(operation, dict):
                    continue
                method_lower = str(http_method).lower()
                if method_lower not in {"get", "post"}:
                    continue
                _ensure_parameters(operation, shared_parameter_refs)

        app.openapi_schema = schema
        return schema

    app.openapi = custom_openapi


__all__ = ["apply_openapi_overrides"]
