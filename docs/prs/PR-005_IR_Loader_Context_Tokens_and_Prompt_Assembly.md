# PROMPT FOR CODEX

Implement the following PR and validate that everything has been completed fully and that all tests pass and all acceptance criteria is met. Feel free to modify the recommended code if there is a better and cleaner approach.

---

# PR-05 of 14 — IR Loader, Context Tokens & Prompt Assembly

## PR Title
Add IR loader, default context token expander, history windowing helpers, and prompt assembly (PR 5 of 14)

## Description
Introduce a **runtime IR loader** and a **context token expander** so prompts can use centrally managed tokens (e.g., `{user_details}`, `{tenant_id}`, `{now_iso}`, `{correlation_id}`, `{request_id}`). Implement **per‑agent history windowing** (LastN, TimeBounded) utilities and a **prompt assembler** that composes:

- Optional **org preamble** (when `injectOrgPreamble` is true)  
- Agent **style guide** (if present)  
- Agent **system instructions**, after token expansion  
- Optional **context vars** (key/value list or JSON block)  

This PR does not yet execute graphs; it prepares all **inputs** for the compiler/executor PRs.

## Purpose
- Centralize token replacement and make it trivial to add/edit default tokens.
- Provide deterministic prompt assembly consistent with the IR.
- Provide reusable message history windowing helpers.

## Scope
- New `context/` token registry & expander.
- New `prompt/` assembler (builds the final system prompt text).
- New `history/` utilities (per‑agent windowing).
- New `ir/loader.py` that validates + normalizes IR (using PR‑04) and returns a **runtime plan** with pre‑expanded prompts and agent metadata.
- Tests for token expansion, prompt assembly, history windowing, and IR loader outputs.
- Settings + `.env.example` entries for org preamble source.

## Goals
- Stable token expansion with **no crashes** on unknown tokens (leave them as‑is).
- Simple extension API: `register_token("name", provider_fn)`.
- History windowing supports `None`, `LastN`, and `TimeBounded`.
- `ir.loader.build_runtime_plan()` returns a shape that later PRs can compile/execute.

---

## Files to Add / Change

```
app/
├─ context/
│  ├─ __init__.py
│  └─ tokens.py
├─ prompt/
│  ├─ __init__.py
│  └─ assembler.py
├─ history/
│  ├─ __init__.py
│  └─ window.py
├─ ir/
│  ├─ __init__.py
│  └─ loader.py
├─ api/
│  └─ v1/
│     └─ routes_compile.py        # (optional demo: build_runtime_plan only; still 501 for compile)
├─ config/settings.py             # (extend with ORG_PREAMBLE_PATH / ORG_PREAMBLE_TEXT)
├─ logging/context.py             # (used for default token sources; no code change here)
tests/
├─ test_tokens.py
├─ test_prompt_assembly.py
├─ test_history_window.py
└─ test_ir_loader.py
.env.example                      # (append org preamble envs)
AGENTS.md                         # (append token list & assembly order)
```

### `.env.example` (append)
```
# Org preamble for prompts (choose one)
ORG_PREAMBLE_PATH=docs/org_preamble.md
# OR: set inline text instead of a file
# ORG_PREAMBLE_TEXT=Our organization values clarity and privacy...
```

### `app/config/settings.py` (extend)
```python
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    # ...existing fields...

    # Prompt preamble
    org_preamble_path: str | None = "docs/org_preamble.md"
    org_preamble_text: str | None = None

    model_config = SettingsConfigDict(env_file=".env", env_prefix="", extra="ignore")

settings = Settings()
```

