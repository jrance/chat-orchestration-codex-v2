# PROMPT FOR CODEX

Implement the following PR and validate that everything has been completed fully and that all tests pass and all acceptance criteria is met. Feel free to modify the recommended code if there is a better and cleaner approach.

---

# PR-12 of 14 — Router Orchestration (LLM JSON‑routing, tie‑break, fallbacks)

## PR Title
Implement Router orchestration with JSON‑schema routing, tie‑break strategies, and fallbacks (PR 12 of 14)

## Description
This PR adds a **Router** orchestration node that uses an LLM classification step (via our OpenAI‑compatible `/v1/responses`) to choose which child node to execute next. The router uses a JSON **routeSchema** with `{target, confidence, rationale}` and supports **minConfidence**, **allowBelowMinForTieBreak**, and three **tieBreak** strategies:

- **HighestConfidence** — choose the highest `confidence` ≥ `minConfidence` (or all if `allowBelowMinForTieBreak=true`).
- **DeterministicOrder** — choose the first eligible child in a stable order.
- **PreferList** — choose the first eligible child that appears in `tieBreakPrefer`.

Fallback modes are supported:
- **AskUserClarify** — pause the run and return metadata that the UI can use to ask a follow‑up question. The run may be resumed with `POST /v1/execute/{runId}/resume`.
- **DefaultChild** — route to `fallback.defaultChild`.
- **SafeAgent** — route to a designated safe agent (by label or id).
- **Error** — raise a `ROUTER_NO_ROUTE` orchestration error.

The router **auto‑syncs** the `routeSchema.properties.target.enum` from the set of child labels when `autoSyncEnum=true`, and validates that all targets are valid & reachable.

The orchestration schema can be found at schemas\orchestration_ir.schema.json and examples of valid orchestration packages can be found in schemas\examples. Use the examples to ensure that the LangGraph app compiles correctly.

## Purpose
- Cleanly separate routing logic from child execution with a consistent JSON‑based contract.
- Provide deterministic behavior under ambiguity via tie‑breakers and clear fallbacks.
- Keep the design extensible and testable (mockable LLM helper, pure decision function).

## Scope
- Compiler: normalize router config; derive targets; validate topology.
- Provider: small helper to run the routing LLM call with `response_format: json_schema`.
- Runtime: router decision + integration with engine; pause/resume for “AskUserClarify”.
- Telemetry: events for router start/decision/fallback.
- Tests: tie‑break paths, minConfidence, allowBelowMinForTieBreak, fallbacks, autoSyncEnum.

---

## Files to Add / Modify

```
app/
├─ compiler/nodes/router.py                 # NEW: normalize + validate + build runtime plan
├─ providers/openai_like/route.py           # NEW: run routing LLM call (json_schema)
├─ runtime/patterns/router.py               # NEW: pure decision & tie-break logic
├─ runtime/engine.py                        # MOD: integrate router execution & pause
├─ runtime/types.py                         # MOD: RouterDecision type, pause metadata
├─ telemetry/events.py                      # MOD: add router event constants
tests/
├─ runtime/test_router_highest_conf.py
├─ runtime/test_router_deterministic_order.py
├─ runtime/test_router_prefer_list.py
├─ runtime/test_router_fallbacks.py
├─ runtime/test_router_autosync_enum.py
docs/
└─ ROUTER_ORCHESTRATION.md                  # NEW: behavior, config table, examples
```
> NOTE: Filenames reflect the current package layout used in previous PRs. If names differ in your tree, adapt accordingly.

---

## IR → Runtime normalization

