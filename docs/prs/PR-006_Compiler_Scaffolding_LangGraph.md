# PROMPT FOR CODEX

Implement the following PR and validate that everything has been completed fully and that all tests pass and all acceptance criteria is met. Feel free to modify the recommended code if there is a better and cleaner approach.

---

# PR-06 of 14 — Compiler Scaffolding → LangGraph StateGraph (Router, Sequential, Concurrent)

## PR Title
Build compiler that converts the normalized IR into a LangGraph StateGraph (PR 6 of 14)

## Description
Create a **compiler** that takes the **normalized plan** (from PR‑04/PR‑05) and constructs a **LangGraph 1.x** `StateGraph` supporting **Router, Sequential, and Concurrent** orchestration kinds (phase‑1 scope). This PR focuses on *graph shape & wiring* — not rich LLM calls or streaming. For agent nodes, provide a **codeless-agent stub** that produces a trivial assistant message so graphs are runnable. Real LLM execution will be wired in later PRs.

Also introduce a minimal **Graph Registry** to store compiled graphs in‑memory by `graph_id`, and extend `/v1/compile` to compile and return a `graph_id` for later execution PRs.

The orchestration schema can be found at schemas\orchestration_ir.schema.json and examples of valid orchestration packages can be found in schemas\examples. Use the examples to ensure that the LangGraph app compiles correctly.

## Purpose
- Establish a clean, extensible compiler surface to translate IR nodes into LangGraph nodes/edges.
- Keep node compilers modular so new orchestration kinds/agents/tools can be added without refactoring.

## Scope
- Compiler package: node compilers for `router`, `sequential`, `concurrent`, and `agent.codeless`.
- Graph builder/registry to return a compiled LangGraph `app`.
- Shared state shape and simple reducers for message aggregation.
- `/v1/compile` updated to compile and register the graph; returns `graph_id` and a tiny summary.
- Tests to validate topology and simple invocation of the compiled app with stubbed agents.

## Goals
- Deterministic wiring: edges must match the IR.
- Modular node compilers (`register_node_compiler(kind, fn)`).
- Runnable graph with a trivial pass through (stub messages) so CI can invoke one path.
- Tests ≥ 80% coverage on compiler modules added in this PR.

---

## Files to Add / Change

```
app/
├─ compiler/
│  ├─ __init__.py
│  ├─ types.py
│  ├─ registry.py
│  ├─ builder.py
│  ├─ nodes/
│  │  ├─ __init__.py
│  │  ├─ agent_codeless.py
│  │  ├─ router.py
│  │  ├─ sequential.py
│  │  └─ concurrent.py
├─ api/
│  └─ v1/
│     └─ routes_compile.py        # (modified to use the compiler and registry)
pyproject.toml                     # (add langgraph dep)
tests/
├─ test_compile_builds.py
└─ test_compile_invoke.py
AGENTS.md                          # (append: compiler overview & extensibility)
```

### `pyproject.toml` (append/modify)
```toml
[project]
dependencies = [
  "fastapi>=0.115",
  "uvicorn[standard]>=0.30",
  "pydantic>=2.7",
  "pydantic-settings>=2.2",
  "httpx>=0.27",
  "typing-extensions>=4.12",
  "structlog>=24.1.0",
  "jsonschema>=4.23.0",
  "langgraph>=1.0.0",
]
```

---

## Implementation

### `app/compiler/types.py`
```python
from __future__ import annotations
from typing import TypedDict, Literal, Any, Dict, List

Role = Literal["system", "user", "assistant", "tool"]

class Message(TypedDict, total=False):
    role: Role
    content: str | dict | list
    ts: float

class OrchestratorState(TypedDict, total=False):
    run_id: str
    input: Dict[str, Any]
    messages: List[Message]
    scratch: Dict[str, Any]
    route: Dict[str, Any]
    result: Dict[str, Any]
```

### `app/compiler/registry.py`
```python
from __future__ import annotations
from typing import Dict, Any

# Super simple in-memory registry for compiled graphs
_registry: Dict[str, Any] = {}

def put(graph_id: str, app: Any) -> None:
    _registry[graph_id] = app

def get(graph_id: str) -> Any | None:
    return _registry.get(graph_id)

def clear() -> None:
    _registry.clear()
```

