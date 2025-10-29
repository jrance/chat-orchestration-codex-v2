# PROMPT FOR CODEX

Implement the following PR and validate that everything has been completed fully and that all tests pass and all acceptance criteria is met. Feel free to modify the recommended code if there is a better and cleaner approach.

---

# PR-07 of 14 — Pluggable Checkpointer & In‑Memory Run State Store

## PR Title
Add swappable LangGraph checkpointer + in‑memory run state store and wire into compiler (PR 7 of 14)

## Description
Introduce a **pluggable checkpointing layer** and a **run state store** so executions can persist/restore state across process restarts and support **resume/retry** semantics. For phase‑1:

- Provide a `Checkpointer` abstraction that returns a **LangGraph‑compatible saver** (e.g., MemorySaver).  
- Implement an **InMemoryCheckpointer** (default) and a factory to resolve from settings/env; structure it so a Redis/SQL variant can drop in later without touching call sites.
- Provide a `RunStateStore` abstraction used by our **engine** to persist the final/partial state and simple telemetry; implement an **InMemoryRunStateStore**.
- Update the **GraphBuilder** to compile graphs with the active checkpointer.
- Add a minimal **Engine** that executes a compiled graph by `graph_id` and `run_id`, persists the resulting state into the store, and supports **resume** by merging new inputs and re‑invoking the graph with the same `run_id`.
- Add tests verifying: (a) checkpointer wiring, (b) state store round‑trip, (c) resume accumulates messages deterministically.

> NOTE: HTTP endpoints for `/v1/execute` and `/v1/execute/:runId/resume` will be fully implemented in PR‑08/09 alongside streaming/SSE. This PR focuses on the **runtime primitives** and programmatic execution helpers.

## Purpose
- Establish a clean, **swappable** persistence boundary for both LangGraph checkpoints and our higher‑level run state.
- Make resume/retry reasonable today while allowing us to swap in Redis/SQL without refactoring the compiler or engine.

## Scope
- New `runtime/checkpointer/` package with base + in‑memory + factory.
- New `runtime/state_store/` package with base + in‑memory.
- New `runtime/engine.py` exposing `execute_once` and `resume_run` helpers.
- Modify `compiler/builder.py` to accept a saver from the active checkpointer during `compile()`.
- Tests for checkpointer selection, store round‑trip, and engine resume behavior.

## Goals
- Default to **in‑memory** but keep the API storage‑agnostic.
- Keep all public surfaces typed and minimal.
- ≥80% test coverage on added modules.

---

## Files to Add / Change

```
app/
├─ runtime/
│  ├─ __init__.py
│  ├─ engine.py
│  ├─ checkpointer/
│  │  ├─ __init__.py
│  │  ├─ base.py
│  │  ├─ memory.py
│  │  └─ factory.py
│  └─ state_store/
│     ├─ __init__.py
│     ├─ base.py
│     └─ memory.py
├─ compiler/
│  └─ builder.py            # (modified: compile with saver from factory)
├─ config/settings.py       # (extend with CHECKPOINTER_KIND, RUN_STORE_KIND)
tests/
├─ test_checkpointer_factory.py
├─ test_state_store_memory.py
└─ test_engine_resume.py
.env.example                # (append)
AGENTS.md                   # (append: persistence overview & extension points)
```

### `.env.example` (append)
```
# Checkpointer selection: memory | (redis|sql in future)
CHECKPOINTER_KIND=memory

# Run state store selection: memory | (redis|sql in future)
RUN_STORE_KIND=memory
```

### `app/config/settings.py` (extend)
```python
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    # ...existing...
    checkpointer_kind: str = "memory"
    run_store_kind: str = "memory"

    model_config = SettingsConfigDict(env_file=".env", env_prefix="", extra="ignore")

settings = Settings()
```

### `app/runtime/checkpointer/base.py`
```python
from __future__ import annotations
from typing import Protocol, Any

class LangGraphSaver(Protocol):
    """A minimal protocol for LangGraph saver/checkpointer objects."""
    # LangGraph saver objects are passed directly into graph.compile(checkpointer=saver)

class Checkpointer(Protocol):
    """Factory interface for providing a LangGraph-compatible saver."""
    def get_saver(self) -> LangGraphSaver: ...
```

### `app/runtime/checkpointer/memory.py`
```python
from __future__ import annotations
from typing import Any
try:
    # LangGraph >=1.0
    from langgraph.checkpoint import MemorySaver  # type: ignore
except Exception:  # pragma: no cover - unit tests can still import the module without langgraph installed
    MemorySaver = object  # sentinel

from .base import Checkpointer, LangGraphSaver

class InMemoryCheckpointer(Checkpointer):
    def __init__(self) -> None:
        self._saver = MemorySaver() if MemorySaver is not object else None

    def get_saver(self) -> LangGraphSaver:
        if self._saver is None:
            raise RuntimeError("LangGraph MemorySaver unavailable; install langgraph 1.x")
        return self._saver  # type: ignore[return-value]
```

