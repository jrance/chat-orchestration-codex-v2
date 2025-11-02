# Execute API

The Execute API compiles orchestration packages into LangGraph applications and runs them with either a synchronous JSON response or an OpenAI Responses-compatible event stream.

## Headers

All endpoints require the following headers:

- `X-Tenant-Id` *(required)* – Tenant identifier; must match `meta.tenantId` in the IR when present.
- `X-Correlation-Id`, `X-Request-Id` *(optional)* – Request tracing identifiers; generated when omitted.
- `X-Timestamp` *(optional)* – RFC3339 timestamp; defaults to current UTC time.
- `X-Client-Id` *(optional)* – Downstream client identifier.
- `X-Telemetry` *(optional)* - `none` (default), `basic`, `verbose`, or `trace`. Enables telemetry streaming when not `none`.
- `X-Telemetry-Redact` *(optional)* - `safe` (default), `full`, or `none`. Controls payload redaction for telemetry events.

Telemetry headers configure the server-side telemetry stream only; they are not forwarded to LLM providers or tools. Additional headers prefixed with `X-Extra-` are forwarded to downstream HTTP calls.

## POST /v1/execute

Runs the orchestration synchronously and returns a JSON body once completed.

```jsonc
{
  "ir": { /* orchestration package */ },
  "input": "hello world",
  "options": {
    "timeout": 30
  }
}
```

Successful response:

```json
{
  "ok": true,
  "runId": "run_123",
  "threadId": "thread_run_123",
  "output_text": "stub response",
  "usage": {"output_tokens": 2},
  "message": "completed"
}
```

Errors return a 4xx/5xx JSON payload of the form:

```json
{"detail": "Tenant mismatch: header 'tenant-1' does not match IR meta 'tenant-2'"}
```

## POST /v1/execute/stream

Streams OpenAI Responses API-compatible events via Server-Sent Events. The initial request body matches `/v1/execute`.

The response has `Content-Type: text/event-stream` and emits events such as:

```
event: response.created
data: {"run_id":"run_123","status":"in_progress"}

event: response.output_text.delta
data: {"delta":"stub ","role":"assistant"}

event: response.tool_call.arguments.delta
data: {"tool_call_id":"call_1","index":0,"name":"tool:search","arguments":"{\"query\":\"stub\"}"}

event: response.completed
data: {"run_id":"run_123","output_text":"stub response","usage":{"output_tokens":2},"tool_calls":[{"id":"call_1","arguments":"{\"query\":\"stub\"}"}]}
```

When telemetry is enabled (`X-Telemetry` header set to `basic`/`verbose`/`trace`), the response also includes:

```
X-Telemetry-Stream-Url: /v1/telemetry/stream?runId=run_123
```

## POST /v1/execute/{runId}/resume

Returns the latest persisted result for a run. This is a synchronous endpoint used to hydrate UI clients before full resume support is available.

```
POST /v1/execute/run_123/resume
{
  "input": "additional context"
}
```

## GET /v1/telemetry/stream

Streams telemetry events (e.g., `telemetry.llm.request`, `telemetry.llm.response`) for a run when telemetry is enabled. Consumers should connect using the URL returned in `X-Telemetry-Stream-Url`.

```
event: telemetry.llm.request
data: {"provider":"openai","model":"gpt-4o","endpoint":"/v1/responses","request_id":"req-123","headers":{"X-Tenant-Id":"tenant-1","X-Correlation-Id":"corr-456"},"body_preview":"{\"model\":\"gpt-4o\",\"input\":[{\"role\":\"user\",\"content\":\"hello\"}]}"}
```

See `docs/TELEMETRY.md` for a full event catalog and configuration details.

## Examples

```bash
curl -X POST http://localhost:8000/v1/execute \
  -H "Content-Type: application/json" \
  -H "X-Tenant-Id: tenant-1" \
  -d @orchestration.json

curl -N -X POST http://localhost:8000/v1/execute/stream \
  -H "Content-Type: application/json" \
  -H "X-Tenant-Id: tenant-1" \
  -H "X-Telemetry: basic" \
  -d @orchestration.json
```

The streaming endpoint can be consumed with any SSE client and is compatible with OpenAI ChatKit token streaming.
