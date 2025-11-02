# Telemetry Stream

The telemetry stream exposes runtime milestones, model payload previews, and
tool execution notes over Server-Sent Events. It complements the Responses
stream so UI clients can render diagnostics without parsing verbose logs.

## Configuration

Telemetry is opt-in and controlled by environment defaults plus per-request
headers.

### Environment variables

| Variable | Default | Description |
| --- | --- | --- |
| `TELEMETRY_ENABLED` | `true` | Master switch for telemetry emission. |
| `TELEMETRY_LEVEL` | `basic` | `none`, `basic`, `verbose`, `trace`. Higher levels include richer payload previews. |
| `TELEMETRY_REDACTION` | `safe` | Redaction mode: `safe`, `full`, or `none`. |
| `TELEMETRY_PAYLOAD_MAX_CHARS` | `8192` | Maximum characters retained in LLM request/response bodies. |
| `TOOL_RESULT_MAX_CHARS` | `4096` | Preview limit for tool result streaming. |

### Request headers

- `X-Telemetry`: Overrides the level for a single run (`basic`, `verbose`,
  `trace`). Omit or set to `none` to disable streaming for the request.
- `X-Telemetry-Redact`: Overrides the redaction mode (`safe`, `full`, `none`).

These headers are **not** forwarded to the LLM provider or tool invocations.

### Stream discovery

When telemetry is active, `/v1/execute/stream` includes
`X-Telemetry-Stream-Url` in the response headers. Connect to that URL with an
SSE client to consume telemetry frames in parallel with the main Responses
stream.

## Event catalog

| Event | Description |
| --- | --- |
| `telemetry.span.start` / `telemetry.span.end` | Marks the beginning and end of a runtime turn. Includes span metadata, duration, and usage summaries. |
| `telemetry.llm.request` | Sanitized model request: provider, model, endpoint, headers, payload preview. |
| `telemetry.llm.response` | Sanitized model response: status, latency, usage, tool-call preview. |
| `telemetry.note` | Free-form milestone (e.g., tool start/finish, retries). |

Example payloads (`verbose` level, `safe` redaction):

```json
event: telemetry.llm.request
data: {
  "provider": "openai",
  "model": "gpt-4o",
  "endpoint": "/v1/responses",
  "request_id": "req-123",
  "headers": {
    "X-Tenant-Id": "tenant-1",
    "X-Correlation-Id": "corr-456",
    "X-Request-Id": "req-123",
    "X-Timestamp": "2025-11-02T08:00:00Z"
  },
  "body_preview": "{\"model\":\"gpt-4o\",\"input\":[{\"role\":\"user\",\"content\":\"latest news\"}]}"
}

event: telemetry.llm.response
data: {
  "provider": "openai",
  "model": "gpt-4o",
  "request_id": "req-123",
  "status_code": 200,
  "latency_ms": 1420.5,
  "usage": {"output_tokens": 128},
  "body_preview": "{\"output_text\":\"Here is an update...\"}",
  "tool_calls_preview": "[{\"id\":\"call_1\",\"type\":\"function\",\"function\":{\"name\":\"tool_search\"}}]"
}

event: telemetry.note
data: {
  "level": "debug",
  "message": "tool.start",
  "detail": {"tool_call_id":"call_1","tool":"tool:search","function":"tool_search","index":0}
}
```

`trace` level replaces `body_preview` with the full (still redacted) body up to
`TELEMETRY_PAYLOAD_MAX_CHARS` characters.

## Redaction modes

- `safe` (default): masks API keys, OAuth tokens, emails, phone numbers, and
  base64 blobs. Payloads are truncated to the configured limit.
- `full`: removes request/response bodies entirely, retaining only structural
  metadata (status, headers, usage).
- `none`: emits payloads as-is except for a minimal guard that masks obvious API
  keys (`sk-...`, `Bearer ...`) and applies truncation limits.

Header redaction keeps the following identifiers visible:
`X-Tenant-Id`, `X-Correlation-Id`, `X-Request-Id`, `X-Timestamp`, `X-OpenAI-Org`.
All other headers are replaced with stable hashes (`[hash:abcd1234]`).

## Tool integration

Tool execution emits `telemetry.note` entries at start/finish and reuses the
Responses stream for detailed argument/result payloads. See
`docs/CHAT_TOOL_ARGS_STREAMING.md` for the SSE contract.
