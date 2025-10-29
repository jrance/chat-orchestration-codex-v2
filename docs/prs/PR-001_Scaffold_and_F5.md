# PROMPT FOR CODEX

Implement the following PR and validate that everything has been completed fully and that all tests pass and all acceptance criteria is met. Feel free to modify the recommended code if there is a better and cleaner approach.

---

# PR-01 of 14 — Repository Scaffold & VS Code F5

## PR Title
Scaffold application (PR 1 of 14)

## Description
Initial FastAPI project skeleton with versioned `/v1` routes, stubbed endpoints (`/v1/compile`, `/v1/validate`, `/v1/execute`, `/v1/execute/stream`, `/v1/execute/{runId}/resume`), OpenAPI docs at `/docs`, environment-based configuration, VS Code F5 debug config, basic SSE plumbing, and a minimal test harness with ≥80% coverage gate for this PR’s code.

## Purpose
Provide a clean, maintainable foundation that respects the project’s constraints: versioned APIs, .env configuration, FastAPI + OpenAPI, no Docker, minimal dependencies, and testability from day one.

## Scope
- Project layout, dependencies, scripts
- Settings loader from `.env`
- Versioned API router (`/v1`)
- Endpoint stubs returning 501 “Not Implemented” (but fully typed)
- SSE streaming helper (simple, dependency-free)
- Basic middleware to thread request/correlation ids (structured logging comes in PR-02)
- Tests (smoke + SSE) with coverage gate ≥80% for this PR
- VS Code F5 (launch.json)
- Seed docs: `README.md` and `AGENTS.md` (skeleton)
- Add the provided IR JSON Schema file to `schemas/` (validation logic lands in PR-04)

## Goals
- Run locally with uvicorn or VS Code F5
- `/docs` shows all v1 endpoints with correct request/response models
- SSE endpoint proves streaming plumbing works
- Tests pass with ≥80% coverage for new code

---

## Files to Add

```
.
├─ app/
│  ├─ __init__.py
│  ├─ main.py
│  ├─ config/
│  │  ├─ __init__.py
│  │  └─ settings.py
│  ├─ api/
│  │  ├─ __init__.py
│  │  └─ v1/
│  │     ├─ __init__.py
│  │     ├─ routes_compile.py
│  │     ├─ routes_validate.py
│  │     ├─ routes_execute.py
│  │     └─ models.py
│  ├─ utils/
│  │  ├─ __init__.py
│  │  ├─ ids.py
│  │  └─ sse.py
│  └─ middleware/
│     ├─ __init__.py
│     └─ request_ids.py
├─ schemas/
│  └─ orchestration_ir.schema.json
├─ tests/
│  ├─ __init__.py
│  ├─ test_health.py
│  ├─ test_v1_routes_smoke.py
│  └─ test_sse_stream.py
├─ .env.example
├─ .gitignore
├─ .vscode/
│  └─ launch.json
├─ pyproject.toml
├─ README.md
├─ AGENTS.md
└─ pytest.ini
```

### `pyproject.toml`
```toml
[project]
name = "codeless-orch"
version = "0.1.0"
description = "Codeless orchestration engine (LangGraph) — FastAPI scaffold"
readme = "README.md"
requires-python = ">=3.11,<3.13"
dependencies = [
  "fastapi>=0.115",
  "uvicorn[standard]>=0.30",
  "pydantic>=2.7",
  "pydantic-settings>=2.2",
  "httpx>=0.27",
  "typing-extensions>=4.12",
]

[project.optional-dependencies]
dev = [
  "pytest>=8.3",
  "pytest-asyncio>=0.23",
  "pytest-cov>=5.0",
  "anyio>=4.4",
]

[tool.pytest.ini_options]
addopts = "-q"

[tool.uvicorn]
factory = false
reload = true
```

### `.gitignore`
```
__pycache__/
*.pyc
.env
.venv/
.coverage
htmlcov/
```

### `.env.example`
```
APP_NAME=codeless-orch
APP_ENV=dev
APP_HOST=127.0.0.1
APP_PORT=8000

# Tenant & downstream header defaults (used later; present here for dev ergonomics)
DEFAULT_TENANT_ID=demo-tenant
APIGEE_CLIENT_ID=replace-me
APIGEE_TOKEN_URL=https://gateway.example.com/oauth2/token
```

