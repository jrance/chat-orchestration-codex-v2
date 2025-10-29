# PROMPT FOR CODEX

Implement the following PR and validate that everything has been completed fully and that all tests pass and all acceptance criteria is met. Feel free to modify the recommended code if there is a better and cleaner approach.

---

# PR-10 of 14 — Tool System (Policy, LLMHidden, AgentOverride) + MCP Plumbing

## PR Title
Implement tool registry + policy engine and wire tool-calling into Codeless Agent; add initial MCP plumbing (PR 10 of 14)

## Description
This PR delivers the **first-class tool execution layer** and integrates it with the Codeless Agent and our OpenAI‑compatible provider:

- A **Tool Registry** where first-party tools are defined, discovered, and registered.
- A **Tool Policy** implementation that obeys IR settings (`Disabled`, `Auto`, `AlwaysAsk`, `Heuristic`), `maxCallsPerTurn`, `timeoutMs`, `parallelism`, PII redaction hooks, etc.
- Support for **LLMHidden** and **AgentOverride** parameters:
  - **LLMHidden**: argument exists only at invocation time; it is **omitted from the tool schema** sent to the model, but injected when we call the tool.
  - **AgentOverride**: argument is **visible in schema** but is programmatically **overwritten** right before invocation.
- **OpenAI Responses** tool events: parse model-emitted tool calls during streaming and drive tool execution loops; emit `response.tool_call.*` and `response.tool_result.*` passthrough events.
- **MCP plumbing (phase‑1)**: introduce an MCP client abstraction and register a simple “echo” MCP tool to demonstrate wiring from IR **MCPServer** nodes to tool execution. (Real MCP protocol features land in a later PR.)

## Purpose
- Allow codeless agents to call tools safely and predictably.
- Provide a clean extension point for enterprise tools and external MCP servers.
- Maintain strict control over arguments (LLMHidden/AgentOverride) and telemetry redaction.

## Scope
- New `tools/` package with registry, base types, policy, runner, and sample tools.
- New `mcp/` package with minimal client abstraction + in-memory registry of MCP servers from the IR.
- Update **codeless agent** to:
  - Publish a **tools schema** to the model (excluding LLMHidden args).
  - Execute model-requested tool calls (respecting policy/limits) and loop until final text or budget exhausted.
  - Stream tool call/results in **Responses** event taxonomy.
- Tests covering policy, LLMHidden/AgentOverride, timeouts, and basic MCP passthrough.

## Goals
- Async-first; robust timeouts and bounded concurrency.
- Minimal additional deps.
- ≥80% coverage on added modules.

---

## Files to Add / Modify

```
app/
├─ tools/
│  ├─ __init__.py
│  ├─ registry.py                 # tool registration + lookup
│  ├─ types.py                    # ToolSpec, ToolCall, ToolResult, Arg behaviors
│  ├─ policy.py                   # Disabled/Auto/AlwaysAsk/Heuristic
│  ├─ runner.py                   # execute tool calls w/ timeouts/parallelism
│  ├─ builtin/
│  │  ├─ __init__.py
│  │  ├─ echo.py                  # sample tool (echo)
│  │  └─ kv_store.py              # sample tool (get/set) used for tests
├─ mcp/
│  ├─ __init__.py
│  ├─ client.py                   # minimal MCP client abstraction
│  └─ registry.py                 # holds MCP servers from IR by id/label
├─ runtime/agents/codeless.py     # MOD: add tool loop + Responses events
├─ providers/openai_like/responses.py  # MOD: include tools schema in requests
├─ api/models.py                  # NEW: pydantic models for tool envelopes (if needed)
tests/
├─ tools/test_registry_and_policy.py
├─ tools/test_llmhidden_agentoverride.py
├─ tools/test_runner_timeouts.py
└─ mcp/test_mcp_registry_and_echo.py
docs/
└─ TOOLS_AND_MCP.md               # NEW: authoring tools; LLMHidden/AgentOverride semantics
```