Given IR (excerpt):
```json
{
  "id": "39d8e718-...",
  "kind": "router",
  "label": "Supervisor",
  "data": {
    "prompt": "Route to <targets>. Return JSON {target, confidence}.",
    "minConfidence": 0.5,
    "tieBreak": "HighestConfidence",
    "tieBreakPrefer": [],
    "fallback": { "mode": "AskUserClarify", "defaultChild": null, "safeAgentRef": null },
    "allowBelowMinForTieBreak": false,
    "routeSchema": { "type": "object", "properties": { "target": { "enum": [] }, "confidence": { "type": "number" } }, "required": ["target"] },
    "telemetry": { "labels": {} },
    "targets": ["Policy Agent", "M365 Agent"],
    "autoSyncEnum": true
  }
}
```
Normalize to:
```python
{
  "id": node.id,
  "kind": "router",
  "label": node.label,
  "cfg": {
    "min_confidence": float | None,  # default 0.0
    "tie_break": "HighestConfidence" | "DeterministicOrder" | "PreferList",
    "tie_break_prefer": list[str],   # default []
    "fallback": {"mode": "...", "defaultChild": str|None, "safeAgentRef": str|None},
    "allow_below_min_for_tiebreak": bool,
    "auto_sync_enum": bool,
    "route_schema": {...},           # JSON Schema (target/confidence/rationale)
    "prompt_template": str           # with <targets> token
  },
  "targets": list[str],              # resolved from children when auto_sync_enum
  "children": list[child_ids],       # outgoing edges order == deterministic order
}
```
Rules:
- If `autoSyncEnum=true`, set `route_schema.properties.target.enum = targets` and ensure `targets` equals the **labels** of outgoing children (case-sensitive). Error on mismatch.
- If `targets` omitted, but there are outgoing edges, derive `targets` from child **labels** in deterministic order.
- `minConfidence` default = 0.0.
- `tieBreak` default = `HighestConfidence`.
- Fallback defaults to `{mode:"Error"}` if omitted.
- Stable order: order of edges as supplied by the IR (first edge encountered = highest precedence for DeterministicOrder).

---

## Routing LLM helper (`app/providers/openai_like/route.py`)

```python
from __future__ import annotations
from typing import Dict, Any
from .responses import create_response
from app.telemetry.bus import publish_telemetry

ROUTE_SCHEMA_BASE = {
  "name": "router_route",
  "schema": {
    "type": "object",
    "properties": {
      "target": {"type":"string"},         # enum is inserted by caller
      "confidence": {"type":"number", "minimum":0, "maximum":1},
      "rationale": {"type":"string"}
    },
    "required": ["target"]
  }
}

async def run_router_llm(prompt: str, targets: list[str], extra_headers: Dict[str,str] | None = None) -> Dict[str, Any]:
  schema = dict(ROUTE_SCHEMA_BASE)
  schema["schema"] = dict(schema["schema"])
  # enforce enum
  props = dict(schema["schema"]["properties"])
  props["target"] = dict(props["target"])
  props["target"]["enum"] = targets
  schema["schema"]["properties"] = props

  messages = [{"role":"system","content":prompt}]
  publish_telemetry({"type":"telemetry.router.llm.request","payload":{"targets":targets}})
  out = await create_response(messages, model="gpt-4o", response_format={"type":"json_schema","json_schema": schema}, extra_headers=extra_headers)
  publish_telemetry({"type":"telemetry.router.llm.response","payload":{"ok": True}})
  # Expect JSON dict with keys; callers validate
  return out
```

---

## Decision logic (`app/runtime/patterns/router.py`)

```python
from __future__ import annotations
from typing import Dict, Any, List, Optional, Tuple

class RouterError(Exception): ...
class NoRouteError(RouterError): ...

def choose_target(
    decision: Dict[str, Any],
    targets: List[str],
    min_conf: float,
    tie_break: str,
    tie_break_prefer: List[str],
    allow_below_min: bool,
    deterministic_order: List[str]
) -> Optional[str]:
    """
    decision: {'target': str, 'confidence': float|None}
    returns chosen target label or None if no eligible target.
    """
    t = decision.get("target")
    c = decision.get("confidence", 0.0)
    # Direct choice if above min
    if t in targets and (c is None or c >= min_conf):
        return t
    # If below min or invalid, apply tie-break among candidates
    eligible = [lab for lab in targets if allow_below_min or lab == t]  # simplest: prefer LLM pick even if low
    if not eligible:
        return None
    if tie_break == "PreferList" and tie_break_prefer:
        for lab in tie_break_prefer:
            if lab in eligible:
                return lab
    if tie_break == "DeterministicOrder":
        for lab in deterministic_order:
            if lab in eligible:
                return lab
    # HighestConfidence is trivial here (we only have one confidence), fall through to LLM pick
    return t if t in targets else (eligible[0] if eligible else None)
```

---

## Engine integration (`app/runtime/engine.py`)

