# LLM Provider Adapter

The runtime communicates with the Gemini via Apigee gateway using an OpenAI
Responses–compatible HTTP interface. Requests are sent via `httpx.AsyncClient`
with OAuth bearer tokens fetched from Apigee.

## Configuration

| Variable | Description |
| --- | --- |
| `OPENAI_BASE_URL` | Base URL for the gateway (e.g. `https://apigee.example/openai`) |
| `APIGEE_TOKEN_URL` | OAuth token endpoint |
| `APIGEE_CLIENT_ID` / `APIGEE_CLIENT_SECRET` | Client credentials |
| `APIGEE_AUDIENCE` | Optional audience claim for the token request |
| `HTTP_TIMEOUT_SECONDS` | Default timeout when issuing requests |
| `HTTP_MAX_RETRIES` / `HTTP_RETRY_BACKOFF_MS` | Retry/backoff tuning |

All API calls propagate the execution headers provided by the FastAPI layer:
`X-Tenant-Id`, `X-Correlation-Id`, `X-Request-Id`, `X-Timestamp`,
`X-Client-Id`, and `X-Telemetry`.

## Endpoints

### `POST /v1/responses`

Sends a synchronous request. The payload mirrors the OpenAI Responses API, with
text-based inputs shaped as:

```json
{
  "model": "gpt-4o",
  "input": [
    {"role": "system", "content": "..." },
    {"role": "user", "content": "..." }
  ],
  "response_format": {
    "type": "json_schema",
    "json_schema": {...}
  }
}
```

When structured output is enabled the adapter switches to `json_mode` and will
retry/repair responses up to the configured `maxRepairAttempts`.

### `POST /v1/responses` (streaming)

Calling `post_responses_stream()` yields individual SSE frames. Events are
forwarded verbatim, expecting OpenAI-style types such as:

* `response.created`
* `response.output_text.delta`
* `response.completed`

The runtime injects `run_id` / `thread_id` metadata before exposing the events
to FastAPI's `StreamingResponse`, allowing the front-end to render incremental
token updates and final usage accounting.

## Telemetry

LLM invocations publish telemetry envelopes via `TelemetryStreamer`:

* `telemetry.llm.request` – before issuing HTTP requests
* `telemetry.llm.response` – after receiving responses (including streaming)

Payloads are automatically redacted when log redaction is enabled or when a
request opts into redaction.