### `app/context/tokens.py`
```python
from __future__ import annotations
import json, datetime as dt
import re
from typing import Any, Callable, Dict, Mapping, Optional

from app.logging.context import current_context  # correlation/request/tenant

TokenProvider = Callable[[Mapping[str, Any]], Any]
_registry: Dict[str, TokenProvider] = {}

_TOKEN_PATTERN = re.compile(r"\{([a-zA-Z0-9_\.:-]+)\}")

def register_token(name: str, provider: TokenProvider) -> None:
    _registry[name] = provider

def _default_tokens() -> None:
    if _registry:
        return  # already initialized
    # Core IDs
    register_token("tenant_id", lambda ctx: ctx.get("tenant_id") or current_context().get("tenant_id"))
    register_token("correlation_id", lambda ctx: ctx.get("correlation_id") or current_context().get("correlation_id"))
    register_token("request_id", lambda ctx: ctx.get("request_id") or current_context().get("request_id"))
    # Time
    register_token("now_iso", lambda ctx: dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z")
    register_token("date_yyyy_mm_dd", lambda ctx: dt.datetime.utcnow().strftime("%Y-%m-%d"))
    # User details (expected to be provided by caller; fallback to empty object)
    def _user_details(ctx: Mapping[str, Any]) -> Any:
        user = ctx.get("user_details") or {}
        return user
    register_token("user_details", _user_details)

_default_tokens()

def _stringify(val: Any) -> str:
    if val is None:
        return ""
    if isinstance(val, str):
        return val
    # JSON for non-strings
    return json.dumps(val, ensure_ascii=False)

def expand(text: str, runtime_vars: Optional[Mapping[str, Any]] = None) -> str:
    """
    Replace {token} with provider values.
    - Unknown tokens are left intact.
    - Non-string token values are JSON-serialized.
    - To include a literal brace, use '{{' or '}}' in your source (handled by authoring).
    """
    if not text:
        return text
    runtime_vars = runtime_vars or {}

    def repl(match: re.Match) -> str:
        name = match.group(1)
        prov = _registry.get(name)
        if not prov:
            return match.group(0)  # keep literal
        try:
            val = prov(runtime_vars)
        except Exception:
            return match.group(0)
        return _stringify(val)

    return _TOKEN_PATTERN.sub(repl, text)

def get_registry() -> Dict[str, TokenProvider]:
    return dict(_registry)
```

### `app/history/window.py`
```python
from __future__ import annotations
from typing import List, Dict, Any, Optional
import time

def last_n(messages: List[Dict[str, Any]], n: int) -> List[Dict[str, Any]]:
    if n <= 0:
        return []
    return messages[-n:]

def time_bounded(messages: List[Dict[str, Any]], duration_ms: int, now: Optional[float] = None) -> List[Dict[str, Any]]:
    if duration_ms <= 0:
        return []
    now = now if now is not None else time.time()
    cutoff = now - (duration_ms / 1000.0)
    # messages expected to carry 'ts' (unix seconds). Keep those with ts >= cutoff
    return [m for m in messages if float(m.get("ts", 0)) >= cutoff]

def window(messages: List[Dict[str, Any]], mode: str, n: int | None = None, duration_ms: int | None = None, now: Optional[float] = None) -> List[Dict[str, Any]]:
    mode = (mode or "None")
    if mode == "None":
        return []
    if mode == "LastN":
        return last_n(messages, int(n or 0))
    if mode == "TimeBounded":
        return time_bounded(messages, int(duration_ms or 0), now=now)
    # Unknown mode: default to empty
    return []
```

### `app/prompt/assembler.py`
```python
from __future__ import annotations
from typing import Any, Dict, Mapping, Optional, List
from app.context.tokens import expand
from app.config.settings import settings

def load_org_preamble() -> str:
    if settings.org_preamble_text:
        return settings.org_preamble_text.strip()
    try:
        if settings.org_preamble_path:
            with open(settings.org_preamble_path, "r", encoding="utf-8") as f:
                return f.read().strip()
    except FileNotFoundError:
        return ""
    return ""

def assemble_system_prompt(agent_data: Mapping[str, Any], runtime_vars: Optional[Mapping[str, Any]] = None) -> str:
    """
    Compose final system prompt with the following order:
    1) Org Preamble (if injectOrgPreamble)
    2) Style Guide (if present)
    3) System Instructions (after token expansion)
    4) Context Vars (optional; rendered as a compact JSON block)
    """
    runtime_vars = runtime_vars or {}
    parts: List[str] = []

    ctx = agent_data.get("context", {}) or {}
    if ctx.get("injectOrgPreamble"):
        preamble = load_org_preamble()
        if preamble:
            parts.append("### ORGANIZATION PREAMBLE
" + preamble)

    style = (agent_data.get("styleGuide") or "").strip()
    if style:
        parts.append("### STYLE GUIDE
" + style)

    sys = (agent_data.get("systemInstructions") or "").strip()
    sys = expand(sys, runtime_vars=runtime_vars)
    if sys:
        parts.append("### INSTRUCTIONS
" + sys)

    # Context vars (key/value strings), if present
    ctx_vars = (ctx.get("vars") or {})
    if ctx_vars:
        import json
        parts.append("### CONTEXT VARS
" + json.dumps(ctx_vars, ensure_ascii=False))

    return "

".join([p for p in parts if p])
```

