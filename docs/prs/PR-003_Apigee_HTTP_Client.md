# PROMPT FOR CODEX

Implement the following PR and validate that everything has been completed fully and that all tests pass and all acceptance criteria is met. Feel free to modify the recommended code if there is a better and cleaner approach.

---

# PR-03 of 14 — Apigee-Aware HTTP Client (OpenAI-Compatible Gateway)

## PR Title
Add Apigee OAuth token provider and OpenAI-compatible async HTTP client (PR 3 of 14)

## Description
Introduce an **async, pooled, retriable** HTTP client for calling an **OpenAI-compatible Responses API** that sits behind an **Apigee** gateway. The client:
- Obtains OAuth access tokens from Apigee (client-credentials by default), **caches** them with TTL and skew, and refreshes automatically.
- Injects mandatory default headers on every request (Authorization, content negotiation, Apigee client id, tenant id, correlation/request/timestamp, and optional extra headers from config).
- Allows per-request headers (e.g., telemetry level) to be merged safely.
- Uses `httpx.AsyncClient` with tuned limits/timeouts and **exponential backoff** retries for 429/5xx.
- Exposes convenience methods for POSTing to the OpenAI-compatible gateway (e.g., `/responses`), and a generic `request()` for extensibility.

This PR focuses on the **transport**; we will wire orchestration and streaming mappings in later PRs.

## Purpose
- Centralize outbound HTTP concerns (auth, headers, retries, timeouts, connection pooling).
- Make it trivial to call the OpenAI-compatible endpoint with the correct security and tenancy envelope.

## Scope
- New modules: token provider, header policy, async client factory/wrapper.
- Settings for Apigee + OpenAI base URL and retry/timeout knobs.
- Tests with `httpx.MockTransport` (no external deps) to validate token caching, header injection, and retry behavior.
- Basic docs updates.

## Goals
- Reliable token acquisition + caching with refresh before expiry.
- Default headers applied consistently; per-request correlation/request/timestamp appended.
- Bounded retries with jitter; respect timeouts.
- All tests pass with ≥80% coverage for code in this PR.

---

## Files to Add / Change

```
app/
├─ http/
│  ├─ __init__.py
│  ├─ token_provider.py
│  ├─ headers.py
│  └─ openai_client.py
├─ config/settings.py         # (extend with Apigee/OpenAI & retry/timeout settings)
├─ api/
│  └─ v1/
│     └─ routes_execute.py   # (light touch: demonstrate a non-stream call placeholder)
tests/
├─ test_token_provider.py
├─ test_headers.py
└─ test_openai_client.py
.env.example                  # (append Apigee/OpenAI envs)
README.md                     # (append quick usage/config)
pyproject.toml                # (no new runtime deps; tests use stdlib only)
```

### `.env.example` (append)
```
# Apigee / OpenAI-compatible gateway
APIGEE_TOKEN_URL=https://gateway.example.com/oauth2/token
APIGEE_CLIENT_ID=replace-me
APIGEE_CLIENT_SECRET=replace-me
APIGEE_SCOPES=openid offline_access

# Base URL for the OpenAI-compatible endpoint proxied by Apigee
OPENAI_BASE_URL=https://gateway.example.com/openai

# Optional extra headers as JSON (merged into every outbound request)
APIGEE_EXTRA_HEADERS_JSON={"X-Channel":"orchestrator","X-App":"codeless-orch"}

# HTTP client tuning
HTTP_CONNECT_TIMEOUT=5.0
HTTP_READ_TIMEOUT=60.0
HTTP_WRITE_TIMEOUT=60.0
HTTP_POOL_MAX_CONNECTIONS=100
HTTP_POOL_MAX_KEEPALIVE=20
HTTP_RETRY_MAX_ATTEMPTS=3
HTTP_RETRY_BASE_DELAY=0.2  # seconds; exponential backoff with jitter
```

