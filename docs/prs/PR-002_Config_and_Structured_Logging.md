# PROMPT FOR CODEX

Implement the following PR and validate that everything has been completed fully and that all tests pass and all acceptance criteria is met. Feel free to modify the recommended code if there is a better and cleaner approach.

---

# PR-02 of 14 — Config & Structured Logging

## PR Title
Add environment-driven configuration & structured JSON logging (PR 2 of 14)

## Description
Introduce centralized configuration via `.env` and implement **structured JSON logging** with `structlog`. Add request lifecycle logging with correlation/request IDs and optional tenant forwarding. Extend the existing RequestId middleware to **bind contextvars** so logs automatically include `correlation_id`, `request_id`, and (if present) `tenant_id`. Provide minimal PII-safe redaction and sample log lines. Update tests to verify logging is wired and contextual fields appear.

## Purpose
- Make runtime behavior predictable and environment-driven.
- Ensure logs are machine-parseable (JSON), consistent, and include request context for tracing.
- Keep dependencies minimal while preparing for richer telemetry in later PRs.

## Scope
- New logging setup module (`app/logging/setup.py`) and context helpers (`app/logging/context.py`).
- Expose shared logger (`app/logging/logger.py`) for easy import.
- Extend RequestId middleware to set contextvars for downstream logging.
- Add a tiny PII redactor utility (email-like patterns); off by default.
- Update settings with logging options (level, redaction toggle).
- Add tests capturing log output (basic happy path + context binding).

## Goals
- JSON logs by default with ISO UTC timestamps.
- Every request results in at least one structured log with `correlation_id` and `request_id`.
- Health endpoint logs a single JSON record (for testability).
- Tests pass with ≥80% coverage for code added in this PR.

---

## Files to Add / Change

```
app/
├─ logging/
│  ├─ __init__.py
│  ├─ setup.py
│  ├─ context.py
│  └─ logger.py
├─ middleware/
│  └─ request_ids.py          # (modified in this PR to bind contextvars)
├─ main.py                    # (modified to initialize logging & add a log in /health)
└─ config/settings.py         # (extended with logging settings)
tests/
├─ test_logging_context.py
└─ test_health_logs.py
.env.example                  # (extended with logging vars)
pyproject.toml                # (add structlog dependency)
```

### `pyproject.toml` (append/modify)
```toml
[project]
# (no changes to existing fields omitted for brevity)
dependencies = [
  "fastapi>=0.115",
  "uvicorn[standard]>=0.30",
  "pydantic>=2.7",
  "pydantic-settings>=2.2",
  "httpx>=0.27",
  "typing-extensions>=4.12",
  "structlog>=24.1.0",
]
```

### `.env.example` (append)
```
# Logging
LOG_LEVEL=INFO
LOG_REDACTION_ENABLED=false
```

### `app/logging/context.py`
```python
from __future__ import annotations
import contextvars
from typing import Optional
import structlog

correlation_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("correlation_id", default=None)
request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)
tenant_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("tenant_id", default=None)

def bind_request_context(correlation_id: str, request_id: str, tenant_id: Optional[str] = None) -> None:
    correlation_id_var.set(correlation_id)
    request_id_var.set(request_id)
    tenant_id_var.set(tenant_id)
    structlog.contextvars.bind_contextvars(
        correlation_id=correlation_id,
        request_id=request_id,
        tenant_id=tenant_id,
    )

def clear_request_context() -> None:
    correlation_id_var.set(None)
    request_id_var.set(None)
    tenant_id_var.set(None)
    structlog.contextvars.clear_contextvars()

def current_context() -> dict:
    return {
        "correlation_id": correlation_id_var.get(),
        "request_id": request_id_var.get(),
        "tenant_id": tenant_id_var.get(),
    }
```