### `app/ir/loader.py`
```python
from __future__ import annotations
from typing import Any, Dict, Tuple
from app.validation.ir_validator import validate_and_normalize
from app.prompt.assembler import assemble_system_prompt

def build_runtime_plan(ir: Dict[str, Any], runtime_vars: Dict[str, Any] | None = None) -> Tuple[bool, Dict[str, Any], list[str]]:
    """
    Validate + normalize (via PR-04). On success, produce a runtime plan that includes:
      - entryId
      - nodeById (original nodes)
      - edges
      - agentPrompts: {agentNodeId: compiled system prompt string}
    Returns (ok, plan_or_empty, errors).
    """
    ok, normalized, errors, warnings = validate_and_normalize(ir)
    if not ok:
        return False, {}, errors

    node_by_id = normalized["nodeById"]
    agent_prompts: Dict[str, str] = {}
    for nid, node in node_by_id.items():
        if node.get("kind") == "agent.codeless":
            agent_prompts[nid] = assemble_system_prompt(node.get("data", {}) or {}, runtime_vars=runtime_vars or {})

    plan = {
        "entryId": normalized["entryId"],
        "nodeById": node_by_id,
        "edges": normalized["edges"],
        "resolved": normalized["resolved"],
        "agentPrompts": agent_prompts,
    }
    return True, plan, []
```

### `app/api/v1/routes_compile.py` (optional tiny demo)
```python
from fastapi import APIRouter
from .models import OrchestrationPackage, CompileResponse
from ...ir.loader import build_runtime_plan

router = APIRouter(prefix="/v1", tags=["v1"])

@router.post("/compile", response_model=CompileResponse)
async def compile_package(pkg: OrchestrationPackage):
    # NOTE: Still returns not implemented for execution; but we can validate/plan here
    ok, plan, errors = build_runtime_plan(pkg.model_dump(), runtime_vars={})
    if ok:
        return CompileResponse(ok=True, graph_id="planned", message="Plan ready")
    return CompileResponse(ok=False, message="; ".join(errors)[:512])
```

---

## Tests

### `tests/test_tokens.py`
```python
from app.context.tokens import expand, get_registry

def test_unknown_token_left_intact():
    s = expand("Hello {unknown_token}")
    assert s == "Hello {unknown_token}"

def test_default_tokens_present():
    reg = get_registry()
    for k in ["tenant_id","correlation_id","request_id","now_iso","date_yyyy_mm_dd","user_details"]:
        assert k in reg

def test_user_details_injection():
    s = expand("User={user_details}", {"user_details": {"name":"Jamie","title":"DE"}})
    assert "Jamie" in s and "DE" in s
```

### `tests/test_prompt_assembly.py`
```python
from app.prompt.assembler import assemble_system_prompt

def test_prompt_assembly_order():
    agent = {
        "systemInstructions": "Hello {tenant_id}.",
        "styleGuide": "Be brief.",
        "context": {"injectOrgPreamble": False, "vars":{"lang":"en"}},
    }
    prompt = assemble_system_prompt(agent, {"tenant_id":"t-1"})
    assert "### STYLE GUIDE" in prompt
    assert "### INSTRUCTIONS" in prompt
    assert "Hello t-1." in prompt
    assert "### CONTEXT VARS" in prompt
```