### `app/config/settings.py`
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

    model_config = SettingsConfigDict(env_file=".env", env_prefix="", extra="ignore")

settings = Settings()
```

### `app/middleware/request_ids.py`
```python
import time
import uuid
from starlette.types import ASGIApp, Receive, Scope, Send

CORRELATION = "X-Correlation-Id"
REQUEST_ID = "X-Request-Id"
TIMESTAMP = "X-Timestamp"

class RequestIdMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = dict(message.get("headers", []))
                def get_header(h):
                    return headers.get(h.lower().encode())
                corr = get_header(CORRELATION) or str(uuid.uuid4()).encode()
                rid = get_header(REQUEST_ID) or str(uuid.uuid4()).encode()
                ts = time.strftime("%Y-%m-%dT%H:%M:%SZ").encode()

                message["headers"] = (message.get("headers") or []) + [
                    (CORRELATION.encode(), corr),
                    (REQUEST_ID.encode(), rid),
                    (TIMESTAMP.encode(), ts),
                ]
            await send(message)

        await self.app(scope, receive, send_wrapper)
```

### `app/utils/ids.py`
```python
import uuid

def new_id() -> str:
    return str(uuid.uuid4())
```

### `app/utils/sse.py`
```python
from typing import AsyncIterator, Mapping, Any

def format_sse(data: str, event: str | None = None, id: str | None = None) -> str:
    """Minimal SSE formatter; avoids extra deps."""
    lines = []
    if id:
        lines.append(f"id: {id}")
    if event:
        lines.append(f"event: {event}")
    for chunk in data.splitlines() or [""]:
        lines.append(f"data: {chunk}")
    return "\n".join(lines) + "\n\n"

async def sse_stream(gen: AsyncIterator[Mapping[str, Any]]):
    """Yield properly framed SSE messages from dicts with keys: event, id, data."""
    async for msg in gen:
        yield format_sse(
            data=msg.get("data", ""),
            event=msg.get("event"),
            id=msg.get("id"),
        )
```

### `app/api/v1/models.py`
```python
from pydantic import BaseModel, Field
from typing import Any, Dict, Optional

class OrchestrationPackage(BaseModel):
    meta: Dict[str, Any]
    nodes: list[Dict[str, Any]]
    edges: list[Dict[str, Any]]
    entryId: Optional[str] = None

class ValidateResponse(BaseModel):
    ok: bool = False
    message: str = "Not implemented"
    normalized: Optional[Dict[str, Any]] = None

class CompileResponse(BaseModel):
    ok: bool = False
    graph_id: Optional[str] = None
    message: str = "Not implemented"

class ExecuteRequest(BaseModel):
    package: OrchestrationPackage
    input: Dict[str, Any] = Field(default_factory=dict)

class ExecuteResponse(BaseModel):
    ok: bool = False
    runId: Optional[str] = None
    message: str = "Not implemented"
```

### `app/api/v1/routes_validate.py`
```python
from fastapi import APIRouter
from .models import OrchestrationPackage, ValidateResponse

router = APIRouter(prefix="/v1", tags=["v1"])

@router.post("/validate", response_model=ValidateResponse, status_code=501)
async def validate_package(pkg: OrchestrationPackage):
    return ValidateResponse(ok=False, message="Validation not yet implemented")
```

### `app/api/v1/routes_compile.py`
```python
from fastapi import APIRouter
from .models import OrchestrationPackage, CompileResponse

router = APIRouter(prefix="/v1", tags=["v1"])

@router.post("/compile", response_model=CompileResponse, status_code=501)
async def compile_package(pkg: OrchestrationPackage):
    return CompileResponse(ok=False, message="Compile not yet implemented")
```

### `app/api/v1/routes_execute.py`
```python
import asyncio
from fastapi import APIRouter, Header
from fastapi.responses import StreamingResponse
from .models import ExecuteRequest, ExecuteResponse
from ...utils.sse import sse_stream

router = APIRouter(prefix="/v1", tags=["v1"])

