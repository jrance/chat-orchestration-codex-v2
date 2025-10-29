# Execute & Telemetry APIs

The orchestration runtime exposes synchronous and streaming execution APIs compatible with the OpenAI Responses format. Headers must be supplied with every request.

## Required Headers

| Header | Description |
| --- | --- |
| X-Tenant-Id | Tenant that owns the orchestration package. Must match meta.tenantId when present. |
| X-Correlation-Id | Optional correlation identifier. Generated when omitted. |
| X-Request-Id | Optional request identifier. Generated when omitted. |
| X-Timestamp | RFC3339 timestamp. Generated when omitted. |
| X-Telemetry | 
one (default), asic, or erbose. Controls telemetry streaming. |

## POST /v1/execute

Executes the orchestration once and returns the final response.

`ash
curl -s -X POST http://localhost:8000/v1/execute \
  -H "Content-Type: application/json" \
  -H "X-Tenant-Id: tenant-123" \
  -d '{
        "ir": {"meta": {"id": "pkg", "name": "Demo", "version": "1.0.0", "tenantId": "tenant-123"}, "nodes": [], "edges": []},
        "input": "Hello runtime"
      }'
`

Sample response:

`json
{
  "ok": true,
  "runId": "e9b0f5af9f7f4c7b8a7498bf8df49839",
  "threadId": "thread-e9b0f5af9f7f4c7b8a7498bf8df49839",
  "output_text": "Echo: Hello runtime",
  "usage": {
    "input_tokens": 2,
    "output_tokens": 3
  },
  "message": "completed"
}
`

## POST /v1/execute/stream

Streams OpenAI Responses-compatible SSE events (esponse.created, esponse.output_text.delta, esponse.completed).

`ash
curl -N -s -X POST http://localhost:8000/v1/execute/stream \
  -H "Content-Type: application/json" \
  -H "X-Tenant-Id: tenant-123" \
  -H "X-Telemetry: basic" \
  -d '{
        "ir": {"meta": {"id": "pkg", "name": "Demo", "version": "1.0.0", "tenantId": "tenant-123"}, "nodes": [], "edges": []},
        "input": "Stream this"
      }'
`

When telemetry is enabled the response includes X-Telemetry-Stream-Url, allowing clients to subscribe to /v1/telemetry/stream?runId=<id>.

## POST /v1/execute/{runId}/resume

Resumes a checkpointed run. The mock runtime replays the previously persisted output and appends any new input provided during resume.

`ash
curl -s -X POST http://localhost:8000/v1/execute/<runId>/resume \
  -H "Content-Type: application/json" \
  -H "X-Tenant-Id: tenant-123" \
  -d '{"input": "Additional instructions"}'
`

## GET /v1/telemetry/stream

Streams telemetry events for a given run. Requires X-Tenant-Id and optional X-Telemetry headers.

`ash
curl -N -s "http://localhost:8000/v1/telemetry/stream?runId=<runId>" \
  -H "X-Tenant-Id: tenant-123" \
  -H "X-Telemetry: basic"
`

Telemetry frames are delivered as SSE events named 	elemetry.* and contain JSON payloads suitable for dashboards.