### `app/compiler/nodes/__init__.py`
```python
from __future__ import annotations
from typing import Callable, Dict, Any

NodeCompiler = Callable[[Any, dict, dict], None]  # (builder, node, plan) -> add nodes/edges

_compilers: Dict[str, NodeCompiler] = {}

def register(kind: str, fn: NodeCompiler) -> None:
    _compilers[kind] = fn

def get(kind: str) -> NodeCompiler | None:
    return _compilers.get(kind)
```

### `app/compiler/nodes/agent_codeless.py`
```python
from __future__ import annotations
from typing import Any
from langgraph.graph import StateGraph
from ..types import OrchestratorState

# A trivial agent node that appends a canned assistant message.
def add_agent_node(g: StateGraph, node_id: str, agent_data: dict, plan: dict) -> None:
    prompt = (plan.get("agentPrompts") or {}).get(node_id, "")

    def agent_fn(state: OrchestratorState) -> OrchestratorState:
        msgs = list(state.get("messages") or [])
        msgs.append({"role":"assistant","content": f"[{node_id}] {prompt[:120]}"})
        return {**state, "messages": msgs}

    g.add_node(node_id, agent_fn)
```

### `app/compiler/nodes/router.py`
```python
from __future__ import annotations
from typing import Any, List
from langgraph.graph import StateGraph
from ..types import OrchestratorState

def add_router_node(g: StateGraph, node_id: str, node: dict, plan: dict) -> None:
    data = node.get("data") or {}
    targets: List[str] = data.get("targets") or []
    min_conf = float(data.get("minConfidence") or 0.0)

    # Stub decision: choose first target; record route (later PR will call LLM)
    def router_fn(state: OrchestratorState) -> OrchestratorState:
        choice = targets[0] if targets else None
        route = {"target": choice, "confidence": 1.0 if choice else 0.0}
        return {**state, "route": route}

    g.add_node(node_id, router_fn)

    def route_edge_selector(state: OrchestratorState) -> str:
        tgt = (state.get("route") or {}).get("target")
        return tgt if tgt else "__END__"

    # Consumers (edges) will be added in builder; we provide a conditional edge hook
    g.add_conditional_edges(node_id, route_edge_selector, {})  # builder will patch mapping
```

### `app/compiler/nodes/sequential.py`
```python
from __future__ import annotations
from langgraph.graph import StateGraph

def add_sequential_node(g: StateGraph, node_id: str, node: dict, plan: dict) -> None:
    # sequential is a logical grouping: we compile children directly and wire edges
    # There is no runtime function; the builder will add an empty passthrough.
    def seq_passthrough(state): return state
    g.add_node(node_id, seq_passthrough)
```

### `app/compiler/nodes/concurrent.py`
```python
from __future__ import annotations
from typing import Dict, Any
from langgraph.graph import StateGraph

def add_concurrent_node(g: StateGraph, node_id: str, node: dict, plan: dict) -> None:
    # For phase-1 scaffolding, concurrent is a passthrough. The builder will fan-out and join.
    def cc_passthrough(state): return state
    g.add_node(node_id, cc_passthrough)
```