### `app/runtime/checkpointer/factory.py`
```python
from __future__ import annotations
from app.config.settings import settings
from .base import Checkpointer
from .memory import InMemoryCheckpointer

def get_checkpointer() -> Checkpointer:
    kind = (settings.checkpointer_kind or "memory").lower()
    if kind == "memory":
        return InMemoryCheckpointer()
    # Future: redis/sql
    raise ValueError(f"Unknown CHECKPOINTER_KIND '{kind}'")
```

### `app/runtime/state_store/base.py`
```python
from __future__ import annotations
from typing import Protocol, Any, Dict, Optional

class RunStateStore(Protocol):
    def put_state(self, run_id: str, state: Dict[str, Any]) -> None: ...
    def get_state(self, run_id: str) -> Optional[Dict[str, Any]]: ...
    def clear(self, run_id: str) -> None: ...
```

### `app/runtime/state_store/memory.py`
```python
from __future__ import annotations
from typing import Dict, Any, Optional
from threading import RLock
from .base import RunStateStore

class InMemoryRunStateStore(RunStateStore):
    def __init__(self) -> None:
        self._data: Dict[str, Dict[str, Any]] = {}
        self._lock = RLock()

    def put_state(self, run_id: str, state: Dict[str, Any]) -> None:
        with self._lock:
            self._data[run_id] = dict(state)

    def get_state(self, run_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            s = self._data.get(run_id)
            return dict(s) if s is not None else None

    def clear(self, run_id: str) -> None:
        with self._lock:
            self._data.pop(run_id, None)
```

### `app/runtime/engine.py`
```python
from __future__ import annotations
from typing import Dict, Any, Optional
from app.compiler.registry import get as get_compiled_graph
from app.runtime.checkpointer.factory import get_checkpointer
from app.runtime.state_store.memory import InMemoryRunStateStore  # default
from app.compiler.types import OrchestratorState

# Simple singleton store for phase-1 (swappable in future via factory if needed)
_RUN_STORE = InMemoryRunStateStore()

def _merge_inputs(prev: Dict[str, Any] | None, new: Dict[str, Any] | None) -> Dict[str, Any]:
    prev = prev or {}
    new = new or {}
    merged = dict(prev)
    # Merge messages if present
    pm, nm = prev.get("messages") or [], new.get("messages") or []
    merged["messages"] = [*pm, *nm]
    # Merge top-level input dicts
    pi, ni = prev.get("input") or {}, new.get("input") or {}
    merged["input"] = {**pi, **ni}
    # Shallow merge scratch
    ps, ns = prev.get("scratch") or {}, new.get("scratch") or {}
    merged["scratch"] = {**ps, **ns}
    return merged

def execute_once(graph_id: str, run_id: str, state_in: Dict[str, Any] | None = None) -> Dict[str, Any]:
    app = get_compiled_graph(graph_id)
    if app is None:
        raise ValueError(f"Unknown graph_id '{graph_id}'")

    prev = _RUN_STORE.get_state(run_id)
    initial = _merge_inputs(prev, state_in)

    # Use LangGraph's thread_id convention so checkpointer persists across steps
    config = {"configurable": {"thread_id": run_id}}

    out: OrchestratorState = app.invoke(initial, config=config)  # type: ignore
    _RUN_STORE.put_state(run_id, out)
    return out

def resume_run(graph_id: str, run_id: str, state_in: Dict[str, Any] | None = None) -> Dict[str, Any]:
    # Resuming is just executing again with prior state merged in
    return execute_once(graph_id, run_id, state_in)
```

### `app/compiler/builder.py` (modify `build()` to include saver)
```python
# ... existing imports ...
from app.runtime.checkpointer.factory import get_checkpointer

class GraphBuilder:
    # ... existing code ...

    def build(self):
        self.compile_nodes()
        self.compile_edges()
        self.g.set_entry_point(self.entry_id)
        saver = get_checkpointer().get_saver()
        return self.g.compile(checkpointer=saver)
```

---

## Tests

### `tests/test_checkpointer_factory.py`
```python
from app.runtime.checkpointer.factory import get_checkpointer

def test_factory_returns_memory_saver():
    cp = get_checkpointer()
    saver = cp.get_saver()
    assert saver is not None
```