### IR expectations (recap)
- **Tool nodes** include:
  ```json
  { "id": "...",
    "kind": "tool",
    "label": "Policy Search Tool",
    "data": {
      "name": "Tool",
      "toolId": "tool:policy-search",
      "version": "2.1.0",
      "argsSchema": { "type":"object","properties":{ "query":{"type":"string"} }, "required":["query"] },
      "parameterOverrides": {
         "apiKey": { "value": "abc123", "mode": "LLMHidden" },
         "topN":   { "value": 5,        "mode": "AgentOverride" }
      }
    }
  }
  ```
- **Codeless agent node** tool policy:
  ```json
  "tools": {
    "policy": "Auto",
    "timeoutMs": 10000,
    "maxCallsPerTurn": 2,
    "parallelism": 1,
    "redactPII": true,
    "attached": ["<tool-node-id-1>", "<tool-node-id-2>"]
  }
  ```
- **MCP server node**:
  ```json
  { "id":"srv-1", "kind":"mcpServer", "data": { "url":"https://onedrive.mcp.com", "protocol":"mcp/1.0" } }
  ```

---

## Implementation

### `app/tools/types.py`
```python
from __future__ import annotations
from typing import Any, Awaitable, Callable, Dict, Literal, TypedDict

ArgMode = Literal["Visible", "LLMHidden", "AgentOverride"]

class ArgSpec(TypedDict, total=False):
    mode: ArgMode              # defaults to Visible if omitted
    default: Any               # default used if arg missing

class ToolSpec(TypedDict, total=False):
    id: str                    # canonical id, e.g. "tool:policy-search"
    name: str                  # human label
    args_schema: Dict[str, Any]# JSON Schema (Visible-only view sent to LLM)
    arg_behaviors: Dict[str, ArgSpec]  # per-arg mode + default
    handler: Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]]
    timeout_ms: int

class ToolCall(TypedDict, total=False):
    id: str
    name: str
    arguments: Dict[str, Any]

class ToolResult(TypedDict, total=False):
    id: str
    name: str
    output: Dict[str, Any] | str
```

### `app/tools/registry.py`
```python
from __future__ import annotations
from typing import Dict
from .types import ToolSpec

_TOOLS: Dict[str, ToolSpec] = {}

def register_tool(spec: ToolSpec) -> None:
    assert spec.get("id")
    _TOOLS[spec["id"]] = spec

def get_tool(tool_id: str) -> ToolSpec | None:
    return _TOOLS.get(tool_id)

def list_tools() -> Dict[str, ToolSpec]:
    return dict(_TOOLS)
```

### `app/tools/policy.py`
```python
from __future__ import annotations

class ToolPolicy:
    DISABLED = "Disabled"
    AUTO = "Auto"
    ALWAYS_ASK = "AlwaysAsk"
    HEURISTIC = "Heuristic"

def can_use_tools(policy: str) -> bool:
    return policy in (ToolPolicy.AUTO, ToolPolicy.ALWAYS_ASK, ToolPolicy.HEURISTIC)
```

### `app/tools/runner.py`
```python
from __future__ import annotations
import asyncio
from typing import Dict, Any
from .registry import get_tool
from .types import ToolSpec

def filter_schema_visible(spec: ToolSpec) -> Dict[str, Any]:
    # Return schema filtered to exclude LLMHidden args.
    schema = dict(spec.get("args_schema") or {"type":"object","properties":{}})
    props = dict(schema.get("properties") or {})
    behaviors = spec.get("arg_behaviors") or {}
    visible = {k:v for k,v in props.items() if behaviors.get(k, {}).get("mode","Visible") != "LLMHidden"}
    schema["properties"] = visible
    required = [k for k in schema.get("required", []) if k in visible]
    if required:
        schema["required"] = required
    else:
        schema.pop("required", None)
    return schema

async def invoke_tool(tool_id: str, call_args: Dict[str, Any], per_call_timeout_ms: int | None = None) -> Dict[str, Any]:
    spec = get_tool(tool_id)
    if not spec:
        return {"error": f"unknown tool '{tool_id}'"}
    behaviors = spec.get("arg_behaviors") or {}
    args = dict(call_args or {})

    # Apply AgentOverride
    for name, meta in behaviors.items():
        if meta.get("mode") == "AgentOverride" and "default" in meta:
            args[name] = meta["default"]

    # Inject LLMHidden defaults
    for name, meta in behaviors.items():
        if meta.get("mode") == "LLMHidden" and "default" in meta:
            args[name] = meta["default"]

    timeout = (per_call_timeout_ms or spec.get("timeout_ms") or 10000) / 1000.0
    async def run():
        return await spec["handler"](args)

    return await asyncio.wait_for(run(), timeout=timeout)
```