### `app/compiler/builder.py`
```python
from __future__ import annotations
from typing import Dict, Any, List, Tuple, Set
from langgraph.graph import StateGraph, END
from .nodes import register, get as get_compiler
from .nodes.agent_codeless import add_agent_node
from .nodes.router import add_router_node
from .nodes.sequential import add_sequential_node
from .nodes.concurrent import add_concurrent_node
from .types import OrchestratorState

# Register built-in compilers
register("agent.codeless", lambda g, n, p: add_agent_node(g, n["id"], n.get("data") or {}, p))
register("router", add_router_node)
register("sequential", add_sequential_node)
register("concurrent", add_concurrent_node)

class GraphBuilder:
    def __init__(self, plan: Dict[str, Any]):
        self.plan = plan
        self.node_by_id: Dict[str, dict] = plan["nodeById"]
        self.edges: List[dict] = plan["edges"]
        self.entry_id: str = plan["entryId"]
        self.g = StateGraph(OrchestratorState)

    def compile_nodes(self) -> None:
        for nid, node in self.node_by_id.items():
            kind = node.get("kind")
            compiler = get_compiler(kind)
            if not compiler:
                # Unknown kinds compile to a no-op node to keep graph buildable
                self.g.add_node(nid, lambda s: s)
                continue
            compiler(self.g, node, self.plan)

    def _outgoing(self) -> Dict[str, List[str]]:
        out: Dict[str, List[str]] = {}
        for e in self.edges:
            out.setdefault(e["from"], []).append(e["to"])
        return out

    def compile_edges(self) -> None:
        outgoing = self._outgoing()

        # Router edges: patch conditional mapping
        for nid, node in self.node_by_id.items():
            if node.get("kind") == "router":
                data = node.get("data") or {}
                targets = data.get("targets") or []
                mapping = {t: t for t in targets}
                # Fallback to END
                self.g.add_conditional_edges(nid, lambda s: (s.get("route") or {}).get("target", "__END__"), mapping)
                # Make sure there are edges from router to each declared target (no-ops otherwise)
                for t in targets:
                    if t not in self.node_by_id:
                        continue
                    # Edge exists implicitly via conditional mapping

        # For all other edges, wire as-is
        for e in self.edges:
            src, dst = e["from"], e["to"]
            if dst not in self.node_by_id:
                continue
            self.g.add_edge(src, dst)

        # Any node without outgoing edges should end
        for nid in self.node_by_id.keys():
            if nid not in outgoing:
                self.g.add_edge(nid, END)

    def build(self):
        self.compile_nodes()
        self.compile_edges()
        self.g.set_entry_point(self.entry_id)
        return self.g.compile()
```

### `app/api/v1/routes_compile.py` (modified to compile & register)
```python
from fastapi import APIRouter
from .models import OrchestrationPackage, CompileResponse
from ...ir.loader import build_runtime_plan
from ...compiler.builder import GraphBuilder
from ...compiler.registry import put as put_graph
import uuid

router = APIRouter(prefix="/v1", tags=["v1"])

@router.post("/compile", response_model=CompileResponse)
async def compile_package(pkg: OrchestrationPackage):
    ok, plan, errors = build_runtime_plan(pkg.model_dump(), runtime_vars={})
    if not ok:
        return CompileResponse(ok=False, message="; ".join(errors)[:512])

    builder = GraphBuilder(plan)
    app = builder.build()
    gid = f"g-{uuid.uuid4().hex}"
    put_graph(gid, app)
    return CompileResponse(ok=True, graph_id=gid, message="Compiled")
```

---

## Tests

### `tests/test_compile_builds.py`
```python
import pytest
from app.ir.loader import build_runtime_plan
from app.compiler.builder import GraphBuilder

def pkg_router_seq():
    # Router -> A (agent) -> END; Router also knows B but no edge used in this tiny test
    router = {
        "id":"r","kind":"router","label":"R",
        "data":{"targets":["a","b"],"minConfidence":0.0,"routeSchema":{"type":"object"}}
    }
    agent_a = {
        "id":"a","kind":"agent.codeless","label":"A",
        "data":{"systemInstructions":"Hello","model":{"provider":"openai","modelId":"gpt-4o","temperature":0.1,"topP":1,"maxTokens":64},
                "context":{"historyWindow":{"mode":"LastN","n":1}},"tools":{"policy":"Disabled","attached":[]}}}
    agent_b = {
        "id":"b","kind":"agent.codeless","label":"B",
        "data":{"systemInstructions":"Hi","model":{"provider":"openai","modelId":"gpt-4o","temperature":0.1,"topP":1,"maxTokens":64},
                "context":{"historyWindow":{"mode":"LastN","n":1}},"tools":{"policy":"Disabled","attached":[]}}}
    return {
        "meta":{"id":"m","name":"x","version":"1.0.0"},
        "nodes":[router, agent_a, agent_b],
        "edges":[{"id":"e1","from":"r","to":"a"}],
        "entryId":"r"
    }

@pytest.mark.anyio
async def test_graph_compiles():
    ok, plan, errs = build_runtime_plan(pkg_router_seq(), {})
    assert ok, errs
    app = GraphBuilder(plan).build()
    assert app is not None
```

