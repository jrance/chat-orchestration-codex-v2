# Tools & MCP Integration

This document captures the phase-one tooling system that lands in PR-010. It covers how tools are discovered, how argument overrides work, and what wiring exists today for Model Context Protocol (MCP) servers.

## Tool Registry

All first-party tools live in `app/tools`. The registry keeps an in-memory map from `toolId` ➜ `ToolSpec`. Builtins are registered automatically when `app.tools` is imported. Additional specs can be registered at runtime by calling:

```python
from app.tools import register_tool

register_tool(
    {
        "id": "tool:weather",
        "name": "Weather",
        "args_schema": {...},
        "handler": weather_handler,
    }
)
```

Tests and the runtime may override specs (for example to apply per-tenant argument defaults). The registry exposes `get_tool`, `list_tools`, and `clear_tools` for inspection and cleanup.

## Argument Behaviours

Tool arguments support different visibility modes via the `arg_behaviors` map:

- **Visible** (default) – present in the schema sent to the LLM and passed through unchanged.
- **LLMHidden** – omitted from the tool schema. The runtime injects the configured `default` value before invocation.
- **AgentOverride** – exposed in the schema, but the runtime always overwrites the provided value with `default`.

Example:

```json
"parameterOverrides": {
  "apiKey": {"mode": "LLMHidden", "value": "secret"},
  "topN": {"mode": "AgentOverride", "value": 5}
}
```

The overrides above ensure the model never sees `apiKey` and that `topN` evaluates to `5` even if the model suggests otherwise.

## Tool Policy

Agent nodes configure tooling via `data.tools`. Key fields:

- `policy`: `Disabled`, `Auto`, `AlwaysAsk`, or `Heuristic`. Only the non-disabled modes allow tool usage.
- `maxCallsPerTurn`: positive integer limiting the number of tool invocations per agent turn (`0` or missing = unlimited).
- `parallelism`: maximum simultaneous tool executions.
- `timeoutMs`: default per-call timeout when the tool spec does not set one.
- `redactPII`: toggles telemetry redaction for tool payloads.

The runtime enforces policy during the tool loop:

1. Call the provider with the assembled message history and the visible tool schemas.
2. If the response contains tool calls and policy allows, execute them (honouring overrides and timeouts).
3. Append tool results as `tool` role messages.
4. Re-issue the provider call with the augmented conversation until text is produced or the call budget is exhausted.

Executed tool results are stored on the agent scratch pad (`runtime.scratch.agents[...]`) to aid diagnostics.

## Model Context Protocol (MCP)

MCP servers are represented as `mcpServer` nodes in the IR. `build_runtime_plan()` indexes their configuration and makes it available to the agent runtime. The runtime registers servers in `app.mcp.registry` and exposes a phase-one client (`app.mcp.call_mcp_tool`) that currently echoes the request for deterministic tests.

Tools can forward to MCP by using identifiers of the form `mcp:<serverId>/<toolName>`. During execution the runtime resolves `<serverId>` against the registry and invokes the MCP client instead of a local handler.

Future work will replace the stub client with a full MCP transport and extend the registry to handle credentials.

## Authoring Checklist

1. Register the tool (builtin or custom) with a unique `toolId`.
2. Define JSON schema for visible parameters; rely on `LLMHidden`/`AgentOverride` for sensitive defaults.
3. Attach the tool node to the agent in the IR and set the desired policy.
4. (Optional) Define `mcpServer` nodes and reference them from `mcp:<server>/<tool>` identifiers.
5. Write unit tests covering handlers, overrides, and timeouts. New modules should maintain ≥80 % coverage.

With these pieces in place codeless agents can safely orchestrate tool execution and surface telemetry-rich events end-to-end.
