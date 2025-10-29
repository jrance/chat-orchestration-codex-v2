# PROMPT FOR CODEX

Implement the following PR and validate that everything has been completed fully and that all tests pass and all acceptance criteria is met. Feel free to modify the recommended code if there is a better and cleaner approach.

---

# PR-04 of 14 — Orchestration IR Validation & Normalization

## PR Title
Add JSON‑Schema validation, topology checks, and normalized plan output for /v1/validate (PR 4 of 14)

## Description
Implement full validation for the orchestration **IR** by combining:
1) **JSON‑Schema validation** (Draft 2020‑12) against `schemas/orchestration_ir.schema.json`, and  
2) **Topology checks** (entry node, reachability from entry, edge integrity, basic edge‑kind constraints), plus  
3) **Normalization** that returns a convenient **plan snapshot** including `entryId`, `nodeById`, `edges`, and **resolved tool bindings** for each agent.

Update `/v1/validate` to run both layers, return structured errors/warnings, and—when valid—emit the normalized plan. This sets the stage for PR‑06 (compiler) and PR‑08/09 (execution/streaming).

The orchestration schema can be found at schemas\orchestration_ir.schema.json and examples of valid orchestration packages can be found in schemas\examples. Use the examples to ensure that the LangGraph app compiles correctly.

## Purpose
- Guarantee IRs conform to the schema and basic structural invariants before compilation/execution.
- Produce a normalized view Codex can use to wire the compiler later (node map, edges, agent→tool map).

## Scope
- New `validation/` package (loader, topology, validator).
- Update request/response models to include `errors`, `warnings`, `normalized`.
- Implement `/v1/validate` logic (200 OK with `ok: true|false`).
- Tests: valid IR (passes), bad edges (fail), missing/ambiguous `entryId` (fail), orphan node (fail).

## Goals
- Draft 2020‑12 schema validation.
- Deterministic topology checks with actionable messages.
- Normalized snapshot includes resolved tool bindings.
- Tests ≥ 80% coverage for this PR’s modules.

---

## Files to Add / Change

```
app/
├─ validation/
│  ├─ __init__.py
│  ├─ schema_loader.py
│  ├─ topology.py
│  └─ ir_validator.py
├─ api/
│  └─ v1/
│     ├─ models.py              # (extend ValidateResponse)
│     └─ routes_validate.py     # (implement /v1/validate)
├─ config/settings.py           # (add ORCH_SCHEMA_PATH)
tests/
├─ test_validate_success.py
├─ test_validate_edges.py
├─ test_validate_entry_rooting.py
└─ test_validate_orphans.py
pyproject.toml                  # (add jsonschema)
.env.example                    # (optional: override schema path)
README.md                       # (document /v1/validate behavior)
```

### `pyproject.toml` (append/modify)
```toml
[project]
# ...existing...
dependencies = [
  "fastapi>=0.115",
  "uvicorn[standard]>=0.30",
  "pydantic>=2.7",
  "pydantic-settings>=2.2",
  "httpx>=0.27",
  "typing-extensions>=4.12",
  "structlog>=24.1.0",
  "jsonschema>=4.23.0",
]
```

### `.env.example` (append)
```
# (Optional) Override schema location
ORCH_SCHEMA_PATH=schemas/orchestration_ir.schema.json
```

### `app/config/settings.py` (extend)
```python
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    # ...existing fields...
    # Validation
    orch_schema_path: str = "schemas/orchestration_ir.schema.json"

    model_config = SettingsConfigDict(env_file=".env", env_prefix="", extra="ignore")

settings = Settings()
```

### `app/validation/schema_loader.py`
```python
from __future__ import annotations
import json, os
from typing import Any
from app.config.settings import settings

_cached_schema: dict[str, Any] | None = None
_cached_mtime: float | None = None

def load_schema() -> dict[str, Any]:
    global _cached_schema, _cached_mtime
    path = settings.orch_schema_path
    stat = os.stat(path)
    if _cached_schema is not None and _cached_mtime == stat.st_mtime:
        return _cached_schema
    with open(path, "r", encoding="utf-8") as f:
        _cached_schema = json.load(f)
    _cached_mtime = stat.st_mtime
    return _cached_schema
```

