
# PROMPT FOR CODEX

Implement the following **Hotfix PR** and validate that everything has been completed fully and that all tests pass and all acceptance criteria is met. Feel free to modify the recommended code if there is a better and cleaner approach.

---

# PR-014b — Hotfix: Fix Failing Tests, Restore Factory Precedence, Resume Coverage ≥80%

## PR Title
Hotfix PR for PR‑014a: align token usage expectations, restore checkpointer factory precedence and return type, fix duplicate test and validation outcome, and add positive resume tests to get coverage ≥80%

## Why
Codex review flagged these issues after PR‑014a:
1) **Token usage mismatch:** `RunStatus.usage["output_tokens"]` now equals **2**, but tests assert **3**.  
2) **Checkpointer factory precedence/return contract:** `get_checkpointer()` always prefers settings and returns a `CheckpointManager`, breaking existing tests that expect env precedence and an `InMemoryCheckpointer` instance (and `ValueError` on unknown kind).  
3) **Duplicate test name / validation change:** Two `test_resume_missing_run` functions; the second actually validates missing `kind` and now correctly returns **422**.  
4) **Coverage < 80%:** Positive resume path tests were removed; new surface area lacks coverage.

This hotfix resolves all four and restores the green suite with coverage ≥80%.

---

## Scope of Changes

```
app/
├─ runtime/state/checkpointer.py                # FIX: env var precedence & return type; raise on unknown kind
├─ api/v1/routes_execute.py                     # (no behavior change here; included if small param aliasing needed)
tests/
├─ api/test_execute_stream.py                   # FIX: align token expectation (2)
├─ api/test_execute_stream_trio.py              # FIX: align token expectation (2) if present
├─ test_checkpointer_factory.py                 # FIX: asserts for env precedence + ValueError restored
├─ test_v1_routes_smoke.py                      # FIX: rename duplicate test; assert 422 for missing kind
├─ test_engine_resume.py                        # NEW: positive resume tests (router_choice & user_message)
```

> If your tree paths differ slightly, adapt the filenames in this PR accordingly.

---

## Implementation Details

### 1) Token usage alignment
**Change** the hard-coded token expectation in the stream tests from **3** to **2** to match the new adapter accounting (deltas only; no “done” counted).

```diff
- assert status["usage"]["output_tokens"] == 3
+ assert status["usage"]["output_tokens"] == 2
```

Apply the same fix to the **trio** variant test if present.

> Rationale: The PR‑09/14a adapter now counts only `response.output_text.delta` chunks as output tokens in tests; previous assertion included an extra unit from an earlier counting scheme.

---

### 2) Checkpointer factory precedence & return type
Restore the previous behavior for **backward compatibility**:

- **Precedence**: Environment variable `CHECKPOINTER_KIND` (legacy name) **overrides** code settings.  
  Fallback to `settings.CHECKPOINTER_BACKEND` when env is absent.
- **Return type**: Return an instance of the concrete backend (`InMemoryCheckpointer` or `RedisCheckpointer`) rather than a wrapper/manager so existing tests continue to `isinstance()` check.
- **Unknown kind**: If env specifies an unknown value, raise `ValueError("Unknown checkpointer kind: ...")`.

Suggested implementation (adjust to your module names):

```python
# app/runtime/state/checkpointer.py (excerpt)

import os

_CACHED = None

def get_checkpointer():
    global _CACHED
    if _CACHED:
        return _CACHED
    kind = os.getenv("CHECKPOINTER_KIND")  # legacy env has precedence
    from app.config import settings
    if not kind:
        kind = getattr(settings, "CHECKPOINTER_BACKEND", "memory")
    kind = (kind or "memory").lower()
    if kind == "memory":
        from .backends.memory import MemoryCheckpointer as InMemoryCheckpointer
        _CACHED = InMemoryCheckpointer(ttl_sec=settings.STATE_TTL_SEC)
    elif kind == "redis":
        from .backends.redis import RedisCheckpointer
        _CACHED = RedisCheckpointer(url=settings.REDIS_URL)
    else:
        raise ValueError(f"Unknown checkpointer kind: {kind}")
    return _CACHED
```

And **tests/test_checkpointer_factory.py**:

```diff
- # expecting CheckpointManager and settings precedence
+ # expect env precedence and concrete type
  os.environ["CHECKPOINTER_KIND"] = "memory"
  cp = get_checkpointer()
- assert isinstance(cp, CheckpointManager)
+ from app.runtime.state.backends.memory import MemoryCheckpointer as InMemoryCheckpointer
+ assert isinstance(cp, InMemoryCheckpointer)

  os.environ["CHECKPOINTER_KIND"] = "unknown"
  with pytest.raises(ValueError):
      get_checkpointer()
```

> Note: Clear `_CACHED` between tests (`monkeypatch.delenv` or reimport module) to avoid leakage.

---

### 3) Duplicate test name & validation result
Rename the second function and assert **422 Unprocessable Entity** due to Pydantic validation:

```diff
- def test_resume_missing_run(client):
+ def test_resume_missing_kind_returns_422(client):

- resp = client.post("/v1/execute/ABC/resume", json={})
- assert resp.status_code == 400
+ resp = client.post("/v1/execute/ABC/resume", json={})
+ assert resp.status_code == 422
```

> Keep the original `test_resume_missing_run` focused on **404** when the run doesn’t exist (with a *valid* body).

---

### 4) Positive resume tests (restore coverage)
Add **tests/test_engine_resume.py** that exercises both a **router_choice** resume and a **user_message** resume on a paused run.

Example outline (pseudo‑code; adapt to your engine helpers):

```python
import pytest
from app.runtime.state.checkpointer import get_checkpointer
from app.runtime.engine import resume_run, start_run  # your entrypoints

@pytest.mark.asyncio
async def test_resume_router_choice_happy_path(monkeypatch):
    # Arrange: create paused run with router pause metadata
    cp = get_checkpointer()
    state = await cp.create_run("T1","ORCH1","tenantA")
    # simulate a checkpoint at router with pause
    await cp.save_checkpoint(state["runId"], graph_state={"paused_at":"router","pending_router_targets":["Policy Agent","M365 Agent"]}, step=1, meta={"reason":"router.ask_user"})
    await cp.mark_status(state["runId"], "paused")

    # Act: resume with explicit choice
    result = await resume_run(run_id=state["runId"], resume_kind="router_choice", payload={"choice":{"target":"Policy Agent"}}, tenant_id="tenantA", request_id=None, correlation_id=None)

    # Assert
    assert result["status"] in ("running","completed")
    # optionally: assert chosen path recorded in metadata

@pytest.mark.asyncio
async def test_resume_user_message_happy_path(monkeypatch):
    cp = get_checkpointer()
    state = await cp.create_run("T1","ORCH1","tenantA")
    await cp.save_checkpoint(state["runId"], graph_state={"paused_at":"groupchat","messages":[]}, step=2, meta={"reason":"groupchat.more_context"})
    await cp.mark_status(state["runId"], "paused")

    result = await resume_run(run_id=state["runId"], resume_kind="user_message", payload={"message":"Clarify PTO accrual"}, tenant_id="tenantA", request_id=None, correlation_id=None)
    assert result["status"] in ("running","completed")
```

These should be **deterministic** and **mock** any provider calls (LLM, tools) the engine might perform on resume.

---

## Acceptance Criteria
- [ ] Token usage tests updated (2 not 3) or accounting restored to make tests pass.  
- [ ] `get_checkpointer()` honors env precedence, returns concrete backend instances, and raises `ValueError` on unknown kind; factory tests updated accordingly.  
- [ ] Duplicate test name resolved; missing‑kind case asserts **422**.  
- [ ] Resume positive tests added and passing; overall coverage ≥ **80%**.  

---

## Validation
```bash
uv run pytest -q --cov=app --cov-report=term-missing
```

> Tip: if coverage is near the threshold, add small targeted tests for resume branches (e.g., `moderation_ack`, `continue`) to push over 80% without flakiness.

---

## Manual Post Items
- If you prefer the **old** token accounting, revert the adapter’s usage accumulator and keep the original test values (3). Otherwise, proceed with **2** and document it in the streaming tests’ comments.
- Add a note in `HITL_AND_PERSISTENCE.md` clarifying the legacy `CHECKPOINTER_KIND` env precedence for compatibility.
