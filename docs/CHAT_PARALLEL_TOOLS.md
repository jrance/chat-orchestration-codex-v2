## Parallel Tool Calls

The engine can execute multiple tool calls issued in a single assistant turn concurrently. This capability relies on both a global feature flag and per-agent configuration to bound parallelism.

### Configuration

- **Environment flag**: set `PARALLEL_TOOL_CALLS_ENABLED=true` (default) to allow chat payloads to request parallel function calling.
- **Agent limit**: each agent may specify `tools.parallelism` (integer ≥1). The runtime never executes more concurrent tool invocations than this value, even when the model emits more tool calls.
- **Policy**: agents must have a tool policy that allows calling functions (e.g. `Auto`, `Enabled`). If tools are disabled for an agent, the runtime ignores streamed tool calls.

When the feature is enabled the outbound Chat Completions payload includes:

```json
{
  "parallel_tool_calls": true,
  "tool_choice": "auto"
}
```

### Streaming Behaviour

When an assistant issues `N` tool calls during a streamed turn, the runtime accumulates the deltas, resolves internal tool identifiers, and executes the calls concurrently using an asyncio semaphore bounded by `tools.parallelism`. Each tool produces a complete lifecycle of Responses-style SSE events, annotated with `tool_call_id` and a deterministic `index`:

```
event: response.tool_call.created
data: {"tool_call_id":"call_fast","name":"tool:stocks.fast","function_name":"tool_stocks_fast","index":0}

event: response.tool_call.delta
data: {"tool_call_id":"call_fast","name":"tool:stocks.fast","function_name":"tool_stocks_fast","index":0,"status":"running"}

event: response.tool_result.created
data: {"tool_call_id":"call_fast","name":"tool:stocks.fast","function_name":"tool_stocks_fast","index":0,"result_count":1}

event: response.tool_result.done
data: {"tool_call_id":"call_fast","name":"tool:stocks.fast","function_name":"tool_stocks_fast","index":0,"output":{"value":"..."}}

event: response.tool_call.delta
data: {"tool_call_id":"call_fast","name":"tool:stocks.fast","function_name":"tool_stocks_fast","index":0,"status":"completed"}

event: response.tool_call.done
data: {"tool_call_id":"call_fast","name":"tool:stocks.fast","function_name":"tool_stocks_fast","index":0,"status":"completed"}
```

Concurrent executions finish independently, so a faster call can emit its `response.tool_result.done` before slower calls complete. Clients can rely on the `index` to restore the original model order when displaying results.

After all tool calls finish the runtime appends one tool-role message per call, then resumes the chat turn by issuing a follow-up `/v1/chat/completions` request. The stream continues with the model’s final assistant answer.

### Fallbacks

- If `PARALLEL_TOOL_CALLS_ENABLED` is `false` the payload omits the parallel flag but the engine still supports multiple tool calls sequentially.
- If a model ignores the flag and emits calls one-at-a-time, the semaphore naturally serialises execution.
- When `tools.parallelism` is `1`, the behaviour is identical to previous sequential execution.

### Notes

- Tool arguments are accumulated from streamed deltas; malformed JSON fragments fall back to `{}` to avoid hard failures.
- Tool responses are redacted when `tools.redactPII` is enabled — the SSE payloads indicate redaction via `redacted: true`.
- The runtime sets `tool_choice="auto"` automatically when the agent policy is Auto. For strict routing scenarios, adjust the policy or override the payload builder before invoking the engine.
