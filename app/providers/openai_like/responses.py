"""Provider adapter for the OpenAI Responses API."""

from __future__ import annotations

from typing import Any, AsyncIterator, Mapping, Tuple

from app.http.openai_client import OpenAICompatibleClient

from .sse import parse_sse

HeaderMap = Mapping[str, str]


def _extract_context(headers: HeaderMap | None) -> Tuple[str | None, str | None, str | None, str | None, dict[str, str]]:
    if not headers:
        return None, None, None, None, {}

    remaining = {str(k): str(v) for k, v in headers.items()}
    tenant = remaining.pop("X-Tenant-Id", None)
    correlation = remaining.pop("X-Correlation-Id", None)
    request = remaining.pop("X-Request-Id", None)
    telemetry = remaining.pop("X-Telemetry", None)
    return tenant, correlation, request, telemetry, remaining


async def create_response(
    body: dict[str, Any],
    *,
    context_headers: HeaderMap | None = None,
    client: OpenAICompatibleClient | None = None,
    max_attempts: int | None = None,
) -> dict[str, Any]:
    """Execute a synchronous Responses API call."""

    tenant, correlation, request, telemetry, extra = _extract_context(context_headers)
    transport = client or OpenAICompatibleClient()
    return await transport.post_responses(
        body,
        tenant_id=tenant,
        correlation_id=correlation,
        request_id=request,
        telemetry=telemetry,
        extra_headers=extra,
        max_attempts=max_attempts,
    )


async def stream_response(
    body: dict[str, Any],
    *,
    context_headers: HeaderMap | None = None,
    client: OpenAICompatibleClient | None = None,
    max_attempts: int | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Stream Responses API events as OpenAI-compatible dictionaries."""

    tenant, correlation, request, telemetry, extra = _extract_context(context_headers)
    transport = client or OpenAICompatibleClient()
    line_iter = transport.post_responses_stream(
        body,
        tenant_id=tenant,
        correlation_id=correlation,
        request_id=request,
        telemetry=telemetry,
        extra_headers=extra,
        max_attempts=max_attempts,
    )
    async for message in parse_sse(line_iter):
        yield message


__all__ = ["create_response", "stream_response"]