### `app/config/settings.py` (extend)
```python
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional
import json

class Settings(BaseSettings):
    app_name: str = "codeless-orch"
    app_env: str = "dev"
    app_host: str = "127.0.0.1"
    app_port: int = 8000

    default_tenant_id: str = "demo-tenant"

    # Logging (from PR-02)
    log_level: str = "INFO"
    log_redaction_enabled: bool = False

    # Apigee / OpenAI-compatible
    apigee_token_url: str | None = None
    apigee_client_id: str | None = None
    apigee_client_secret: str | None = None
    apigee_scopes: str = "openid"
    openai_base_url: str | None = None
    apigee_extra_headers_json: str | None = None

    # HTTP client tuning
    http_connect_timeout: float = 5.0
    http_read_timeout: float = 60.0
    http_write_timeout: float = 60.0
    http_pool_max_connections: int = 100
    http_pool_max_keepalive: int = 20
    http_retry_max_attempts: int = 3
    http_retry_base_delay: float = 0.2  # seconds

    model_config = SettingsConfigDict(env_file=".env", env_prefix="", extra="ignore")

    def apigee_extra_headers(self) -> dict:
        if not self.apigee_extra_headers_json:
            return {}
        try:
            return json.loads(self.apigee_extra_headers_json)
        except Exception:
            return {}

settings = Settings()
```

### `app/http/token_provider.py`
```python
from __future__ import annotations
import time
import json
import httpx
from typing import Optional, Tuple
from app.config.settings import settings

class TokenCache:
    __slots__ = ("access_token", "expires_at")
    def __init__(self) -> None:
        self.access_token: Optional[str] = None
        self.expires_at: float = 0.0

    def valid(self) -> bool:
        # Refresh 60s before expiry as safety skew
        return bool(self.access_token) and (time.time() + 60) < self.expires_at

    def set(self, token: str, expires_in: int) -> None:
        self.access_token = token
        self.expires_at = time.time() + max(60, int(expires_in))

class ApigeeTokenProvider:
    """
    Client-credentials token provider (default). Can be extended later for OBO.
    """
    def __init__(self, transport: httpx.BaseTransport | None = None) -> None:
        self._cache = TokenCache()
        self._transport = transport

    async def get_token(self, force_refresh: bool = False) -> str:
        if (not force_refresh) and self._cache.valid():
            return self._cache.access_token  # type: ignore[return-value]

        if not settings.apigee_token_url or not settings.apigee_client_id or not settings.apigee_client_secret:
            raise RuntimeError("Apigee token configuration missing (url/client_id/client_secret).")

        form = {
            "grant_type": "client_credentials",
            "client_id": settings.apigee_client_id,
            "client_secret": settings.apigee_client_secret,
        }
        scopes = (settings.apigee_scopes or "").strip()
        if scopes:
            form["scope"] = scopes

        async with httpx.AsyncClient(transport=self._transport, timeout=15) as ac:
            resp = await ac.post(settings.apigee_token_url, data=form, headers={"Accept":"application/json"})
            resp.raise_for_status()
            payload = resp.json()
            token = payload.get("access_token")
            expires_in = int(payload.get("expires_in") or 300)
            if not token:
                raise RuntimeError("Apigee token endpoint did not return access_token")
            self._cache.set(token, expires_in)
            return token
```

### `app/http/headers.py`
```python
from __future__ import annotations
import time
from typing import Mapping, MutableMapping, Optional
from app.config.settings import settings

# Normalize to lower-case for lookups; send canonical header names on output
H_CORR = "X-Correlation-Id"
H_REQ = "X-Request-Id"
H_TS = "X-Timestamp"
H_TENANT = "X-Tenant-Id"
H_CLIENT = "X-Client-Id"
H_TELEMETRY = "X-Telemetry"

def build_default_headers(
    tenant_id: Optional[str],
    correlation_id: Optional[str],
    request_id: Optional[str],
    telemetry: Optional[str] = None,
) -> dict:
    headers: dict[str, str] = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        H_TS: time.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    if correlation_id:
        headers[H_CORR] = correlation_id
    if request_id:
        headers[H_REQ] = request_id
    if tenant_id:
        headers[H_TENANT] = tenant_id
    if settings.apigee_client_id:
        headers[H_CLIENT] = settings.apigee_client_id
    if telemetry:
        headers[H_TELEMETRY] = telemetry
    # Merge extra headers from config (if any), without clobbering explicitly set ones
    for k, v in settings.apigee_extra_headers().items():
        headers.setdefault(k, str(v))
    return headers
```