### `app/validation/topology.py`
```python
from __future__ import annotations
from typing import Dict, List, Set, Tuple

# Minimal kind constraints (phase-1):
# - Tool/MCP/Output nodes must not have outgoing edges
NO_OUTGOING = {"tool", "mcpServer", "output"}

def index_nodes(nodes: List[dict]) -> Dict[str, dict]:
    idx: Dict[str, dict] = {}
    for n in nodes:
        nid = n.get("id")
        if not nid:
            raise ValueError("Node missing id")
        if nid in idx:
            raise ValueError(f"Duplicate node id: {nid}")
        idx[nid] = n
    return idx

def build_graph(edges: List[dict]) -> Tuple[Dict[str, List[str]], Dict[str, int]]:
    adj: Dict[str, List[str]] = {}
    indeg: Dict[str, int] = {}
    for e in edges:
        f = e.get("from")
        t = e.get("to")
        if f is None or t is None:
            raise ValueError("Edge missing 'from' or 'to'")
        adj.setdefault(f, []).append(t)
        indeg[t] = indeg.get(t, 0) + 1
        indeg.setdefault(f, indeg.get(f, 0))
    return adj, indeg

def find_roots(node_ids: Set[str], indeg: Dict[str, int]) -> List[str]:
    roots = [nid for nid in node_ids if indeg.get(nid, 0) == 0]
    return roots

def reachable_from(start: str, adj: Dict[str, List[str]]) -> Set[str]:
    seen: Set[str] = set()
    stack = [start]
    while stack:
        u = stack.pop()
        if u in seen:
            continue
        seen.add(u)
        for v in adj.get(u, []):
            if v not in seen:
                stack.append(v)
    return seen

def check_no_outgoing_constraints(edges: List[dict], node_by_id: Dict[str, dict]) -> List[str]:
    errors: List[str] = []
    outgoing_map = {}
    for e in edges:
        outgoing_map.setdefault(e["from"], 0)
        outgoing_map[e["from"]] += 1
    for nid, n in node_by_id.items():
        kind = n.get("kind")
        if kind in NO_OUTGOING and outgoing_map.get(nid, 0) > 0:
            errors.append(f"Node '{nid}' of kind '{kind}' must not have outgoing edges")
    return errors
```

### `app/validation/ir_validator.py`
```python
from __future__ import annotations
from typing import Any, Dict, List, Tuple
from jsonschema import Draft202012Validator
from app.validation.schema_loader import load_schema
from app.validation.topology import index_nodes, build_graph, find_roots, reachable_from, check_no_outgoing_constraints

def _format_error(e) -> str:
    loc = "/".join([str(p) for p in e.path]) or "<root>"
    return f"{loc}: {e.message}"

def run_jsonschema_validation(ir: Dict[str, Any]) -> List[str]:
    schema = load_schema()
    validator = Draft202012Validator(schema)
    return [_format_error(e) for e in validator.iter_errors(ir)]

def resolve_tools_for_agents(node_by_id: Dict[str, dict]) -> Tuple[Dict[str, List[str]], List[str]]:
    errors: List[str] = []
    bindings: Dict[str, List[str]] = {}
    for nid, node in node_by_id.items():
        if node.get("kind") != "agent.codeless":
            continue
        attached = (node.get("data", {}).get("tools", {}) or {}).get("attached", []) or []
        tool_ids: List[str] = []
        for tid in attached:
            if tid not in node_by_id:
                errors.append(f"Agent '{nid}' references missing tool id '{tid}'")
            elif node_by_id[tid].get("kind") != "tool":
                errors.append(f"Agent '{nid}' attached id '{tid}' is not a tool node")
            else:
                tool_ids.append(tid)
        bindings[nid] = tool_ids
    return bindings, errors

def validate_and_normalize(ir: Dict[str, Any]) -> Tuple[bool, Dict[str, Any], List[str], List[str]]:
    errors: List[str] = []
    warnings: List[str] = []

    # 1) JSON Schema
    schema_errors = run_jsonschema_validation(ir)
    errors.extend(schema_errors)
    if errors:
        return False, {}, errors, warnings

    nodes = ir.get("nodes", [])
    edges = ir.get("edges", [])
    node_by_id = index_nodes(nodes)

    # 2) Edge integrity
    adj, indeg = build_graph(edges)
    for e in edges:
        if e["from"] not in node_by_id:
            errors.append(f"Edge '{e.get('id', '<no-id>')}' references missing 'from' node '{e['from']}'")
        if e["to"] not in node_by_id:
            errors.append(f"Edge '{e.get('id', '<no-id>')}' references missing 'to' node '{e['to']}'")

    errors.extend(check_no_outgoing_constraints(edges, node_by_id))

    if errors:
        return False, {}, errors, warnings

    # 3) EntryId resolution
    entry_id = ir.get("entryId")
    if not entry_id:
        roots = find_roots(set(node_by_id.keys()), indeg)
        if len(roots) == 1:
            entry_id = roots[0]
        elif len(roots) == 0:
            errors.append("No entryId provided and no root node could be determined (graph has cycles or all nodes have incoming edges).")
        else:
            errors.append(f"No entryId provided and multiple root nodes found: {roots}")
    else:
        if entry_id not in node_by_id:
            errors.append(f"entryId '{entry_id}' is not a valid node id")

    if errors:
        return False, {}, errors, warnings

    # 4) Reachability
    seen = reachable_from(entry_id, adj)
    all_ids = set(node_by_id.keys())
    orphans = list(sorted(all_ids - seen))
    if orphans:
        errors.append(f"Unreachable nodes from entryId '{entry_id}': {orphans}")

    if errors:
        return False, {}, errors, warnings

    # 5) Resolve tool bindings from agents
    tool_bindings, tb_errors = resolve_tools_for_agents(node_by_id)
    errors.extend(tb_errors)
    if errors:
        return False, {}, errors, warnings

    # 6) Normalized snapshot
    normalized = {
        "entryId": entry_id,
        "nodeById": node_by_id,
        "edges": edges,
        "resolved": {
            "agentToolBindings": tool_bindings
        }
    }
    return True, normalized, errors, warnings
```