@router.post("/execute", response_model=ExecuteResponse, status_code=501)
async def execute(req: ExecuteRequest):
    return ExecuteResponse(ok=False, message="Execute not yet implemented")

@router.get("/execute/{runId}/resume", response_model=ExecuteResponse, status_code=501)
async def resume(runId: str):
    return ExecuteResponse(ok=False, runId=runId, message="Resume not yet implemented")

@router.post("/execute/stream")
async def execute_stream(
    req: ExecuteRequest,
    x_telemetry: str | None = Header(default=None, alias="X-Telemetry")
):
    async def gen():
        yield {"event": "response.created", "data": '{"status":"starting"}'}
        await asyncio.sleep(0.01)
        yield {"event": "response.output_text.delta", "data": "streaming ready..."}
        await asyncio.sleep(0.01)
        yield {"event": "response.completed", "data": '{"status":"done"}'}

    return StreamingResponse(
        sse_stream(gen()),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )
```

### `app/main.py`
```python
from fastapi import FastAPI
from app.config.settings import settings
from app.middleware.request_ids import RequestIdMiddleware
from app.api.v1.routes_validate import router as validate_router
from app.api.v1.routes_compile import router as compile_router
from app.api.v1.routes_execute import router as execute_router

def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        docs_url="/docs",
        redoc_url=None,
    )

    app.add_middleware(RequestIdMiddleware)

    @app.get("/health", tags=["meta"])
    async def health():
        return {"status": "ok", "env": settings.app_env}

    app.include_router(validate_router)
    app.include_router(compile_router)
    app.include_router(execute_router)
    return app

app = create_app()
```

### `schemas/orchestration_ir.schema.json`
Save the exact JSON Schema provided in the project brief to this file. (Validation logic will be implemented in PR-04.)

### `.vscode/launch.json`
```json
{
  "version": "0.2.0",
  "configurations": [
    {
      "name": "FastAPI (uvicorn)",
      "type": "python",
      "request": "launch",
      "module": "uvicorn",
      "args": ["app.main:app", "--host", "127.0.0.1", "--port", "8000", "--reload"],
      "jinja": true,
      "envFile": "${workspaceFolder}/.env",
      "console": "integratedTerminal"
    }
  ]
}
```

### `README.md`
```markdown
# Codeless Orchestration Engine (Scaffold)

- FastAPI app with versioned `/v1` routes.
- OpenAPI docs at `/docs`.
- Env config via `.env` (see `.env.example`).
- No Docker; run with VS Code F5 or uvicorn.

## Quickstart
```bash
python -m venv .venv && . .venv/Scripts/activate  # Windows (or source .venv/bin/activate)
pip install -e ".[dev]"
cp .env.example .env
uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000/docs

## Endpoints (stubbed)
- `POST /v1/validate`
- `POST /v1/compile`
- `POST /v1/execute`
- `GET  /v1/execute/{runId}/resume`
- `POST /v1/execute/stream` (SSE demo)

Next PRs add validation, compilation to LangGraph, execution, streaming mapping to Responses API, and telemetry.
```

