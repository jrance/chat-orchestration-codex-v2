# PROMPT FOR CODEX

Implement the following PR and validate that everything has been completed fully and that all tests pass and all acceptance criteria is met. Feel free to modify the recommended code if there is a better and cleaner approach.

---

# PR-14 of 14 — HITL Resume, Persistence Polish, and OpenAPI Docs

## PR Title
Finalize Human‑in‑the‑Loop (HITL) pause/resume flows, production‑ready checkpointer/state store, and OpenAPI documentation (PR 14 of 14)

## Description
This PR completes phase‑1 by wiring **pause/resume** for Human‑in‑the‑Loop scenarios, hardening the **checkpointer/state store**, and publishing a high‑fidelity **OpenAPI 3.0** spec. It ensures that long‑running or multi‑turn orchestrations can be **paused** (e.g., router “AskUserClarify”, moderation gate) and **resumed** via `POST /v1/execute/{runId}/resume` with consistent **thread/run IDs**, durable **graph state**, and predictable **SSE** semantics aligned with OpenAI **Responses** streaming.

## Purpose
- Enable robust HITL experiences with clear pause metadata and deterministic resumption.
- Make state durable across process restarts with a swappable checkpointer interface (in‑memory default; backends pluggable).
- Provide a precise API contract via OpenAPI at `/docs` and `/openapi.json`.

## Scope
- API: add/complete `POST /v1/execute/{runId}/resume`, status/read APIs, error taxonomy, headers.
- Runtime: state model, checkpointer interface, in‑memory store, resume integration with router/concurrent/groupchat.
- SSE: finalize event shapes; ensure telemetry stream split remains intact.
- OpenAPI: schemas for requests/responses; examples; header documentation.
- Tests: pause/resume flows, idempotency, state recovery, OpenAPI validation.

---

## Files to Add / Modify

```
app/
├─ runtime/state/
│  ├─ __init__.py
│  ├─ models.py                     # RunState, Checkpoint, Status enums
│  ├─ checkpointer.py               # interface + factory
│  ├─ backends/
│  │  ├─ __init__.py
│  │  ├─ memory.py                  # default in‑proc (thread‑safe), TTL/pruning
│  │  └─ redis.py                   # thin adapter (stub impl + tests via fakeredis)
├─ runtime/engine.py                # MOD: persist/restore; implement pause->resume
├─ runtime/patterns/router.py       # MOD: emit pause metadata for AskUserClarify
├─ runtime/patterns/concurrent.py   # MOD: resumable checkpoints (budget/tokens left)
├─ runtime/patterns/groupchat.py    # MOD: resumable transcript; next‑speaker pointer
├─ api/routes/execute.py            # MOD: implement POST /v1/execute/{runId}/resume + GET /v1/runs/{runId}
├─ api/schemas.py                   # NEW: pydantic models for RunRequest, ResumeRequest, RunStatus, ErrorEnvelope
├─ docs/openapi_overrides.py        # NEW: tags, examples, and SSE hints
├─ telemetry/events.py              # MOD: add pause/resume event constants
├─ config.py                        # MOD: checkpointer backend selection + TTL
tests/
├─ api/test_resume_router_hitl.py
├─ api/test_resume_groupchat.py
├─ runtime/test_checkpointer_memory.py
├─ runtime/test_state_recovery.py
├─ api/test_openapi_contract.py
docs/
└─ HITL_AND_PERSISTENCE.md          # NEW: pause metadata, resume payloads, state diagram
```

---

## API Contract (OpenAPI 3.0)

### Headers (all endpoints)
- `Authorization: Bearer <token>` — from Apigee OAuth.
- `X-Request-ID` — request GUID; echoed in logs/telemetry.
- `X-Correlation-ID` — workflow correlation; forwarded downstream.
- `X-Tenant-ID` — **required** tenant id (propagated to tools/providers).
- `X-Telemetry: none|basic|verbose` — streaming verbosity (see PR‑06).
- `X-Timestamp` — RFC3339; optional.

### `POST /v1/execute`
Starts a new run.
- **Body**: `RunRequest`
  - `orchestration`: Orchestration IR JSON
  - `input`: initial user text or message array
  - `threadId` (optional): caller‑provided thread
- **Response 201**: `RunStatus`
  - `runId`, `threadId`, `status: running|paused|completed|error`
  - `sse: { url: "/v1/execute/stream?runId=..." }`