### `app/http/openai_client.py`
```python
from __future__ import annotations
import asyncio
import json
import random
from typing import Any, AsyncIterator, Optional
import httpx
from app.config.settings import settings
from app.http.token_provider import ApigeeTokenProvider
from app.http.headers import build_default_headers

# Module-level singleton client
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
    global _client
    if _client is None:
        _client = _make_async_client(transport=transport)
    return _client

async def _retry_delay(attempt: int) -> float:
    base = settings.http_retry_base_delay
    # exponential backoff with jitter
    return (base * (2 ** (attempt - 1))) + random.uniform(0, base)

class OpenAICompatibleClient:
    def __init__(self, token_provider: ApigeeTokenProvider | None = None, transport: httpx.BaseTransport | None = None) -> None:
        self.token_provider = token_provider or ApigeeTokenProvider(transport=transport)
        self._transport = transport

    async def _auth_headers(self) -> dict[str, str]:
        token = await self.token_provider.get_token()
        return {"Authorization": f"Bearer {token}"}

    async def request(
        self,
        method: str,
        path: str,
        json_body: dict[str, Any] | None = None,
        tenant_id: str | None = None,
        correlation_id: str | None = None,
        request_id: str | None = None,
        telemetry: str | None = None,
        extra_headers: dict[str, str] | None = None,
        max_attempts: int | None = None,
    ) -> httpx.Response:
        if not settings.openai_base_url:
            raise RuntimeError("OPENAI_BASE_URL not configured")

        attempts = max(1, int(max_attempts or settings.http_retry_max_attempts))
        client = get_client(transport=self._transport)

        last_exc: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                headers = build_default_headers(tenant_id, correlation_id, request_id, telemetry)
                headers.update(await self._auth_headers())
                if extra_headers:
                    headers.update(extra_headers)

                resp = await client.request(method=method, url=path, json=json_body, headers=headers)
                if resp.status_code in (429, 500, 502, 503, 504) and attempt < attempts:
                    await asyncio.sleep(await _retry_delay(attempt))
                    continue
                resp.raise_for_status()
                return resp
            except httpx.HTTPError as exc:
                last_exc = exc
                if attempt >= attempts:
                    raise
                await asyncio.sleep(await _retry_delay(attempt))
        # Should not reach here
        if last_exc:
            raise last_exc  # pragma: no cover
        raise RuntimeError("request failed without exception")  # pragma: no cover

    # Convenience for Responses API (non-stream). Streaming handled in PR-09.
    async def post_responses(self, body: dict[str, Any], **hdrs) -> dict[str, Any]:
        resp = await self.request("POST", "/responses", json_body=body, **hdrs)
        return resp.json()
```

### (optional) touch in `app/api/v1/routes_execute.py` to show how it will be used later
```python
# NOTE: This is illustrative only; full execution wiring lands in PR-08/PR-09.
# from app.http.openai_client import OpenAICompatibleClient
# client = OpenAICompatibleClient()
# await client.post_responses(body={"model":"gemini-2.5-flash","input":[...]}, tenant_id="...", correlation_id="...", request_id="...")
```

### `tests/test_token_provider.py`
```python
import json
import pytest
import httpx
from app.http.token_provider import ApigeeTokenProvider
from app.config.settings import settings

class _MockTokenTransport(httpx.MockTransport):
    def __init__(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url == httpx.URL(settings.apigee_token_url):
                body = {"access_token":"abc123","expires_in":120}
                return httpx.Response(200, json=body)
            return httpx.Response(404)
        super().__init__(handler)

@pytest.mark.anyio
async def test_token_caches_and_reuses(monkeypatch):
    monkeypatch.setenv("APIGEE_TOKEN_URL", "https://gw/token")
    monkeypatch.setenv("APIGEE_CLIENT_ID", "id")
    monkeypatch.setenv("APIGEE_CLIENT_SECRET", "secret")

    prov = ApigeeTokenProvider(transport=_MockTokenTransport())
    t1 = await prov.get_token()
    t2 = await prov.get_token()
    assert t1 == "abc123"
    assert t2 == "abc123"  # cached
```