### `app/api/v1/models.py` (extend `ValidateResponse`)
```python
from pydantic import BaseModel, Field
from typing import Any, Dict, Optional, List

class OrchestrationPackage(BaseModel):
    meta: Dict[str, Any]
    nodes: list[Dict[str, Any]]
    edges: list[Dict[str, Any]]
    entryId: Optional[str] = None

class ValidateResponse(BaseModel):
    ok: bool = False
    message: str = ""
    errors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
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

### `app/api/v1/routes_validate.py` (implement endpoint)
```python
from fastapi import APIRouter
from .models import OrchestrationPackage, ValidateResponse
from ...validation.ir_validator import validate_and_normalize

router = APIRouter(prefix="/v1", tags=["v1"])

@router.post("/validate", response_model=ValidateResponse)
async def validate_package(pkg: OrchestrationPackage):
    ok, normalized, errors, warnings = validate_and_normalize(pkg.model_dump())
    if ok:
        return ValidateResponse(ok=True, normalized=normalized, message="Valid IR")
    return ValidateResponse(ok=False, errors=errors, warnings=warnings, message="Invalid IR")
```

---

## Tests

> Tests assume `schemas/orchestration_ir.schema.json` exists (from PR‑01).

### Shared helpers (inline in tests as needed)
```python
def minimal_agent_node(id="agent-1", label="Agent"):
    return {
        "id": id,
        "kind": "agent.codeless",
        "label": label,
        "data": {
            "systemInstructions": "Do a thing.",
            "model": {
                "provider": "openai",
                "modelId": "gpt-4o",
                "temperature": 0.3,
                "topP": 1,
                "maxTokens": 128
            },
            "context": {"historyWindow": {"mode":"LastN","n":3}},
            "tools": {"policy":"Disabled","attached":[]}
        }
    }

def minimal_tool_node(id="tool-1", label="Tool"):
    return {
        "id": id,
        "kind": "tool",
        "label": label,
        "data": {"name": "tool:echo", "argsSchema": {"type":"object"}}
    }

def base_meta():
    return {"id":"m-1","name":"Test","version":"1.0.0"}
```

### `tests/test_validate_success.py`
```python
import pytest
from httpx import AsyncClient
from app.main import app

