
# PROMPT FOR CODEX

Implement the following **Hotfix PR** and validate that everything has been completed fully and that all tests pass and all acceptance criteria is met. Feel free to modify the recommended code if there is a better and cleaner approach.

---

# PR-014a — Hotfix: Resume Endpoint Wiring, Redis Checkpointer Stub, OpenAPI Wiring, Tests & Docs

## PR Title
Hotfix PR for PR‑14: Implement working `/v1/execute/{runId}/resume`, usable Redis checkpointer stub (fakeredis for tests), wire new schemas into routes & OpenAPI, and add missing tests/docs

## Why
Codex review flagged four gaps in PR‑14:
1) `/v1/execute/{runId}/resume` does not call `engine.resume_run(...)` nor validate payload; paused runs can’t be resumed.  
2) `CHECKPOINTER_BACKEND=redis` immediately raises, preventing stub usage & tests.  
3) New models in `app/api/schemas.py` aren’t wired; OpenAPI remains stale.  
4) No new tests/docs were added; coverage & tenets unmet.

This hotfix closes those gaps.

---

## Scope of Changes

```
app/
├─ api/v1/routes_execute.py                # FIX: wire ResumeRequest/RunStatus and call engine.resume_run
├─ api/schemas.py                          # MOD: ensure Pydantic models + examples; import in routes
├─ docs/openapi_overrides.py               # MOD: register headers/response examples
├─ runtime/engine.py                       # FIX: implement resume_run(run_id, resume_kind, payload, ctx)
├─ runtime/state/checkpointer.py           # MOD: factory supports 'redis' w/ fakeredis fallback for tests
├─ runtime/state/backends/redis.py         # NEW: usable stub over fakeredis/redis-py; no RuntimeError on import
├─ telemetry/events.py                     # MOD: add telemetry.resume.start|end events
tests/
├─ api/test_resume_router_hitl.py          # NEW
├─ api/test_openapi_contract.py            # NEW
├─ runtime/test_checkpointer_memory.py     # NEW
├─ runtime/test_checkpointer_redis.py      # NEW (uses fakeredis)
docs/
└─ HITL_AND_PERSISTENCE.md                 # MOD: adds resume cURL + state diagrams
```

---

## Implementation Details

### 1) Resume endpoint wiring (app/api/v1/routes_execute.py)

**Before (problem):** handler returns a cached RunStatus without resuming the engine.  
**After (fix):** validate with `ResumeRequest`, call `engine.resume_run(...)`, return updated `RunStatus` and SSE URL.

```python
# app/api/v1/routes_execute.py (excerpt)
from fastapi import APIRouter, Depends, Header, HTTPException
from app.api.schemas import ResumeRequest, RunStatus
from app.runtime.engine import resume_run
from app.runtime.state.checkpointer import get_checkpointer

@router.post("/v1/execute/{runId}/resume", response_model=RunStatus, status_code=200, tags=["execute"])
async def resume_execution(
    runId: str,
    body: ResumeRequest,
    x_tenant_id: str = Header(..., alias="X-Tenant-ID"),
    x_request_id: str | None = Header(None, alias="X-Request-ID"),
    x_correlation_id: str | None = Header(None, alias="X-Correlation-ID"),
):
    cp = get_checkpointer()
    state = await cp.get_run(runId)
    if not state:
        raise HTTPException(status_code=404, detail={"error":{"code":"RUN_NOT_FOUND","message":"Unknown run"}})
    if state.get("status") != "paused":
        raise HTTPException(status_code=409, detail={"error":{"code":"RUN_NOT_PAUSED","message":"Run is not paused"}})

    await cp.save_checkpoint(runId, graph_state=state.get("lastCheckpoint", {}).get("graph_state", {}), step=state.get("lastCheckpoint", {}).get("step", 0) + 1, meta={"resume_kind": body.kind})
    updated = await resume_run(run_id=runId, resume_kind=body.kind, payload=body.dict(exclude_none=True), tenant_id=x_tenant_id, request_id=x_request_id, correlation_id=x_correlation_id)
    return updated  # RunStatus
```

### 2) Engine resume entrypoint (app/runtime/engine.py)

Add `resume_run(...)` that:
- Reloads run state from the checkpointer.
- Applies the resume `kind`:
  - `user_message` → append to conversation and continue where paused.
  - `router_choice` → set next node to provided target and continue.
  - `moderation_ack` / `continue` → clear pause reason and continue.
- Marks status `running` → `completed`/`paused` accordingly.

```python
# app/runtime/engine.py (excerpt)
from app.runtime.state.checkpointer import get_checkpointer

async def resume_run(run_id: str, resume_kind: str, payload: dict, tenant_id: str, request_id: str | None, correlation_id: str | None) -> dict:
    cp = get_checkpointer()
    state = await cp.get_run(run_id)
    # mutate graph_state based on resume_kind
    graph = state["lastCheckpoint"]["graph_state"]
    if resume_kind == "router_choice":
        choice = (payload.get("choice") or {}).get("target")
        graph["pending_router_choice"] = choice
    elif resume_kind == "user_message":
        msg = payload.get("message") or ""
        (graph.setdefault("messages", [])).append({"role":"user","content":msg})
    # clear pause flags and continue
    state = await cp.mark_status(run_id, "running")
    # re-enter execution loop at the paused node
    result = await _resume_engine(graph, tenant_id, request_id, correlation_id)  # your existing loop
    return result  # RunStatus with sse url if applicable
```

