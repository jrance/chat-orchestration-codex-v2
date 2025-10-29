
Implement the following PR and validate that everything has been completed fully and that all tests pass and all acceptance criteria is met. Feel free to modify the recommended code if there is a better and cleaner approach.

# PR-08 — Execute Endpoints, SSE Streaming (Responses API), and Telemetry

## Description
Add synchronous and streaming execution endpoints that compile an orchestration package (IR) to a LangGraph app and execute it. The streaming endpoint emits **OpenAI Responses API–compatible SSE events** so the front-end (OpenAI ChatKit) can render partial tokens and model/tool updates. A parallel telemetry stream is also exposed and can be enabled per request.

## Purpose
- Provide the primary runtime API surface for orchestration execution.
- Enable **interactive streaming UX** using OpenAI Responses-style events.
- Wire in request-scoped headers (tenant, correlation, request, timestamp) and Apigee/Gemini auth headers via our custom HTTP client.
- Enable optional **telemetry streaming** with basic/verbose levels and PII redaction hooks.

## Scope
- **Endpoints** (FastAPI):
  - `POST /v1/execute` — synchronous run; returns final response JSON.
  - `POST /v1/execute/stream` — SSE; emits `response.*` events and optional `tool.*` / `mcp.*` passthrough events from the engine.
  - `POST /v1/execute/{runId}/resume` — resume a paused HITL/checkpointed run (synchronous for now).
  - `GET  /v1/telemetry/stream?runId=...` — separate SSE stream for server telemetry when requested.
- **Headers**:
  - `X-Tenant-Id` (required, must match `meta.tenantId` in IR when present)
  - `X-Correlation-Id`, `X-Request-Id`, `X-Timestamp` (RFC3339), `X-Client-Id`, plus any Apigee-required headers
  - `X-Telemetry: none|basic|verbose` (default `none`)
- **PII redaction**: honor agent/tool settings and global env (`LOG_REDACTION_ENABLED=true|false`); redact in telemetry and error payloads.
- **Checkpointer**: use in-memory implementation behind an interface; attach `runId` and `threadId` consistently.
- **OpenAPI**: document endpoints and SSE response shape at `/docs`.

## Goals
- **Pass-through SSE** in OpenAI Responses event format: `response.created`, `response.output_text.delta`, `response.completed`, plus tool/MCP events where available.
- **No UI logic** here. This app is server-only.
- **Async-first** implementation with `httpx.AsyncClient` and async generators for SSE.
- ≥ **80% test coverage** for new code (pytest + pytest-cov).

---

## Files to Add / Modify
- `app/api/routes/execute.py` (NEW): FastAPI routes for execute, stream, resume, telemetry.
- `app/api/deps.py` (NEW): header extraction, validation, context propagation helpers.
- `app/sse/streams.py` (NEW): SSE helpers (event formatting, heartbeats, graceful close).
- `app/telemetry/models.py` (NEW): typed telemetry events, redaction utilities.
- `app/telemetry/streamer.py` (NEW): per-run async broadcast for telemetry stream.
- `app/runtime/engine.py` (MOD): expose `run_once()` and **`run_stream()`** async generator yielding Responses-style events.
- `app/runtime/checkpoint.py` (MOD): ensure `acquire(run_id)`, `save_state`, `load_state` support resume.
- `app/clients/http.py` (MOD): ensure default headers (Apigee token, client id, tenant id, correlation/request ids, timestamp) and per-request overrides.
- `app/core/config.py` (MOD): add `CORS_ALLOWED_ORIGINS`, `APIGEE_*` config, and defaults.
- `tests/api/test_execute_stream.py` (NEW): endpoint & SSE tests (incl. basic/verbose telemetry and redaction).
- `tests/runtime/test_run_stream.py` (NEW): engine streaming adapter tests.
- `docs/API_EXECUTE.md` (NEW): endpoint docs and curl examples.

---

## Step-by-Step Implementation

1) **Route models**
   - Define `ExecuteRequest` with fields:
     - `ir: dict` (validated earlier by `/v1/validate`, but re-validate minimally here if `strict` query is set)
     - `input: list | str | None` (initial user message(s) / multimodal content)
     - `options: dict | None` (execution overrides: `resumeFrom`, `tool_choice`, `timeout`, etc.)
   - Define `ResumeRequest` with optional `input` appended on resume.

2) **Header parsing (app/api/deps.py)**
   - Read headers: `X-Tenant-Id` (required), `X-Telemetry` (`none|basic|verbose`), `X-Correlation-Id`, `X-Request-Id`, `X-Timestamp`, `X-Client-Id`.
   - Generate sane defaults when missing (UUIDv4, `datetime.now(tz=UTC).isoformat()`).
   - Validate that `X-Tenant-Id` equals `ir.meta.tenantId` when present; reject with 400 if mismatch.
   - Inject these values into a request-scoped `ExecutionContext` used by compiler, engine, and clients.