def minimal_pkg():
    agent = {
        "id": "agent-1",
        "kind": "agent.codeless",
        "label": "Agent",
        "data": {
            "systemInstructions": "Use tool to answer.",
            "model": {
                "provider": "openai",
                "modelId": "gpt-4o",
                "temperature": 0.3,
                "topP": 1,
                "maxTokens": 128
            },
            "context": {"historyWindow": {"mode":"LastN","n":3}},
            "tools": {"policy":"Auto","attached":["tool-1"]}
        }
    }
    tool = {"id":"tool-1","kind":"tool","label":"Tool","data":{"name":"tool:policy-search","argsSchema":{"type":"object"}}}
    return {
        "meta": {"id":"m-1","name":"Test","version":"1.0.0"},
        "nodes": [agent, tool],
        "edges": [{"id":"e1","from":"agent-1","to":"tool-1"}],
        "entryId": "agent-1"
    }

@pytest.mark.anyio
async def test_validate_success():
    pkg = minimal_pkg()
    async with AsyncClient(app=app, base_url="http://test") as ac:
        r = await ac.post("/v1/validate", json=pkg)
    assert r.status_code == 200
    j = r.json()
    assert j["ok"] is True
    assert j["normalized"]["entryId"] == "agent-1"
    bindings = j["normalized"]["resolved"]["agentToolBindings"]
    assert bindings["agent-1"] == ["tool-1"]
```

### `tests/test_validate_edges.py`
```python
import pytest
from httpx import AsyncClient
from app.main import app

@pytest.mark.anyio
async def test_missing_node_in_edge():
    agent = {
        "id":"agent-1","kind":"agent.codeless","label":"A",
        "data":{"systemInstructions":"x","model":{"provider":"openai","modelId":"gpt-4o","temperature":0.1,"topP":1,"maxTokens":64},
                "context":{"historyWindow":{"mode":"LastN","n":1}},"tools":{"policy":"Disabled","attached":[]}}}
    pkg = {"meta":{"id":"m","name":"x","version":"1.0.0"},
           "nodes":[agent],
           "edges":[{"id":"e1","from":"agent-1","to":"missing"}],
           "entryId":"agent-1"}
    async with AsyncClient(app=app, base_url="http://test") as ac:
        r = await ac.post("/v1/validate", json=pkg)
    j = r.json()
    assert j["ok"] is False
    assert any("missing 'to' node 'missing'" in err for err in j["errors"])
```

### `tests/test_validate_entry_rooting.py`
```python
import pytest
from httpx import AsyncClient
from app.main import app

@pytest.mark.anyio
async def test_infer_single_root():
    agent = {
        "id":"agent-1","kind":"agent.codeless","label":"A",
        "data":{"systemInstructions":"x","model":{"provider":"openai","modelId":"gpt-4o","temperature":0.1,"topP":1,"maxTokens":64},
                "context":{"historyWindow":{"mode":"LastN","n":1}},"tools":{"policy":"Disabled","attached":[]}}}
    tool = {"id":"tool-1","kind":"tool","label":"T","data":{"name":"tool:echo","argsSchema":{"type":"object"}}}
    pkg = {"meta":{"id":"m","name":"x","version":"1.0.0"},
           "nodes":[agent, tool],
           "edges":[{"id":"e1","from":"agent-1","to":"tool-1"}]}
    async with AsyncClient(app=app, base_url="http://test") as ac:
        r = await ac.post("/v1/validate", json=pkg)
    j = r.json()
    assert j["ok"] is True
    assert j["normalized"]["entryId"] == "agent-1"

@pytest.mark.anyio
async def test_multiple_roots_error():
    a1 = {"id":"a1","kind":"agent.codeless","label":"A1",
          "data":{"systemInstructions":"x","model":{"provider":"openai","modelId":"gpt-4o","temperature":0.1,"topP":1,"maxTokens":64},
                  "context":{"historyWindow":{"mode":"LastN","n":1}},"tools":{"policy":"Disabled","attached":[]}}}
    a2 = {"id":"a2","kind":"agent.codeless","label":"A2",
          "data":{"systemInstructions":"y","model":{"provider":"openai","modelId":"gpt-4o","temperature":0.1,"topP":1,"maxTokens":64},
                  "context":{"historyWindow":{"mode":"LastN","n":1}},"tools":{"policy":"Disabled","attached":[]}}}
    pkg = {"meta":{"id":"m","name":"x","version":"1.0.0"}, "nodes":[a1,a2], "edges":[]}
    async with AsyncClient(app=app, base_url="http://test") as ac:
        r = await ac.post("/v1/validate", json=pkg)
    j = r.json()
    assert j["ok"] is False
    assert "multiple root nodes" in " ".join(j["errors"])