### `tests/test_history_window.py`
```python
from app.history.window import window
import time

def test_lastn():
    msgs = [{"id":i} for i in range(5)]
    out = window(msgs, "LastN", n=2)
    assert [m["id"] for m in out] == [3,4]

def test_timebounded():
    now = time.time()
    msgs = [{"ts": now-10}, {"ts": now-1}]
    out = window(msgs, "TimeBounded", duration_ms=2000, now=now)
    assert out == [msgs[1]]

def test_none_mode():
    msgs = [{"id":1}]
    assert window(msgs, "None") == []
```

### `tests/test_ir_loader.py`
```python
import pytest
from app.ir.loader import build_runtime_plan

def pkg_min():
    agent = {
        "id":"a","kind":"agent.codeless","label":"A",
        "data":{
            "systemInstructions":"Hi {tenant_id}",
            "styleGuide":"Friendly",
            "model":{"provider":"openai","modelId":"gpt-4o","temperature":0.3,"topP":1,"maxTokens":64},
            "context":{"historyWindow":{"mode":"LastN","n":5},"injectOrgPreamble":False},
            "tools":{"policy":"Disabled","attached":[]}
        }
    }
    return {"meta":{"id":"m","name":"x","version":"1.0.0"},
            "nodes":[agent],
            "edges":[],
            "entryId":"a"}

@pytest.mark.anyio
async def test_build_runtime_plan_success():
    ok, plan, errs = build_runtime_plan(pkg_min(), {"tenant_id":"t-123"})
    assert ok
    assert plan["entryId"] == "a"
    assert "a" in plan["agentPrompts"]
    assert "t-123" in plan["agentPrompts"]["a"]
```

---

## Step‑By‑Step Implementation

1. **Settings**
   - Add optional org preamble inputs: `ORG_PREAMBLE_PATH` (file) or `ORG_PREAMBLE_TEXT` (inline).

2. **Token registry**
   - Implement `context/tokens.py` with a registry and simple `{token}` expander.
   - Supply default tokens using `current_context()` (PR‑02) for request/correlation/tenant ids, plus time and user details.

3. **History helpers**
   - Add `history/window.py` for `LastN` and `TimeBounded` modes.

4. **Prompt assembler**
   - Compose preamble + style + instructions (expanded) + context vars.
   - Unknown tokens must remain literally unchanged.

5. **IR loader**
   - Use `validate_and_normalize()` (PR‑04). If valid, produce a runtime plan containing `agentPrompts` per agent node.

6. **Tests**
   - Cover tokens, prompt assembly, history windowing, and runtime plan success path.

---

## Acceptance Criteria
- [ ] Token registry with default tokens (`tenant_id`, `correlation_id`, `request_id`, `now_iso`, `date_yyyy_mm_dd`, `user_details`).
- [ ] `expand()` leaves unknown tokens untouched and JSON‑serializes non‑string values.
- [ ] `assemble_system_prompt()` produces ordered sections and honors `injectOrgPreamble` and `context.vars`.
- [ ] History windowing correctly returns slices for `LastN` and `TimeBounded`.
- [ ] `build_runtime_plan()` returns `ok=True` and includes `agentPrompts` for each `agent.codeless` node.
- [ ] Tests pass with **≥80%** coverage for modules added in this PR.
- [ ] No changes to execution semantics yet (compilation/execution will use these in later PRs).

## Validation
```bash
pytest -q
# (optional) quick import checks
python - <<'PY'
from app.context.tokens import expand
print(expand("Now={now_iso} Tenant={tenant_id}", {"tenant_id":"tenant-x"}))
PY
```

---

## Manual Pre/Post Items
- **Org preamble file:** If using a preamble file, create `docs/org_preamble.md` (or set `ORG_PREAMBLE_TEXT`).
- **Schema file:** Ensure `schemas/orchestration_ir.schema.json` is present (from PR‑01).
- **Token usage in IR:** Start using tokens like `{tenant_id}` or `{user_details}` in your agent instructions; they will be expanded at compile/execution time.
- **History timestamps:** If you plan to use `TimeBounded`, ensure messages include a `ts` field (unix seconds).

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