3) **Auth & custom HTTP client (Apigee + Gemini OpenAI-compatible)**
   - Use existing `app/clients/http.py` to acquire an OAuth token from Apigee and configure default headers:
     - `Authorization: Bearer <token>`
     - `X-Client-Id`, `X-Tenant-Id`, `X-Correlation-Id`, `X-Request-Id`, `X-Timestamp`
   - Allow per-call headers to be merged in an `extra_headers` dict in the execution context.
   - Expose `get_llm_client(context)` for the engine to call.

4) **Engine streaming adapter (app/runtime/engine.py)**
   - Implement `async def run_stream(ir, input, context) -> AsyncIterator[dict]` that yields **Responses-style** events:
     - `{ "type": "response.created", "response": { "id": "resp_...", ... } }`
     - `{ "type": "response.output_text.delta", "delta": "partial text" }`
     - Tool/MCP events (when tools are enabled), e.g. `{ "type": "response.tool_call.arguments.delta", ... }`
     - `{ "type": "response.completed", "response": {...}, "usage": {...} }`
   - If HITL pause is requested by a node, emit `{ "type": "response.completed", "status": "requires_action", "run_id": "...", "checkpoint": {...} }` and persist state.

   *Notes:* For PR-08 it is acceptable to use a **mock provider adapter** that simulates streaming from a final text output (until the real provider adapter lands in a later PR).

5) **SSE helpers (app/sse/streams.py)**
   - Implement `format_sse(event_type: str, data: dict) -> bytes` producing:
     ```
     event: <event_type>\n
     data: <json>\n
     \n
     ```
   - Add periodic heartbeats `event: ping` with empty `data` every 15s to keep proxies alive.
   - Ensure `text/event-stream` content-type, `Cache-Control: no-cache`, `Connection: keep-alive` and CORS headers.

6) **Telemetry streamer (app/telemetry/streamer.py)**
   - Create a per-run async pub/sub using `asyncio.Queue` or lightweight broadcast channel.
   - The engine publishes telemetry envelopes such as `{"kind":"llm.request","agent":"Answer Generation Agent", "payload":{...}}`.
   - Redact payloads if `redaction_enabled` or agent/tool setting requests PII redaction.
   - Expose `async def telemetry_events(run_id) -> AsyncIterator[dict]` to feed `/v1/telemetry/stream`.

7) **Routes (app/api/routes/execute.py)**
   - `POST /v1/execute`:
     - Compile IR → graph
     - Execute with engine `run_once` (non-streaming); return final response JSON (including usage and run metadata).
   - `POST /v1/execute/stream`:
     - Compile IR → graph
     - Return `StreamingResponse` that iterates `engine.run_stream(...)` and formats SSE frames:
       - Raw OpenAI Responses events (`response.*`) are passed through as SSE `event:` names with `data:` JSON.
     - If `X-Telemetry != none`, also include a **`X-Telemetry-Stream-Url`** header pointing to `/v1/telemetry/stream?runId=...`.
   - `POST /v1/execute/{runId}/resume`:
     - Load checkpoint by `runId` and resume the workflow with optional `input`.
   - `GET /v1/telemetry/stream?runId=...`:
     - Stream `telemetry.*` events (see below).

8) **Telemetry event schema (examples)**
   - `telemetry.agent.start`, `telemetry.agent.end`
   - `telemetry.llm.request`, `telemetry.llm.response`
   - `telemetry.tool.call`, `telemetry.tool.result`
   - `telemetry.mcp.call`, `telemetry.mcp.result`
   - Include `runId`, `agentLabel`, `nodeId`, timestamps, and redacted payloads.

9) **Error handling**
   - Map internal exceptions to JSON error envelopes and SSE `event: error` frames with sanitized messages and `correlationId`.
   - Ensure 499/ClientClosedRequest is handled without error noise.

10) **OpenAPI**
    - Document query/headers and SSE semantics.
    - Provide example curl for both endpoints.

---

## Code — Key Snippets

> These snippets are intentionally concise. Implement full error handling and logging per our standards.

**app/api/deps.py**
```python
from __future__ import annotations
import uuid, datetime as dt
from typing import Literal
from pydantic import BaseModel
from fastapi import Header, HTTPException

TelemetryLevel = Literal["none", "basic", "verbose"]

class RequestContext(BaseModel):
    tenant_id: str
    correlation_id: str
    request_id: str
    timestamp: str  # RFC3339
    client_id: str | None = None
    telemetry: TelemetryLevel = "none"

async def get_request_context(
    x_tenant_id: str = Header(..., alias="X-Tenant-Id"),
    x_correlation_id: str | None = Header(None, alias="X-Correlation-Id"),
    x_request_id: str | None = Header(None, alias="X-Request-Id"),
    x_timestamp: str | None = Header(None, alias="X-Timestamp"),
    x_client_id: str | None = Header(None, alias="X-Client-Id"),
    x_telemetry: TelemetryLevel = Header("none", alias="X-Telemetry"),
) -> RequestContext:
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    ctx = RequestContext(
        tenant_id=x_tenant_id,
        correlation_id=x_correlation_id or str(uuid.uuid4()),
        request_id=x_request_id or str(uuid.uuid4()),
        timestamp=x_timestamp or now,
        client_id=x_client_id,
        telemetry=x_telemetry,
    )
    return ctx
```

