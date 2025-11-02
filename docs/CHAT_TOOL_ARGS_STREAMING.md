# Streaming Tool Calls

The Responses stream now emits canonical tool-call events that make argument and
result handling deterministic for the UI:

```
event: response.tool_call.arguments.delta
data: {"tool_call_id":"call_1","index":0,"name":"tool:search","function_name":"tool_search","arguments":"{\"query\":\"latest"}

event: response.tool_call.arguments.delta
data: {"tool_call_id":"call_1","index":0,"arguments":" news on Ukraine\""}

event: response.tool_call.arguments.done
data: {"tool_call_id":"call_1","index":0,"name":"tool:search","function_name":"tool_search","arguments":"{\"query\":\"latest news on Ukraine\"}"}

event: response.completed
data: { ..., "tool_calls": [{"id":"call_1","index":0,"name":"tool:search","function_name":"tool_search","arguments":"{\"query\":\"latest news on Ukraine\"}"}] }
```

Key changes:

- **Canonical namespace** – only `response.tool_call.arguments.delta|done` are emitted.
  Legacy `response.function_call_arguments.*` events have been removed.
- **Automatic aggregation** – the runtime buffers all deltas per `tool_call_id` and
  replaces the final `.done` payload with the complete JSON string. The same
  string is persisted in `response.completed.tool_calls[*].arguments` so the UI
  can render the final tool card even after the stream closes.
- **Stable metadata** – every payload includes the tool’s `index`, stable `id`,
  sanitized `function_name`, and original `name` when provided.

## Result previews

Tool results now stream alongside arguments:

```
event: response.tool_result.created
data: {"tool_call_id":"call_1","index":0,"name":"tool:search","function_name":"tool_search","result_count":3}

event: response.tool_result.delta
data: {"tool_call_id":"call_1","index":0,"name":"tool:search","function_name":"tool_search","preview":true,"output":"Top headline 1\nTop headline 2\n","truncated":false}

event: response.tool_result.done
data: {"tool_call_id":"call_1","index":0,"name":"tool:search","function_name":"tool_search","output":[{"title":"..."}, ...]}
```

- Non-redacted results stream a preview chunk via `response.tool_result.delta`
  capped by `TOOL_RESULT_MAX_CHARS`. The final `.done` event carries the full
  JSON payload.
- Redacted results emit `result_preview` on `response.tool_result.created` so the
  UI can still display summary metadata even when the output body is omitted.

## Handling guidance

- Buffer argument deltas keyed by `tool_call_id` until `.done` arrives, then parse
  the final JSON string. The engine guarantees that `.done` will contain a fully
  balanced payload.
- Update existing UI listeners to read `response.completed.tool_calls` instead of
  recomputing argument strings from earlier frames.
- When rendering result previews, prefer the `.delta` chunks when present;
  otherwise fall back to the `result_preview` field on `.created`.

See `docs/TELEMETRY.md` for details on telemetry events that accompany tool
invocation and result publication.