### `tests/test_headers.py`
```python
import pytest
from app.http.headers import build_default_headers

def test_build_default_headers_sets_core_fields(monkeypatch):
    monkeypatch.setenv("APIGEE_CLIENT_ID", "client-123")
    from app.config.settings import settings as s  # reload uses env
    # NOTE: in this simple setup, module import happens once per session; we keep assertion generic.
    h = build_default_headers("tenant-x", "corr-y", "req-z", "verbose")
    assert h["X-Tenant-Id"] == "tenant-x"
    assert h["X-Correlation-Id"] == "corr-y"
    assert h["X-Request-Id"] == "req-z"
    assert h["X-Client-Id"] == "client-123"
    assert h["X-Telemetry"] == "verbose"
    assert "X-Timestamp" in h
    assert h["Accept"] == "application/json"
    assert h["Content-Type"] == "application/json"
```

### `tests/test_openai_client.py`
```python
import json
import pytest
import httpx
from app.http.openai_client import OpenAICompatibleClient, get_client
from app.config.settings import settings

class _MockGatewayTransport(httpx.MockTransport):
    def __init__(self, status_sequence=None):
        status_sequence = status_sequence or [200]
        self._calls = 0
        def handler(request: httpx.Request) -> httpx.Response:
            # Token endpoint
            if request.url == httpx.URL(settings.apigee_token_url):
                return httpx.Response(200, json={"access_token":"abc123","expires_in":300})
            # OpenAI-compatible endpoint
            if str(request.url).startswith(settings.openai_base_url):
                st = status_sequence[min(self._calls, len(status_sequence)-1)]
                self._calls += 1
                if st != 200:
                    return httpx.Response(st, json={"error":"retry"})
                # assert headers present
                assert request.headers.get("Authorization") == "Bearer abc123"
                assert request.headers.get("X-Correlation-Id")
                assert request.headers.get("X-Request-Id")
                return httpx.Response(200, json={"ok": True})
            return httpx.Response(404)
        super().__init__(handler)

@pytest.mark.anyio
async def test_post_responses_success(monkeypatch):
    monkeypatch.setenv("APIGEE_TOKEN_URL", "https://gw/token")
    monkeypatch.setenv("APIGEE_CLIENT_ID", "id")
    monkeypatch.setenv("APIGEE_CLIENT_SECRET", "secret")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://gw/openai")

    transport = _MockGatewayTransport()
    client = OpenAICompatibleClient(transport=transport)
    result = await client.post_responses({"model":"x","input":[]},
        tenant_id="t", correlation_id="c", request_id="r", telemetry="basic")
    assert result["ok"] is True

@pytest.mark.anyio
async def test_retries_on_429_then_200(monkeypatch):
    monkeypatch.setenv("APIGEE_TOKEN_URL", "https://gw/token")
    monkeypatch.setenv("APIGEE_CLIENT_ID", "id")
    monkeypatch.setenv("APIGEE_CLIENT_SECRET", "secret")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://gw/openai")
    monkeypatch.setenv("HTTP_RETRY_MAX_ATTEMPTS", "3")
    monkeypatch.setenv("HTTP_RETRY_BASE_DELAY", "0.01")

    transport = _MockGatewayTransport(status_sequence=[429, 200])
    client = OpenAICompatibleClient(transport=transport)
    res = await client.post_responses({"model":"x","input":[]},
        tenant_id="t", correlation_id="c", request_id="r")
    assert res["ok"] is True
```

---

## Step-By-Step Implementation

1. **Settings**
   - Extend `Settings` with Apigee/OpenAI and HTTP tuning variables (envs in `.env.example`).

2. **Token provider**
   - Add `ApigeeTokenProvider` that performs client-credentials token exchange against `APIGEE_TOKEN_URL`, caches token with TTL and 60s skew, and exposes `get_token()`.

3. **Headers policy**
   - Add `build_default_headers()` to standardize mandatory headers and merge config-driven extra headers without clobbering explicit arguments.