**app/sse/streams.py**
```python
import json, asyncio
from typing import AsyncIterator, Callable, Any

def format_sse(event_type: str, data: dict | list | str | None) -> bytes:
    payload = data if isinstance(data, str) else json.dumps(data or {})
    return (f"event: {event_type}\n" f"data: {payload}\n\n").encode("utf-8")

async def with_heartbeats(source: AsyncIterator[dict], interval_sec: int = 15):
    hb = asyncio.create_task(asyncio.sleep(interval_sec))
    try:
        async for evt in source:
            yield format_sse(evt.get("type", "message"), evt)
            if hb.done():
                yield b"event: ping\n\n"
                hb = asyncio.create_task(asyncio.sleep(interval_sec))
    finally:
        if not hb.done():
            hb.cancel()
```

**app/api/routes/execute.py** (abridged)
```python
from fastapi import APIRouter, Depends, Response, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse
from app.api.deps import get_request_context, RequestContext
from app.sse.streams import with_heartbeats
from app.runtime.engine import run_once, run_stream
from app.runtime.compiler import compile_ir  # already implemented earlier
from app.telemetry.streamer import telemetry_stream_for_run

router = APIRouter(prefix="/v1", tags=["execute"])

@router.post("/execute")
async def execute_sync(body: dict, ctx: RequestContext = Depends(get_request_context)):
    ir, input = body.get("ir"), body.get("input")
    if not ir:
        raise HTTPException(400, "Missing 'ir' in request body")
    graph = await compile_ir(ir, ctx=ctx.model_dump())
    result = await run_once(graph=graph, input=input, ctx=ctx.model_dump())
    return JSONResponse(result)

@router.post("/execute/stream")
async def execute_stream(body: dict, response: Response, ctx: RequestContext = Depends(get_request_context)):
    ir, input = body.get("ir"), body.get("input")
    if not ir:
        raise HTTPException(400, "Missing 'ir' in request body")
    graph = await compile_ir(ir, ctx=ctx.model_dump())
    run_id = graph.get("run_id")  # ensure engine sets this deterministically
    if ctx.telemetry != "none":
        response.headers["X-Telemetry-Stream-Url"] = f"/v1/telemetry/stream?runId={run_id}"
    stream = run_stream(graph=graph, input=input, ctx=ctx.model_dump())
    return StreamingResponse(with_heartbeats(stream), media_type="text/event-stream")

@router.post("/execute/{runId}/resume")
async def resume(runId: str, body: dict, ctx: RequestContext = Depends(get_request_context)):
    input = body.get("input")
    result = await run_once(resume_from=runId, input=input, ctx=ctx.model_dump())
    return JSONResponse(result)

@router.get("/telemetry/stream")
async def telemetry_stream(runId: str, ctx: RequestContext = Depends(get_request_context)):
    stream = telemetry_stream_for_run(runId, level=ctx.telemetry)
    return StreamingResponse(with_heartbeats(stream), media_type="text/event-stream")
```

**app/telemetry/models.py** (sketch)
```python
from pydantic import BaseModel

class TelemetryEvent(BaseModel):
    type: str  # e.g., telemetry.llm.request
    runId: str
    agentLabel: str | None = None
    nodeId: str | None = None
    timestamp: str
    payload: dict | None = None
```

**tests/api/test_execute_stream.py** (outline)
```python
import json, pytest, asyncio
from httpx import AsyncClient
from app.main import app

@pytest.mark.asyncio
async def test_streaming_sends_responses_events():
    async with AsyncClient(app=app, base_url="http://test") as ac:
        body = {"ir": {"meta": {"id":"x","name":"y","version":"1.0.0","tenantId":"t"},"nodes":[],"edges":[]}, "input":"hi"}
        headers = {"X-Tenant-Id":"t","X-Telemetry":"basic"}
        r = await ac.post("/v1/execute/stream", json=body, headers=headers)
        assert r.status_code == 200
        # Consume first few SSE chunks
        chunks = (await r.aread()).decode().split("\n\n")
        assert any("event: response.created" in c for c in chunks)
        assert any("event: response.output_text.delta" in c for c in chunks)
        assert any("event: response.completed" in c for c in chunks)

@pytest.mark.asyncio
async def test_telemetry_header_exposed():
    async with AsyncClient(app=app, base_url="http://test") as ac:
        body = {"ir": {"meta": {"id":"x","name":"y","version":"1.0.0","tenantId":"t"},"nodes":[],"edges":[]}, "input":"hi"}
        r = await ac.post("/v1/execute/stream", json=body, headers={"X-Tenant-Id":"t","X-Telemetry":"verbose"})
        assert "X-Telemetry-Stream-Url" in r.headers
```