### `AGENTS.md`
```markdown
# AGENTS — Principles & Conventions (Seed)

- **Versioned APIs**: `/v1/*` only.
- **Configuration**: `.env` for local dev; overridable in tests.
- **Extensibility**: Node/Agent/Tool registries added in future PRs.
- **Performance**: Async everywhere; bounded concurrency to be introduced.
- **Testing**: Each PR maintains ≥80% coverage for new code.
```

### `pytest.ini`
```ini
[pytest]
asyncio_mode = auto
addopts = --cov=app --cov-report=term-missing --cov-fail-under=80
```

---

## Step-By-Step Implementation

1. Initialize project & deps
   - Create folders/files as shown.
   - `pip install -e ".[dev]"`.

2. Settings loader
   - Implement `Settings` with `pydantic-settings` to read `.env`.

3. App factory & middleware
   - `create_app()` sets title/version and attaches `RequestIdMiddleware`.
   - Add `/health`.

4. Versioned routers
   - Implement `routes_validate`, `routes_compile`, `routes_execute` with `/v1` prefix and typed models. Return 501 stubs.

5. SSE helper
   - Add minimal `format_sse` + `sse_stream`.
   - Wire `/v1/execute/stream` to yield a short 3-event demo.

6. Schema file
   - Save the complete IR JSON Schema to `schemas/orchestration_ir.schema.json` (no logic yet).

7. VS Code F5
   - Add `.vscode/launch.json` using `envFile: ".env"`.

8. Tests & coverage
   - Add three tests (health, smokes for v1 routes, SSE framing).
   - Include `pytest.ini` with coverage gate 80.

9. Docs
   - Write `README.md` quickstart, and seed `AGENTS.md`.

---

## Examples

**SSE smoke test with curl**
```bash
curl -N -H "Accept: text/event-stream"      -X POST http://127.0.0.1:8000/v1/execute/stream      -H "Content-Type: application/json"      -d '{"package":{"meta":{},"nodes":[],"edges":[]},"input":{}}'
```
Expected (sample):
```
event: response.created
data: {"status":"starting"}

event: response.output_text.delta
data: streaming ready...

event: response.completed
data: {"status":"done"}
```

---

## Tests

### `tests/test_health.py`
```python
import pytest
from httpx import AsyncClient
from app.main import app

@pytest.mark.anyio
async def test_health():
    async with AsyncClient(app=app, base_url="http://test") as ac:
        r = await ac.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"
```

### `tests/test_v1_routes_smoke.py`
```python
import pytest
from httpx import AsyncClient
from app.main import app

PKG = {"meta": {"id":"x","name":"y","version":"1.0.0"}, "nodes": [], "edges": []}

@pytest.mark.anyio
async def test_validate_stub():
    async with AsyncClient(app=app, base_url="http://test") as ac:
        r = await ac.post("/v1/validate", json=PKG)
        assert r.status_code == 501
        assert r.json()["ok"] is False

@pytest.mark.anyio
async def test_compile_stub():
    async with AsyncClient(app=app, base_url="http://test") as ac:
        r = await ac.post("/v1/compile", json=PKG)
        assert r.status_code == 501
        assert r.json()["ok"] is False

@pytest.mark.anyio
async def test_execute_stub():
    async with AsyncClient(app=app, base_url="http://test") as ac:
        r = await ac.post("/v1/execute", json={"package": PKG, "input": {}})
        assert r.status_code == 501
        assert r.json()["ok"] is False

@pytest.mark.anyio
async def test_resume_stub():
    async with AsyncClient(app=app, base_url="http://test") as ac:
        r = await ac.get("/v1/execute/run-123/resume")
        assert r.status_code == 501
        assert r.json()["ok"] is False
        assert r.json()["runId"] == "run-123"
```

### `tests/test_sse_stream.py`
```python
import pytest
from httpx import AsyncClient
from app.main import app

@pytest.mark.anyio
async def test_stream_sse():
    async with AsyncClient(app=app, base_url="http://test") as ac:
        r = await ac.post("/v1/execute/stream", json={"package":{"meta":{},"nodes":[],"edges":[]},"input":{}})
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        body = r.text
        assert "event: response.created" in body
        assert "event: response.completed" in body
```

---

## Acceptance Criteria
- [ ] Project boots with `uvicorn app.main:app --reload`
- [ ] `/health` returns `{status:"ok"}`
- [ ] `/docs` shows all v1 routes with typed models
- [ ] All v1 routes exist and return 501 with shaped payloads (until later PRs)
- [ ] `/v1/execute/stream` returns properly framed SSE (at least 2 events)
- [ ] `.env` values load (verify via `/health` env field)
- [ ] Tests pass locally with **≥80%** coverage for `app/*` in this PR
- [ ] VS Code F5 launches server and reads `.env`

## Validation Steps
1. **Run**
   ```bash
   pip install -e ".[dev]"
   cp .env.example .env
   uvicorn app.main:app --reload
   ```
   Visit `http://127.0.0.1:8000/docs` and confirm endpoints.

2. **SSE check**
   Use the curl example above and confirm event lines arrive.

3. **Coverage gate**
   ```bash
   pytest
   ```
   Ensure coverage shows ≥80% on the files added in this PR.

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