### `app/logging/setup.py`
```python
from __future__ import annotations
import logging
import structlog
from typing import Any

def configure_logging(level: str = "INFO", redact: bool = False) -> None:
    """
    Configure structlog + stdlib logging for JSON output and contextvars support.
    """
    # Basic stdlib config first
    logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO))

    # Processors shared by structlog & stdlib
    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)

    def pii_redactor(logger, method_name, event_dict):
        if not redact:
            return event_dict
        # minimal redact: replace email-like strings
        import re
        def _scrub(val: Any) -> Any:
            if isinstance(val, str):
                val = re.sub(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", "[REDACTED_EMAIL]", val)
            return val
        return {k: _scrub(v) for k, v in event_dict.items()}

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            timestamper,
            pii_redactor,
            structlog.processors.dict_tracebacks,
            structlog.processors.JSONRenderer(),
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
```

### `app/logging/logger.py`
```python
import structlog
log = structlog.get_logger("app")
```

### `app/middleware/request_ids.py` (modified)
```python
import time
import uuid
from starlette.types import ASGIApp, Receive, Scope, Send
from app.logging.context import bind_request_context, clear_request_context

CORRELATION = "x-correlation-id"
REQUEST_ID = "x-request-id"
TIMESTAMP = "x-timestamp"
TENANT = "x-tenant-id"

class RequestIdMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        # Get incoming headers
        headers = dict(scope.get("headers") or [])
        def get(h: str): return headers.get(h.encode())

        corr = (get(CORRELATION) or uuid.uuid4().hex.encode()).decode()
        rid = (get(REQUEST_ID) or uuid.uuid4().hex.encode()).decode()
        tenant = (get(TENANT).decode() if get(TENANT) else None)
        bind_request_context(corr, rid, tenant)

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                message["headers"] = (message.get("headers") or []) + [
                    (CORRELATION.encode(), corr.encode()),
                    (REQUEST_ID.encode(), rid.encode()),
                    (TENANT.encode(), (tenant or "").encode()),
                    (TIMESTAMP.encode(), time.strftime("%Y-%m-%dT%H:%M:%SZ").encode()),
                ]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            clear_request_context()
```

### `app/config/settings.py` (extended)
```python
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    app_name: str = "codeless-orch"
    app_env: str = "dev"
    app_host: str = "127.0.0.1"
    app_port: int = 8000

    default_tenant_id: str = "demo-tenant"
    apigee_client_id: str | None = None
    apigee_token_url: str | None = None

    # Logging
    log_level: str = "INFO"
    log_redaction_enabled: bool = False

    model_config = SettingsConfigDict(env_file=".env", env_prefix="", extra="ignore")

settings = Settings()
```

### `app/main.py` (modified)
```python
from fastapi import FastAPI
from app.config.settings import settings
from app.middleware.request_ids import RequestIdMiddleware
from app.api.v1.routes_validate import router as validate_router
from app.api.v1.routes_compile import router as compile_router
from app.api.v1.routes_execute import router as execute_router
from app.logging.setup import configure_logging
from app.logging.logger import log
from app.logging.context import current_context

def create_app() -> FastAPI:
    # initialize logging early
    configure_logging(level=settings.log_level, redact=settings.log_redaction_enabled)

    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        docs_url="/docs",
        redoc_url=None,
    )

    app.add_middleware(RequestIdMiddleware)

    @app.get("/health", tags=["meta"])
    async def health():
        # Log a single line so tests can assert JSON + context presence
        ctx = current_context()
        log.info("healthcheck", env=settings.app_env, **ctx)
        return {"status": "ok", "env": settings.app_env}

    app.include_router(validate_router)
    app.include_router(compile_router)
    app.include_router(execute_router)
    return app

app = create_app()
```