> Keep `_resume_engine` thin and reuse your existing execution machinery; ensure a checkpoint is saved before any side-effectful step.

### 3) Redis checkpointer stub (app/runtime/state/backends/redis.py)

Provide a minimal, usable implementation:
- Use `redis.asyncio` if available; otherwise **fallback to fakeredis** for tests when `REDIS_EMULATOR=true` or `REDIS_URL=fakeredis://`.
- Implement the same methods as the memory backend; keys: `runs:{runId}`, `ckpt:{runId}` (hash/json).

```python
# app/runtime/state/backends/redis.py (excerpt)
from __future__ import annotations
import os, json, asyncio
try:
    import redis.asyncio as redis  # type: ignore
except Exception:  # pragma: no cover
    redis = None
try:
    import fakeredis.aioredis as fakeredis  # type: ignore
except Exception:  # pragma: no cover
    fakeredis = None

class RedisCheckpointer:
    def __init__(self, url: str | None = None):
        emulate = (os.getenv("REDIS_EMULATOR","").lower() == "true") or (url or "").startswith("fakeredis://")
        if emulate:
            if not fakeredis:
                raise RuntimeError("fakeredis not installed; cannot emulate Redis")
            self._client = fakeredis.FakeRedis()
        else:
            if not redis:
                raise RuntimeError("redis-py not installed")
            self._client = redis.from_url(url or "redis://localhost:6379/0", decode_responses=True)

    async def get_run(self, run_id: str) -> dict | None:
        data = await self._client.get(f"runs:{run_id}")
        return json.loads(data) if data else None

    async def create_run(self, thread_id: str, orchestration_id: str, tenant_id: str) -> dict:
        # ... create and store JSON (omitted for brevity in this snippet) ...
        pass

    # Implement save_checkpoint, mark_status, resume_from_checkpoint similarly
```

### 4) Checkpointer factory (app/runtime/state/checkpointer.py)

```python
# app/runtime/state/checkpointer.py (excerpt)
_BACKEND = None

def get_checkpointer():
    global _BACKEND
    if _BACKEND:
        return _BACKEND
    from app.config import settings
    if settings.CHECKPOINTER_BACKEND == "redis":
        from .backends.redis import RedisCheckpointer
        _BACKEND = RedisCheckpointer(url=settings.REDIS_URL)
    else:
        from .backends.memory import MemoryCheckpointer
        _BACKEND = MemoryCheckpointer(ttl_sec=settings.STATE_TTL_SEC)
    return _BACKEND
```

### 5) Wire Pydantic models & OpenAPI (app/api/schemas.py + routes + docs/openapi_overrides.py)

- Ensure `RunRequest`, `ResumeRequest`, `RunStatus`, `ErrorEnvelope` are imported and used as `response_model=` and request bodies.
- Add examples and header parameters in `openapi_overrides.py` so `/docs` reflects the new contract.
- Update all endpoints to accept/return these models.

### 6) Tests

- **api/test_resume_router_hitl.py** — deterministic mock where router pauses; resume with `router_choice`; assert 200 and completion.
- **runtime/test_checkpointer_memory.py** — covers create/get/save_checkpoint/mark_status TTL path.
- **runtime/test_checkpointer_redis.py** — set `REDIS_URL=fakeredis://` and `REDIS_EMULATOR=true`; assert identical behavior to memory backend.
- **api/test_openapi_contract.py** — fetch `/openapi.json` and assert presence of paths, headers, and schemas.
- Keep tests network‑free; mock provider calls.

### 7) Docs

- **HITL_AND_PERSISTENCE.md** — add end‑to‑end cURL for pause → resume; include brief sequence diagram.

---

## Acceptance Criteria

- [ ] `POST /v1/execute/{runId}/resume` validates `ResumeRequest`, calls `engine.resume_run`, and resumes actual execution (not just returning cached state).  
- [ ] `CHECKPOINTER_BACKEND=redis` yields a functional stub (fakeredis in tests) without raising on import/config.  
- [ ] Routes use the new `app/api/schemas.py` models; `/openapi.json` exposes the documented headers and response shapes.  
- [ ] New tests pass; overall coverage on changed files ≥ 80%.  
- [ ] Updated docs describe HITL resume and persistence behavior.

---

## Validation

```bash
# Env
set CHECKPOINTER_BACKEND=memory
uv run pytest -q --cov=app --cov-report=term-missing

# Redis stub tests
set CHECKPOINTER_BACKEND=redis
set REDIS_URL=fakeredis://
set REDIS_EMULATOR=true
uv run pytest -q tests/runtime/test_checkpointer_redis.py
```

---

## Manual Pre/Post Items

- If you want real Redis locally, set `CHECKPOINTER_BACKEND=redis` and `REDIS_URL=redis://localhost:6379/0` and install `redis>=5`.  
- For CI, you can rely on `fakeredis` only (no service needed).  
- Confirm `/docs` shows `RunRequest`, `ResumeRequest`, `RunStatus`, and shared headers on **all** execute endpoints.

---

## Tenets Cross‑Check

- Clean, maintainable code; small, focused functions.  
- Async I/O and bounded concurrency where relevant.  
- Strong typing and Pydantic validation for all API surfaces.  
- Minimal dependencies (fakeredis only for tests).  
- API versioning `/v1/*`, OpenAPI accurate.  
- Deterministic tests; ≥80% coverage on changed code.  
- Clear error envelopes; no secrets in logs.  
- Headers propagated; tenant/auth/correlation handled.  
- Extensible, pluggable backends (memory/redis) without refactors.