### `tests/test_compile_invoke.py`
```python
import pytest, time
from app.ir.loader import build_runtime_plan
from app.compiler.builder import GraphBuilder

def pkg_simple_seq():
    a = {
        "id":"a","kind":"agent.codeless","label":"A",
        "data":{"systemInstructions":"Say hi","model":{"provider":"openai","modelId":"gpt-4o","temperature":0.1,"topP":1,"maxTokens":64},
                "context":{"historyWindow":{"mode":"LastN","n":1}},"tools":{"policy":"Disabled","attached":[]}}}
    return {"meta":{"id":"m","name":"x","version":"1.0.0"},"nodes":[a],"edges":[],"entryId":"a"}

@pytest.mark.anyio
async def test_invoke_stub_agent():
    ok, plan, errs = build_runtime_plan(pkg_simple_seq(), {})
    assert ok, errs
    app = GraphBuilder(plan).build()
    out = app.invoke({"messages":[{"role":"user","content":"hello","ts": time.time()}]})
    assert "messages" in out and any(m.get("role") == "assistant" for m in out["messages"])
```

---

## Step‑By‑Step Implementation

1. **Add LangGraph dependency**
   - Add `langgraph>=1.0.0` to `pyproject.toml`.

2. **Define shared state**
   - `OrchestratorState` with `messages`, `scratch`, `route`, `result` for simple flow.

3. **Node compilers**
   - `agent.codeless`: stub that appends a deterministic assistant message (real LLM comes later).
   - `router`: stub decision picks the first `targets[]`, sets `state.route`, uses `add_conditional_edges` mapping.
   - `sequential`: passthrough node; edges are wired normally.
   - `concurrent`: passthrough node; builder will fan‑out/fan‑in (scaffolding only).

4. **Builder**
   - Registers built‑in compilers; iterates nodes to add them to the graph; wires edges; sets `END` for nodes with no outgoing edges; sets entry point; compiles the app.

5. **Registry + /compile**
   - Store compiled app in an in‑memory registry; return `graph_id` from `/v1/compile`.

6. **Tests**
   - Ensure graph builds and an invocation on a simple graph yields an assistant message (from the stub agent).

---

## Acceptance Criteria
- [ ] Compiler translates IR plan into a LangGraph `StateGraph` with nodes for `router`, `sequential`, `concurrent`, and `agent.codeless`.
- [ ] Unknown node kinds compile to a no‑op node so graphs remain buildable.
- [ ] Router uses `add_conditional_edges` and sets `state.route` (first target as placeholder).
- [ ] Nodes without outgoing edges connect to `END`.
- [ ] `/v1/compile` compiles + registers a graph and returns a `graph_id`.
- [ ] Tests pass with **≥80%** coverage for compiler modules added in this PR.
- [ ] Cleanly organized and easy to extend with new orchestration kinds.

## Validation
```bash
pytest -q
# (manual) start server and compile a tiny IR
uvicorn app.main:app --reload
curl -s -X POST http://127.0.0.1:8000/v1/compile -H "Content-Type: application/json" -d '{
  "meta": {"id":"m","name":"x","version":"1.0.0"},
  "nodes": [{"id":"a","kind":"agent.codeless","label":"A","data":{"systemInstructions":"Hello","model":{"provider":"openai","modelId":"gpt-4o","temperature":0.1,"topP":1,"maxTokens":64},"context":{"historyWindow":{"mode":"LastN","n":1}},"tools":{"policy":"Disabled","attached":[]}}}],
  "edges": [],
  "entryId": "a"
}'
```

---

## Manual Pre/Post Items
- **Install LangGraph** in your dev env (`pip install -e .` should bring it in via `pyproject.toml`).  
- **IR prerequisites:** `/v1/validate` + `build_runtime_plan()` (PR‑04/PR‑05) should already be working.  
- **Stub behavior:** This PR’s agent is intentionally a stub; we’ll add real LLM execution and tool calls in PR‑08/PR‑09.  
- **Graph persistence:** The registry is in‑memory only; durable storage will be tackled alongside the checkpointer PR.  

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