### `tests/test_state_store_memory.py`
```python
from app.runtime.state_store.memory import InMemoryRunStateStore

def test_round_trip_state():
    store = InMemoryRunStateStore()
    store.put_state("r1", {"messages":[{"role":"user","content":"hi"}], "input":{"x":1}})
    got = store.get_state("r1")
    assert got and got["input"]["x"] == 1
    store.clear("r1")
    assert store.get_state("r1") is None
```

### `tests/test_engine_resume.py`
```python
import time, pytest
from app.ir.loader import build_runtime_plan
from app.compiler.builder import GraphBuilder
from app.compiler.registry import put as put_graph
from app.runtime.engine import execute_once, resume_run

def pkg_two_agents():
    a1 = {"id":"a1","kind":"agent.codeless","label":"A1",
          "data":{"systemInstructions":"Step1","model":{"provider":"openai","modelId":"gpt-4o","temperature":0.1,"topP":1,"maxTokens":64},
                  "context":{"historyWindow":{"mode":"LastN","n":3}},"tools":{"policy":"Disabled","attached":[]}}}
    a2 = {"id":"a2","kind":"agent.codeless","label":"A2",
          "data":{"systemInstructions":"Step2","model":{"provider":"openai","modelId":"gpt-4o","temperature":0.1,"topP":1,"maxTokens":64},
                  "context":{"historyWindow":{"mode":"LastN","n":3}},"tools":{"policy":"Disabled","attached":[]}}}
    return {"meta":{"id":"m","name":"x","version":"1.0.0"}, "nodes":[a1,a2],
            "edges":[{"id":"e1","from":"a1","to":"a2"}], "entryId":"a1"}

@pytest.mark.anyio
async def test_resume_accumulates_messages():
    ok, plan, errs = build_runtime_plan(pkg_two_agents(), {})
    assert ok, errs
    app = GraphBuilder(plan).build()
    gid = "g-test"
    put_graph(gid, app)

    run_id = "run-1"
    # first turn
    out1 = execute_once(gid, run_id, {"messages":[{"role":"user","content":"hello","ts": time.time()}]})
    assert len(out1.get("messages", [])) >= 2  # assistant messages appended

    # second turn (resume)
    out2 = resume_run(gid, run_id, {"messages":[{"role":"user","content":"again","ts": time.time()}]})
    # expect more messages appended (>= previous)
    assert len(out2.get("messages", [])) >= len(out1.get("messages", []))
```

---

## Step‑By‑Step Implementation

1. **Settings & env**
   - Add `CHECKPOINTER_KIND` and `RUN_STORE_KIND` with default `memory`.

2. **Checkpointer abstraction**
   - Define a minimal `Checkpointer` interface returning a LangGraph saver.
   - Implement `InMemoryCheckpointer` using `langgraph.checkpoint.MemorySaver`.

3. **Factory**
   - `get_checkpointer()` reads `settings.checkpointer_kind` and returns the appropriate implementation.

4. **Run state store**
   - Introduce `RunStateStore` protocol and `InMemoryRunStateStore` for final state persistence.

5. **Engine helpers**
   - Implement `execute_once(graph_id, run_id, state_in)` and `resume_run(...)`:
     - Merge prior stored state with new inputs.
     - Invoke the compiled app with `configurable.thread_id = run_id`.
     - Persist the resulting state via the store.

6. **Wire compiler to checkpointer**
   - Update `GraphBuilder.build()` to compile the graph with `checkpointer=get_checkpointer().get_saver()`.

7. **Tests**
   - Validate factory returns a saver, run store roundtrip works, and resume appends messages across turns.

---

## Acceptance Criteria
- [ ] `GraphBuilder.build()` compiles with the active checkpointer (default: **MemorySaver**).
- [ ] `get_checkpointer()` resolves based on `CHECKPOINTER_KIND` (default `memory`).
- [ ] `RunStateStore` exists with an in‑memory implementation used by the engine.
- [ ] `execute_once()` and `resume_run()` merge state correctly and persist outputs.
- [ ] Tests pass with **≥80%** coverage across new runtime modules.
- [ ] Clear documentation in `AGENTS.md` on how to implement alternative backends (Redis/SQL).

## Validation
```bash
pytest -q
python - <<'PY'
from app.runtime.checkpointer.factory import get_checkpointer
print("Saver:", type(get_checkpointer().get_saver()).__name__)
PY
```

---

## Manual Pre/Post Items
- **Install LangGraph** if you haven’t already (`langgraph>=1.0.0`).  
- **No external infra yet:** This PR uses in‑memory implementations only; keep in mind state will be lost on process restarts.  
- **Plan for prod:** When ready, add Redis/SQL implementations that satisfy the same protocols; no compiler/engine changes should be necessary.

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