### `POST /v1/execute/stream`
SSE stream of the active run.
- **Query**: `runId`
- **Events**: Responses taxonomy (`response.created`, `response.output_text.delta`, `response.completed`, `response.error`) and tool events; parallel **telemetry** events if `X-Telemetry ≠ none`.
- **Close**: on `response.completed` or `response.error`.

### `POST /v1/execute/{runId}/resume`
Resumes a **paused** run.
- **Body**: `ResumeRequest`
  - `kind`: `"user_message" | "router_choice" | "moderation_ack" | "continue"`
  - `message` (optional): user text when `kind="user_message"`
  - `choice` (optional): `{ target: "<label or id>" }` when `kind="router_choice"`
  - `metadata` (optional): free‑form dict for advanced flows
- **Responses**:
  - `200 RunStatus` with `status: running` and an `sse.url`
  - `409 ErrorEnvelope` if run not paused
  - `404 ErrorEnvelope` if unknown run

### `GET /v1/runs/{runId}`
Fetch run status.
- **Response 200**: `RunStatus`

### Error envelope
```json
{ "error": { "code": "ROUTER_NO_ROUTE", "message": "No eligible target", "details": {...} } }
```

> OpenAPI: populate `components.schemas` for `RunRequest`, `ResumeRequest`, `RunStatus`, `ErrorEnvelope` with examples; document headers under `components.parameters` and reference them in paths.

---

## Runtime State & Checkpointing

### `app/runtime/state/models.py`
```python
from __future__ import annotations
from typing import TypedDict, Literal, Any
from datetime import datetime

RunStatus = Literal["queued","running","paused","completed","error"]

class Checkpoint(TypedDict, total=False):
    id: str
    step: int
    graph_state: dict
    created_at: str  # isoformat
    meta: dict       # nodeId, labels, budget, etc.

class RunState(TypedDict, total=False):
    runId: str
    threadId: str
    orchestrationId: str
    tenantId: str
    status: RunStatus
    lastCheckpoint: Checkpoint | None
    created_at: str
    updated_at: str
    error: dict | None
```

### `app/runtime/state/checkpointer.py`
- Interface:
  - `create_run(thread_id, orchestration_id, tenant_id) -> RunState`
  - `get_run(run_id) -> RunState | None`
  - `save_checkpoint(run_id, graph_state: dict, step: int, meta: dict) -> Checkpoint`
  - `mark_status(run_id, status, error=None) -> RunState`
  - `resume_from_checkpoint(run_id) -> tuple[RunState, dict]`  *(returns state + graph_state)*
- Factory reads `.env`: `CHECKPOINTER_BACKEND=memory|redis`, `STATE_TTL_SEC`, `STATE_MAX_BYTES` (soft cap).

### Memory backend
- Thread‑safe dict + `asyncio.Lock`.
- TTL pruning on write/read (store `expires_at` on run and checkpoint rows).
- Enforce `STATE_MAX_BYTES` with a soft warning; do not crash.

### Redis backend (stub)
- Implement same interface using `redis.asyncio` or `fakeredis` for tests.
- Use keys: `runs:{runId}`, `ckpt:{runId}:{step}`.
- Optional `STREAM` for audit is **not** in scope (later phase).

---

## Pause/Resume Semantics

- **Pause points** (at least):
  - Router with `fallback.mode = AskUserClarify` → engine sets `status=paused` and returns `response.completed` with:
    ```json
    { "metadata": { "hitl": { "status":"awaiting_user_input", "reason":"router.ask_user", "targets":["Policy Agent","M365 Agent"] } } }
    ```
  - GroupChat maxTurns reached without satisfaction (optionally) → `awaiting_user_input` with `reason:"groupchat.more_context"`.
- **Resume kinds**:
  - `user_message`: adds a user message to the transcript/context and continues where paused.
  - `router_choice`: caller supplies `{target}` to override routing and execution continues at that child.
  - `moderation_ack`: caller acknowledges a moderation warning and instructs to proceed with safe path.
  - `continue`: no extra input; simply unpause (useful for automated retries).
- **Idempotency**: `resume` stores a new checkpoint (`step += 1`) before re‑entering the graph to avoid duplicate side effects on retries.

---