### `app/tools/builtin/echo.py`
```python
from __future__ import annotations
from typing import Dict, Any
from ..registry import register_tool

async def _echo(args: Dict[str, Any]) -> Dict[str, Any]:
    return {"echo": args}

def register():
    register_tool({
        "id":"tool:echo",
        "name":"Echo Tool",
        "args_schema":{"type":"object","properties":{"text":{"type":"string"}},"required":["text"]},
        "arg_behaviors":{},
        "handler": _echo,
        "timeout_ms": 2000
    })
```

### `app/mcp/registry.py`
```python
from __future__ import annotations
from typing import Dict, Any

_SERVERS: Dict[str, Dict[str, Any]] = {}

def register_server(node_id: str, config: Dict[str, Any]) -> None:
    _SERVERS[node_id] = dict(config or {})

def get_server(node_id: str) -> Dict[str, Any] | None:
    return _SERVERS.get(node_id)

def list_servers() -> Dict[str, Dict[str, Any]]:
    return dict(_SERVERS)
```

### `app/mcp/client.py`
```python
from __future__ import annotations
from typing import Dict, Any

async def call_mcp_tool(server_cfg: Dict[str, Any], tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    # Phase-1 stub: just echo back the intent; real MCP protocol later.
    return {"mcp_server": server_cfg.get("url"), "tool": tool_name, "args": args}
```

### Wiring the IR → runtime (summary)
- When building **runtime plan** (PR‑05), collect:
  - `attached` tool node ids for each agent.
  - For each attached tool: resolve its `argsSchema` and `parameterOverrides`. Translate overrides into **arg_behaviors**:
    - `LLMHidden` → `mode="LLMHidden"` with `default=<value>` (don’t include in visible schema).
    - `AgentOverride` → `mode="AgentOverride"` with `default=<value>` (include in visible schema, **override before invoke**).
- Register **MCP servers** into `app/mcp/registry.py` with their configuration (id → config). Tools can refer to MCP via `toolId` pattern like `mcp:<serverNodeId>/<toolName>` (optional; included for parity).

### `app/providers/openai_like/responses.py` (MOD highlights)
- Accept `tools: List[Dict]]` and include in the request JSON when present:
  ```python
  body = {
    "model": model,
    "input": input_messages,
    "tools": tools,  # [{"type":"function","name":"...","description": "...","parameters": <JSON Schema>}]
    "stream": stream,
  }
  ```
- During **streaming**, forward tool events. Example mapping (passthrough):
  - `response.tool_call.created`
  - `response.tool_call.arguments.delta`
  - `response.tool_call.completed`
  - `response.tool_result.created`
  - `response.tool_result.completed`

### `app/runtime/agents/codeless.py` (MOD — add tool loop)
Key algorithm (sync path; streaming path mirrors via event pump):
```python
async def _visible_tools_payload(attached_specs) -> list[dict]:
    payload = []
    for spec in attached_specs:
        payload.append({
            "type": "function",
            "name": spec["id"],
            "description": spec.get("name",""),
            "parameters": filter_schema_visible(spec),
        })
    return payload

async def _tool_loop(messages, attached_specs, policy_cfg, budget):
    tools_payload = await _visible_tools_payload(attached_specs)
    turns = 0
    while turns < budget and can_use_tools(policy_cfg["policy"]):
        resp = await create_response(messages, model, extra_headers=headers, response_format=resp_format, tools=tools_payload)
        # If provider returns tool call(s) (non-streaming), execute them:
        calls = extract_tool_calls(resp)   # e.g., [{"id": "...", "name":"tool:echo", "arguments":"{...json...}"}]
        if not calls:
            break
        for call in calls:
            args = json.loads(call["arguments"] or "{}")
            result = await invoke_tool(call["name"], args, per_call_timeout_ms=policy_cfg.get("timeoutMs"))
            # append tool result to messages for the next round
            messages.append({"role":"tool","content": json.dumps(result),"tool_call_id": call["id"]})
        turns += 1
    return resp  # final model output
```