- When encountering a `router` node:
  1. Build `targets` from normalized plan and render `prompt_template`, replacing `<targets>` with a comma‑separated list.
  2. Call `providers.openai_like.route.run_router_llm(...)` with `response_format=json_schema`.
  3. Validate the returned dict: must contain `target` in `targets`; coerce `confidence` into `[0,1]` range if provided.
  4. Use `app/runtime/patterns/router.choose_target(...)` to finalize the route (considering `min_confidence`, `tie_break`, etc.).
  5. If **no** target: evaluate `fallback.mode`:
     - `AskUserClarify`: set run state to `paused` with `pause_reason="router.ask_user"`; include `metadata.hitl` object on the final event. Return control without proceeding to a child.
     - `DefaultChild`: pick `fallback.defaultChild` (validate that it is a valid target) and continue.
     - `SafeAgent`: route to `fallback.safeAgentRef` (label or id); continue.
     - `Error`: raise `NoRouteError("ROUTER_NO_ROUTE")` (handled by PR‑08 middleware with a structured error envelope).
  6. Emit telemetry:
     - `telemetry.router.start` with `targets`, `min_confidence`, etc.
     - `telemetry.router.decision` with raw LLM `target/confidence` and final `chosen`.
     - `telemetry.router.fallback` when a fallback is used.
  7. Continue execution at the chosen child node.

- **Streaming**: the router does not emit partial tokens to the main Responses stream. Only telemetry events are emitted during routing. After a route is decided, the chosen child’s normal streaming continues.

- **Pause envelope** (AskUserClarify): terminate the stream with `response.completed` including metadata:
  ```json
  { "type":"response.completed",
    "response": { "id":"...", "metadata": { "hitl": { "status":"awaiting_user_input", "reason":"router.ask_user", "prompt":"Please clarify which area you need help with: Policy vs. M365." } } } }
  ```
  The client can resume via `POST /v1/execute/{runId}/resume` (already implemented in PR‑08).

---

## Tests

### `tests/runtime/test_router_highest_conf.py`
- Three targets; mock LLM to return `{target:"M365 Agent", confidence:0.9}`.
- With `minConfidence=0.5`, route to “M365 Agent”.
- With `minConfidence=0.95` and `allowBelowMinForTieBreak=false` → no eligible; `fallback.mode=DefaultChild` should pick default.

### `tests/runtime/test_router_deterministic_order.py`
- LLM returns `{target:"X", confidence:0.2}` where “X” is not a valid target; `tieBreak="DeterministicOrder"` chooses the first child by edges order.

### `tests/runtime/test_router_prefer_list.py`
- LLM returns below min; `tieBreak="PreferList"` with `tieBreakPrefer=["Policy Agent","M365 Agent"]` selects “Policy Agent”.

### `tests/runtime/test_router_fallbacks.py`
- **AskUserClarify**: decision below min, no eligible, ensure engine sets `paused` and emits HITL metadata.
- **SafeAgent**: verify routing to known safe agent id/label.
- **Error**: verify `NoRouteError` raised and caught by error middleware.

### `tests/runtime/test_router_autosync_enum.py`
- `autoSyncEnum=true` with children labels `[A,B]` and `targets=["A","B","C"]` in schema → validation error (C not reachable).
- Missing `targets` but children exist → targets derived from children, and schema enum patched accordingly.

> All tests mock the LLM helper and avoid network.

---

## Docs (`docs/ROUTER_ORCHESTRATION.md`)

Include:
- Config table (minConfidence, tieBreak, tieBreakPrefer, allowBelowMinForTieBreak, autoSyncEnum).
- Examples: HighestConfidence, DeterministicOrder, PreferList with fallbacks.
- Pause/Resume flow with sample `response.completed` metadata for AskUserClarify.
- Notes on **targets** and label/id mapping (labels should be unique among router children).

---

## Acceptance Criteria
- [ ] Router compiles with normalized config; `autoSyncEnum` keeps schema/targets in sync.
- [ ] Router calls LLM with `response_format=json_schema` and validates the shape.
- [ ] Tie‑breakers behave as specified; deterministic order is stable across runs.
- [ ] Fallbacks work: AskUserClarify pauses; DefaultChild/SafeAgent route; Error surfaces structured error.
- [ ] Telemetry emits router start/decision/fallback events with labels and run ids.
- [ ] Unit tests pass; coverage ≥ 80% on new/modified modules.
- [ ] Documentation explains behavior with runnable examples.

---

## Validation
```bash
uv run pytest -q --cov=app --cov-report=term-missing

# Manual smoke:
# 1) Use Sample Orchestration Package 1 where router → Policy vs M365.
# 2) Set tieBreak=PreferList with ["Policy Agent"]; ask: "What's our vacation policy?" → routes to Policy.
# 3) Set minConfidence=0.95; ask ambiguous question → verify AskUserClarify pause + resume path.
```

---

## Manual Pre/Post Items
- Ensure child nodes of a router have **unique labels**; they become your routing `targets`.
- If you want stricter behavior, set `allowBelowMinForTieBreak=false` (recommended for production).

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
