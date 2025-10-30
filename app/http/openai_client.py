"""Async HTTP client for OpenAI-compatible calls via Apigee."""

from __future__ import annotations

import asyncio
import random
from typing import Any, AsyncIterator, Optional

import httpx

from app.config.settings import settings
from app.http.headers import build_default_headers
from app.http.token_provider import ApigeeTokenProvider

RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

_client: httpx.AsyncClient | None = None


def _make_async_client(transport: httpx.BaseTransport | None = None) -> httpx.AsyncClient:
    limits = httpx.Limits(
        max_connections=settings.http_pool_max_connections,
        max_keepalive_connections=settings.http_pool_max_keepalive,
    )
    timeout = httpx.Timeout(
        connect=settings.http_connect_timeout,
        read=settings.http_read_timeout,
        write=settings.http_write_timeout,
        pool=settings.http_read_timeout,
    )
    return httpx.AsyncClient(
        base_url=settings.openai_base_url or "",
        timeout=timeout,
        limits=limits,
        transport=transport,
        follow_redirects=True,
        headers={"Accept": "application/json"},
    )


def get_client(transport: httpx.BaseTransport | None = None) -> httpx.AsyncClient:
    """Return the shared AsyncClient or create one with a custom transport."""
    global _client
    if transport is not None:
        return _make_async_client(transport=transport)
    if _client is None:
        _client = _make_async_client()
    return _client


async def _retry_delay(attempt: int) -> float:
    base = settings.http_retry_base_delay
    jitter = random.uniform(0, base)
    return base * (2 ** (attempt - 1)) + jitter


class OpenAICompatibleClient:
    """High-level client that injects auth, headers, and retry behavior."""

    def __init__(
        self,
        token_provider: ApigeeTokenProvider | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.token_provider = token_provider or ApigeeTokenProvider(transport=transport)
        self._transport = transport
        self._client: httpx.AsyncClient | None = None

    async def aclose(self) -> None:
        """Close any per-instance client created for custom transports."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _auth_headers(self) -> dict[str, str]:
        token = await self.token_provider.get_token()
        return {"Authorization": f"Bearer {token}"}

    def _get_client(self) -> httpx.AsyncClient:
        if self._transport is not None:
            if self._client is None:
                self._client = _make_async_client(transport=self._transport)
            return self._client
        return get_client()

    async def _prepare_headers(
        self,
        tenant_id: Optional[str],
        correlation_id: Optional[str],
        request_id: Optional[str],
        telemetry: Optional[str],
        extra_headers: dict[str, str] | None,
    ) -> dict[str, str]:
        headers = build_default_headers(tenant_id, correlation_id, request_id, telemetry)
        headers.update(await self._auth_headers())
        if extra_headers:
            headers.update(extra_headers)
        return headers

    async def request(
        self,
        method: str,
        path: str,
        json_body: dict[str, Any] | None = None,
        tenant_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        request_id: Optional[str] = None,
        telemetry: Optional[str] = None,
        extra_headers: dict[str, str] | None = None,
        max_attempts: Optional[int] = None,
    ) -> httpx.Response:
        """Send an HTTP request with retry/backoff for transient failures."""
        if not settings.openai_base_url:
            raise RuntimeError("OPENAI_BASE_URL not configured.")

        attempts = max(1, int(max_attempts or settings.http_retry_max_attempts))
        client = self._get_client()
        last_error: Exception | None = None

        for attempt in range(1, attempts + 1):
            try:
                headers = await self._prepare_headers(
                    tenant_id,
                    correlation_id,
                    request_id,
                    telemetry,
                    extra_headers,
                )
                response = await client.request(method, path, json=json_body, headers=headers)
                if response.status_code in RETRYABLE_STATUS_CODES and attempt < attempts:
                    await asyncio.sleep(await _retry_delay(attempt))
                    continue

                response.raise_for_status()
                return response
            except httpx.HTTPStatusError as exc:
                last_error = exc
                status_code = exc.response.status_code
                if status_code in RETRYABLE_STATUS_CODES and attempt < attempts:
                    await asyncio.sleep(await _retry_delay(attempt))
                    continue
                raise
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt >= attempts:
                    raise
                await asyncio.sleep(await _retry_delay(attempt))

        if last_error is not None:
            raise last_error
        raise RuntimeError("request failed without raising an exception")  # pragma: no cover

    async def post_responses(self, body: dict[str, Any], **headers: Any) -> dict[str, Any]:
        """Convenience helper for POST /responses."""
        response = await self.request("POST", "/v1/responses", json_body=body, **headers)
        return response.json()

    async def post_responses_stream(
        self,
        body: dict[str, Any],
        *,
        tenant_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        request_id: Optional[str] = None,
        telemetry: Optional[str] = None,
        extra_headers: dict[str, str] | None = None,
        max_attempts: Optional[int] = None,
    ) -> AsyncIterator[str]:
        """Stream the Responses endpoint as an async iterator of SSE lines."""
        if not settings.openai_base_url:
            raise RuntimeError("OPENAI_BASE_URL not configured.")

        attempts = max(1, int(max_attempts or settings.http_retry_max_attempts))
        client = self._get_client()
        last_error: Exception | None = None

        for attempt in range(1, attempts + 1):
            try:
                headers = await self._prepare_headers(
                    tenant_id,
                    correlation_id,
                    request_id,
                    telemetry,
                    extra_headers,
                )
                async with client.stream(
                    "POST",
                    "/v1/responses",
                    json=body,
                    headers=headers,
                ) as response:
                    if response.status_code in RETRYABLE_STATUS_CODES and attempt < attempts:
                        await asyncio.sleep(await _retry_delay(attempt))
                        continue

                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        yield line
                    return
            except httpx.HTTPStatusError as exc:
                last_error = exc
                status_code = exc.response.status_code
                if status_code in RETRYABLE_STATUS_CODES and attempt < attempts:
                    await asyncio.sleep(await _retry_delay(attempt))
                    continue
                raise
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt >= attempts:
                    raise
                await asyncio.sleep(await _retry_delay(attempt))

        if last_error is not None:
            raise last_error
        raise RuntimeError("streaming request failed without raising an exception")  # pragma: no cover