### Streaming behavior
- The engine’s `run_stream()` captures `response.tool_call.*` events; when a `tool_call.completed` arrives with fully-parsed arguments:
  - Execute the tool asynchronously.
  - Emit `response.tool_result.created` + `response.tool_result.completed` as SSE events (payload is the JSON result).
  - Feed a synthetic **assistant** event back into the model on the next round if the provider requires it; otherwise continue until `response.completed`.

---

## Tests

### `tests/tools/test_registry_and_policy.py`
- Register a sample tool; verify `filter_schema_visible()` removes LLMHidden args.
- Verify `can_use_tools()` logic and `maxCallsPerTurn` stop condition (simulate provider that always emits a tool call once).

### `tests/tools/test_llmhidden_agentoverride.py`
- Build a tool with:
  - `apiKey` as LLMHidden default `"abc"`
  - `topN` as AgentOverride default `5`
- Call `invoke_tool("tool:echo", {"text":"hi","topN":1})` and assert:
  - Handler receives `apiKey="abc"` (injected) and `topN=5` (overridden).

### `tests/tools/test_runner_timeouts.py`
- Define a tool handler that sleeps > timeout and assert `asyncio.TimeoutError` is raised by `invoke_tool`.

### `tests/mcp/test_mcp_registry_and_echo.py`
- Register an MCP server node config and call `call_mcp_tool()`; assert echo structure.

---

## Step‑By‑Step Implementation

1. **Create `tools/` package** with registry, types, policy, runner; add builtin `echo` tool and register it on app startup (or in tests).
2. **Translate IR overrides** (from `parameterOverrides`) into `arg_behaviors` when building the runtime plan (PR‑05 hook). Support `mode: LLMHidden|AgentOverride` + `value` → `default`.
3. **Modify provider adapter** to accept `tools` payload and include it in `/v1/responses` requests.
4. **Extend codeless agent**:
   - Build `tools` payload from attached tool specs (visible-only schema).
   - Implement the **tool loop** with `maxCallsPerTurn`, `timeoutMs`, `parallelism` (fan-out if multiple calls are returned), and message augmentation with tool outputs.
   - Emit telemetry for tool requests/results (respect redaction setting).
5. **Add MCP plumbing**: registry + `call_mcp_tool` stub; provide path for tools to forward to MCP where `toolId` starts with `mcp:` (or when a tool’s handler intentionally uses MCP).
6. **Add tests** and ensure coverage ≥ 80% for new modules.
7. **Docs**: author `docs/TOOLS_AND_MCP.md` describing tool authoring, LLMHidden/AgentOverride, and MCP server registration.

---

## Acceptance Criteria
- [ ] Tool Registry supports registering and resolving tools by `toolId` with JSON Schema and arg behaviors.
- [ ] Visible schema sent to the model **excludes** LLMHidden args.
- [ ] AgentOverride values are **enforced at invocation time**, ignoring model-proposed values for those keys.
- [ ] Codeless Agent executes tool calls honoring policy, budget (`maxCallsPerTurn`), timeout, and parallelism.
- [ ] SSE streaming includes `response.tool_call.*` and `response.tool_result.*` passthrough events when present.
- [ ] MCP registry/client in place; sample MCP call returns stub echo data.
- [ ] Tests pass with ≥80% coverage on new code.

---

## Validation
```bash
uv run pytest -q --cov=app --cov-report=term-missing

# Manual: run server and exercise a simple IR where the model calls tool:echo once.
# Verify that tool args shown to the model don't include LLMHidden fields,
# and that the final handler receives AgentOverride values.
```

---

## Manual Pre/Post Items
- Ensure your IR builder emits `parameterOverrides` entries like:
  ```json
  "parameterOverrides": {
    "apiKey": {"value":"***","mode":"LLMHidden"},
    "topN": {"value": 5, "mode":"AgentOverride"}
  }
  ```
- If you need real MCP now, create a follow-up PR to replace `call_mcp_tool()` with a proper client and wire authentication.

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