## SSE Events (final polish)
- Maintain **Responses** stream fidelity:
  - `response.created`
  - `response.output_text.delta` / `response.output_text.done`
  - `response.tool_call.created` / `.arguments.delta` / `.done`
  - `response.tool_result.created` / `.done`
  - `response.completed`
  - `response.error`
- Telemetry stream mirrors prior PRs (`telemetry.*`) with `runId`, `nodeId`, `tenantId`, and `labels`.

---

## Tests

### `tests/api/test_resume_router_hitl.py`
- Start a run that pauses at Router (`AskUserClarify`).
- Assert `status=paused` and `response.completed` metadata includes `hitl` with targets.
- Call `/resume` with `{kind:"router_choice", "choice": {"target":"Policy Agent"}}`.
- Assert run transitions to `running` then completes; verify chosen child executed.

### `tests/api/test_resume_groupchat.py`
- Start GroupChat with `maxTurns=1` and moderator not satisfied → pause.
- Resume with `{kind:"user_message", "message":"Add details about PTO accrual"}}` and ensure one more turn runs and completes.

### `tests/runtime/test_checkpointer_memory.py`
- Save/restore checkpoints; TTL expiry behavior; concurrent access safety.

### `tests/runtime/test_state_recovery.py`
- Simulate process restart by creating new checkpointer instance; ensure run can resume from last checkpoint.

### `tests/api/test_openapi_contract.py`
- Use FastAPI test client to fetch `/openapi.json` and ensure required components/paths/parameters exist.

All tests must be **deterministic** and **not** require network (mock provider calls).

---

## Step‑By‑Step Implementation

1. **State Models/Interface**: add `models.py` and `checkpointer.py` with abstract interface + factory.
2. **Memory Backend**: implement `memory.py` with TTL and size guard; wire `.env` config.
3. **Engine Integration**: at start of each node, call `save_checkpoint`; on pause, call `mark_status(..., "paused")`.
4. **Resume Endpoint**: implement `/v1/execute/{runId}/resume`; validate paused state; persist a resume checkpoint; re‑enter engine with supplied input.
5. **OpenAPI**: add `api/schemas.py` models; attach examples; add tags and SSE notes via `docs/openapi_overrides.py`.
6. **Tests**: add test suites; gate CI on `pytest-cov` ≥80% overall and for changed modules.
7. **Docs**: author `HITL_AND_PERSISTENCE.md` with sequence diagrams and cURL examples.

---

## cURL Examples

Start:
```bash
curl -X POST https://host/v1/execute  -H "Authorization: Bearer $TOKEN" -H "X-Tenant-ID: demo"  -H "Content-Type: application/json"  -d '{ "orchestration": { ...IR... }, "input": "What is our PTO policy?" }'
```

Stream:
```bash
curl -N "https://host/v1/execute/stream?runId=RUN123"  -H "Authorization: Bearer $TOKEN" -H "X-Telemetry: basic"
```

Resume (router choice):
```bash
curl -X POST https://host/v1/execute/RUN123/resume  -H "Authorization: Bearer $TOKEN" -H "X-Tenant-ID: demo"  -H "Content-Type: application/json"  -d '{ "kind":"router_choice", "choice": { "target":"Policy Agent" } }'
```

---

## Acceptance Criteria
- [ ] `/v1/execute/{runId}/resume` implemented with validation and idempotency.
- [ ] Engine persists checkpoints before/after nodes; state survives process restarts.
- [ ] Router/GroupChat can pause and resume using defined kinds.
- [ ] SSE stream adheres to Responses taxonomy; telemetry remains separate.
- [ ] OpenAPI includes headers, schemas, and examples for execute/stream/resume/status.
- [ ] Unit tests pass; coverage ≥ 80% on new/modified modules.
- [ ] Documentation (`HITL_AND_PERSISTENCE.md`) provides end‑to‑end guidance.

---

## Manual Pre/Post Items
- `.env`: set `CHECKPOINTER_BACKEND=memory` (or `redis` later), `STATE_TTL_SEC=86400`, `STATE_MAX_BYTES=5242880`.
- If using Redis later, provide `REDIS_URL` and enable the backend in `config.py` (this PR ships a stub + tests via `fakeredis` only).
- Ensure your deployment/plugin runner exposes `/docs` and `/openapi.json` (FastAPI default).

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