```

### `tests/test_validate_orphans.py`
```python
import pytest
from httpx import AsyncClient
from app.main import app

@pytest.mark.anyio
async def test_orphan_unreachable_node():
    agent = {
        "id":"agent-1","kind":"agent.codeless","label":"A",
        "data":{"systemInstructions":"x","model":{"provider":"openai","modelId":"gpt-4o","temperature":0.1,"topP":1,"maxTokens":64},
                "context":{"historyWindow":{"mode":"LastN","n":1}},"tools":{"policy":"Disabled","attached":[]}}}
    tool = {"id":"tool-1","kind":"tool","label":"T","data":{"name":"tool:echo","argsSchema":{"type":"object"}}}
    orphan = {"id":"orphan","kind":"tool","label":"Orphan","data":{"name":"tool:noop","argsSchema":{"type":"object"}}}
    pkg = {"meta":{"id":"m","name":"x","version":"1.0.0"},
           "nodes":[agent, tool, orphan],
           "edges":[{"id":"e1","from":"agent-1","to":"tool-1"}],
           "entryId":"agent-1"}
    async with AsyncClient(app=app, base_url="http://test") as ac:
        r = await ac.post("/v1/validate", json=pkg)
    j = r.json()
    assert j["ok"] is False
    assert "Unreachable nodes" in " ".join(j["errors"])
```

---

## Step‑By‑Step Implementation

1. **Dependencies**
   - Add `jsonschema>=4.23.0` to `pyproject.toml` and reinstall dev env.

2. **Settings**
   - Add `orch_schema_path` with default `schemas/orchestration_ir.schema.json` and expose `ORCH_SCHEMA_PATH` in `.env.example`.

3. **Schema loader**
   - Implement `schema_loader.load_schema()` with mtime caching so edits reload automatically in dev.

4. **Topology utilities**
   - Build node index, adjacency, indegree; infer root(s) and reachability; enforce basic “no outgoing” constraint for `tool`, `mcpServer`, `output` nodes.

5. **Validator**
   - `run_jsonschema_validation()` gathers all Draft 2020‑12 errors (with JSON pointer paths).
   - `validate_and_normalize()` performs: schema → edge integrity → entry resolution → reachability → tool binding resolution → normalized snapshot.

6. **API**
   - Update `/v1/validate` to return `200 OK` with `ok: true|false`, `errors`, `warnings`, and `normalized` (on success).

7. **Tests**
   - Cover success path and each failure class (bad edge, ambiguous entry, orphan). Ensure deterministic error strings contain helpful details.

---

## Acceptance Criteria
- [ ] `/v1/validate` performs Draft 2020‑12 JSON‑Schema validation from `ORCH_SCHEMA_PATH`.
- [ ] Topology checks enforce: valid edges, resolvable `entryId` (or single root inference), and full reachability from entry.
- [ ] `tool`, `mcpServer`, and `output` nodes have **no outgoing edges**.
- [ ] On success, response includes a `normalized` snapshot with `entryId`, `nodeById`, `edges`, and `resolved.agentToolBindings`.
- [ ] All tests in this PR pass with **≥80%** coverage for the new validation modules.
- [ ] Behavior is deterministic and error messages are actionable.

## Validation
```bash
pytest -q
curl -s -X POST http://127.0.0.1:8000/v1/validate -H "Content-Type: application/json" \
  -d @path/to/your/sample_ir.json | jq .
```

---

## Manual Pre/Post Items
- **Ensure the schema file exists:** Confirm `schemas/orchestration_ir.schema.json` is present and matches your current IR schema.
- **Override path if needed:** Set `ORCH_SCHEMA_PATH` in `.env` to point to a custom schema file/location.
- **Keep schema in source control:** Commit the schema so validation stays reproducible across environments/CI.
- **Large IRs:** For very large IRs, consider raising FastAPI request size limits later; current code uses defaults.

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