4. **HTTP client**
   - Create `OpenAICompatibleClient` using a module-level pooled `httpx.AsyncClient` (limits, timeouts, redirects).
   - Implement `request()` with exponential backoff (429/5xx) and `post_responses()` convenience method.

5. **Light touch demonstration**
   - (Commented in `routes_execute.py`) show future usage pattern—full execution wiring will arrive in PR-08/PR-09.

6. **Tests**
   - Use `httpx.MockTransport` to simulate token and gateway endpoints.
   - Validate token caching, header presence, and retry-on-429.

---

## Acceptance Criteria
- [ ] `ApigeeTokenProvider` fetches and **caches** tokens; refreshes when near expiry.
- [ ] `build_default_headers()` injects expected headers, including timestamp and client id.
- [ ] Client automatically sets `Authorization: Bearer <token>` and merges per-request headers.
- [ ] Retries with exponential backoff occur on 429/5xx up to `HTTP_RETRY_MAX_ATTEMPTS`.
- [ ] `post_responses()` successfully POSTs JSON to `/responses` and returns JSON.
- [ ] Tests in this PR pass with **≥80%** coverage for added modules.
- [ ] No new runtime dependencies beyond those already declared.

## Validation
```bash
# Set env vars (adjust URLs/secrets to your env)
set APIGEE_TOKEN_URL=https://gw/token
set APIGEE_CLIENT_ID=id
set APIGEE_CLIENT_SECRET=secret
set OPENAI_BASE_URL=https://gw/openai

pytest -q
```

---

## Manual Pre/Post Items
- **Real endpoints/secrets:** Replace `APIGEE_TOKEN_URL`, `APIGEE_CLIENT_ID`, `APIGEE_CLIENT_SECRET`, `OPENAI_BASE_URL` with your real values.
- **Scopes:** If your gateway requires specific scopes/audience, set `APIGEE_SCOPES` accordingly.
- **Tenant headers:** If tenants require extra headers, set `APIGEE_EXTRA_HEADERS_JSON` (JSON dict).
- **Corporate TLS:** If your enterprise requires a custom CA bundle, configure `SSL_CERT_FILE` or point httpx transport to your CA (to be added in a later PR).
- **Schema file:** Confirm `schemas/orchestration_ir.schema.json` is present (from PR-01).
- **Perf tuning:** Adjust pool sizes and timeouts to match your environment’s RPS/latency.

---

## PR Tenets (Append to All PRs)

- Ensure code is clean, readable, and well-structured; keep functions small and focused.  
- Prefer async I/O; make hot paths non-blocking and bound concurrency where needed.  
- Type-annotate everything; validate inputs/outputs with Pydantic models.  
- Keep dependencies minimal and justified; remove anything unused.  
- Maintain API versioning (`/v1/*`) and accurate OpenAPI docs.  
- Write comprehensive tests (unit + light integration); keep coverage ≥ 80%.  
- Make tests deterministic (no network, time, or randomness without fixtures/mocks).  
- Implement clear error taxonomy; never leak stack traces or secrets in responses.  
- Use correlation/request IDs end-to-end; log enough to trace a request.  
- Honor configuration via `.env`; never hardcode secrets or tenant data.  
- Propagate required headers (tenant, correlation, auth) to downstream services.  
- Follow JSON-Schema & topology rules for the IR; fail fast with actionable messages.  
- Keep the orchestration compiler/extensibility simple (new nodes/tools are plug-in friendly).  
- Match OpenAI **Responses** SSE event names verbatim; don’t invent new shapes.  
- Respect telemetry level header (`none|basic|verbose`) and apply PII redaction when enabled.  
- Add timeouts, retries (with backoff), and circuit-breaker logic where appropriate.  
- Favor clarity over micro-optimizations; document any intentional trade-offs.  
- No commented-out code or TODOs; use issues/PR descriptions for follow-ups.  
- Keep PRs small, atomic, and self-contained with runnable validation steps.  
- Provide clear docs for junior devs (what it does, why, how to run/test).  
- Ensure graceful startup/shutdown and idempotent behavior on resume/retry.  
- Leave the system in a working state: `uvicorn app.main:app --reload` + `/docs` must work.