---

## Examples (curl)

**Streaming (with telemetry):**
```bash
curl -N -s -X POST http://localhost:8000/v1/execute/stream       -H "Content-Type: application/json"       -H "X-Tenant-Id: tenant-123"       -H "X-Correlation-Id: 1e98c5b6-..."       -H "X-Request-Id: 2d41b8d1-..."       -H "X-Timestamp: $(date -u +%Y-%m-%dT%H:%M:%SZ)"       -H "X-Telemetry: basic"       -d @sample_ir_and_input.json
```

**Subscribe to telemetry stream:**
```bash
curl -N -s "http://localhost:8000/v1/telemetry/stream?runId=RUN_ID_HERE"       -H "X-Tenant-Id: tenant-123" -H "X-Telemetry: verbose"
```

---

## Tests
- `tests/api/test_execute_stream.py` validates:
  - SSE framing with `response.created`, `response.output_text.delta`, `response.completed`
  - Telemetry header exposed when `X-Telemetry != none`
  - 400 on missing `X-Tenant-Id` or tenant mismatch with `ir.meta.tenantId`
- `tests/runtime/test_run_stream.py` validates engine adapter yields well-formed events and completes.
- Coverage enforced via `pytest --cov=app --cov-report=term-missing` (CI gate ≥ 80%).

---

## Acceptance Criteria
1. `POST /v1/execute` returns final JSON including `runId`, `usage`, and `output_text` when applicable.
2. `POST /v1/execute/stream` emits **Responses API–compatible** SSE (`response.*`) and cleanly terminates with `response.completed`.
3. `X-Telemetry` header switches telemetry off or exposes `X-Telemetry-Stream-Url` when enabled.
4. Headers `X-Tenant-Id`, `X-Correlation-Id`, `X-Request-Id`, `X-Timestamp`, `X-Client-Id` propagate to LLM/tool clients.
5. PII redaction applied to telemetry when enabled globally or per-node.
6. OpenAPI docs are updated and visible at `/docs`.
7. Unit tests pass and coverage ≥ 80%.

---

## Validation
- Run `uv run pytest -q --cov=app` and ensure ≥ 80% coverage.
- Run the server (F5 in VS Code) and execute the curl examples.
- Verify that ChatKit renders the streamed text and that no parsing errors occur.
- Kill the client mid-stream; server should exit generator gracefully (no stack traces).

---

## Manual TODOs (pre/post PR)
- Ensure `.env` has valid Apigee/Gemini settings:
  - `APIGEE_TOKEN_URL`, `APIGEE_CLIENT_ID`, `APIGEE_CLIENT_SECRET`, `APIGEE_AUDIENCE`
  - `OPENAI_BASE_URL` (Gemini OpenAI-compatible gateway via Apigee)
- Add/confirm `schemas/orchestration_ir.schema.json` exists (from PR-01) and stays in sync.
- Optionally set `CORS_ALLOWED_ORIGINS` to your ChatKit host(s).
- Confirm `LOG_LEVEL` (e.g., `INFO` in dev, `WARNING`/`ERROR` in prod) and `LOG_REDACTION_ENABLED=true` in regulated envs.

---

## Key Tenets (append to all PRs)
- Ensure code is **clean, readable, and maintainable**; prefer clarity over cleverness.
- Favor **async I/O** and efficient concurrency where it improves real latency.
- Keep dependencies **minimal** and **version-pinned** where stability matters.
- Design for **extensibility**: new orchestration nodes and providers plug in without major refactors.
- Provide **great tests**: fast, isolated, and meaningful (≥80% coverage).
- Log **structured** events with correlation IDs; never log secrets; redact PII when enabled.
- Fail **gracefully** with actionable error messages and consistent error envelopes.
- Keep **OpenAPI docs** accurate and example-backed; debugging starts with docs.
- Make **configuration explicit** (.env first; injectable for tests).
- Align with **OpenAI Responses streaming** semantics for maximum UI compatibility.
```

---

## Notes for Codex
- It is acceptable in this PR to use a mock streaming adapter that synthesizes `response.output_text.delta` from a final string; provider-specific adapters will land in a later PR.
- Ensure SSE frames use `event:` **matching the `type` field** of our internal events and that `data:` is valid JSON per frame.
- Keep SSE line lengths reasonable; flush promptly and send periodic `ping`s.