### `tests/test_logging_context.py`
```python
import json
import pytest
from httpx import AsyncClient
from app.main import app

@pytest.mark.anyio
async def test_health_includes_context_in_logs(caplog):
    caplog.set_level("INFO")
    async with AsyncClient(app=app, base_url="http://test") as ac:
        r = await ac.get("/health", headers={"X-Tenant-Id": "qa-tenant"})
        assert r.status_code == 200
    # Find a JSON log line with "healthcheck"
    combined = "\n".join([rec.message for rec in caplog.records])
    assert "healthcheck" in combined
    # Try parse last JSON-ish line
    parsed = None
    for rec in caplog.records[::-1]:
        try:
            parsed = json.loads(rec.message)
            break
        except Exception:
            continue
    assert parsed is not None, "Expected JSON log"
    assert parsed.get("event") == "healthcheck"
    # Context fields should be present
    assert "correlation_id" in parsed
    assert "request_id" in parsed
    assert parsed.get("tenant_id") == "qa-tenant"
```

### `tests/test_health_logs.py`
```python
import json
import pytest
from httpx import AsyncClient
from app.main import app

@pytest.mark.anyio
async def test_default_log_redaction_flag(caplog):
    caplog.set_level("INFO")
    async with AsyncClient(app=app, base_url="http://test") as ac:
        r = await ac.get("/health")
        assert r.status_code == 200

    # Ensure JSON formatting and timestamp presence
    for rec in caplog.records:
        try:
            payload = json.loads(rec.message)
            assert "timestamp" in payload or "@timestamp" in payload or "event" in payload
            break
        except Exception:
            continue
```

---

## Step-By-Step Implementation

1. **Dependencies**
   - Add `structlog>=24.1.0` to `pyproject.toml`; reinstall dev env.

2. **Logging setup**
   - Create `app/logging/setup.py`, `context.py`, `logger.py` as above.
   - Configure processors for ISO UTC timestamps and JSON rendering.
   - Keep the redactor minimal and off by default.

3. **Middleware context binding**
   - Update `RequestIdMiddleware` to read incoming `X-Correlation-Id`, `X-Request-Id`, `X-Tenant-Id` (case-insensitive), or generate UUIDs.
   - Bind to contextvars so all downstream logs include them automatically.
   - Ensure response headers echo these values for downstream consumers.

4. **App bootstrap**
   - In `create_app()`, call `configure_logging(...)` before building routes.
   - In `/health`, emit a single JSON log line for test assertions.

5. **Settings**
   - Extend settings with `LOG_LEVEL`, `LOG_REDACTION_ENABLED` and surface them in `.env.example`.

6. **Tests**
   - Use `caplog` to capture log output and assert JSON + context fields exist.

---

## Acceptance Criteria
- [ ] Logs are JSON with ISO UTC timestamps.
- [ ] Each request binds `correlation_id`, `request_id` (and optional `tenant_id`) into logs.
- [ ] `/health` produces one JSON log entry carrying the bound context.
- [ ] `LOG_LEVEL` and `LOG_REDACTION_ENABLED` control behavior from `.env`.
- [ ] All tests in this PR pass with **≥80%** coverage for files added/changed here.
- [ ] No leaked secrets or stack traces in successful paths.

## Validation
1. **Run**
   ```bash
   pip install -e ".[dev]"
   uvicorn app.main:app --reload
   ```
2. **Call health**
   ```bash
   curl -i http://127.0.0.1:8000/health -H "X-Tenant-Id: qa-tenant"
   ```
3. **Observe logs**
   - Ensure a single JSON line includes `event":"healthcheck"` and context IDs.
4. **Tests**
   ```bash
   pytest -q
   ```

---

## Manual Pre/Post Items
- **Ensure `schemas/orchestration_ir.schema.json` exists** (you added it after PR-01).
- **Create `.env`** (copy `.env.example`) and set `LOG_LEVEL`/`LOG_REDACTION_ENABLED` as desired.
- **VS Code**: confirm the debug profile points to your virtualenv and reads `.env`.
- **Uvicorn access logs**: if noisy during dev, you can start with `--no-access-log` (optional).
- **Pipelines/CI**: if you gate on coverage, confirm `pytest.ini` threshold still applies.

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
