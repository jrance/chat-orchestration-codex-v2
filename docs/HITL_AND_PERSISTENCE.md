# Human-in-the-Loop Resume & Persistence

This guide describes how the runtime pauses, resumes, and persists orchestration state when Human-in-the-Loop (HITL) intervention is required.

## Pause Flow

1. The router detects an `AskUserClarify` fallback and marks the run `status="paused"`.
2. Pause metadata (`reason`, `prompt`, `targets`) is persisted via the checkpoint backend and surfaced in `RunStatus.pause`.
3. Clients surface the pause prompt to the operator and collect a response.

## Resume Flow

Resume requests are sent to `POST /v1/execute/{runId}/resume` with a JSON body matching `ResumeRequest`.

- `router_choice` – choose a router target via `{ "choice": { "target": "Policy Agent" } }`.
- `user_message` – provide additional customer input via `{ "message": "Please escalate to HR." }`.
- `continue` / `moderation_ack` – clear pause metadata and continue from the last checkpoint.

### cURL Example

```bash
curl -X POST "https://host/v1/execute/$RUN_ID/resume" \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Tenant-ID: demo" \
  -H "Content-Type: application/json" \
  -d '{
        "kind": "router_choice",
        "choice": {"target": "Policy Agent"}
      }'
```

## Persistence Architecture

- **Checkpoint Manager** – wraps pluggable backends (`memory`, `redis`) and LangGraph saver hooks.
- **Run State Store** – tracks high-level metadata (invocation count, last response, pause payloads).
- **Redis Backend** – uses fakeredis for local/tests (`REDIS_EMULATOR=true`), promoting to real Redis when installed.

The runtime saves a checkpoint after every node execution. During resume, the checkpoint is mutated with the resume payload and replayed to completion, emitting new telemetry (`telemetry.resume.start` / `.completed`).

> **Environment precedence:** For backward compatibility, the factory resolves the checkpointer kind in this order:
> 1) `CHECKPOINTER_KIND` (legacy env override).
> 2) `settings.CHECKPOINTER_BACKEND` (configuration default such as `"memory"`).
> Unknown values raise `ValueError`. CI can force the Redis stub by setting `CHECKPOINTER_KIND=redis` with `REDIS_URL=fakeredis://` and `REDIS_EMULATOR=true`.

## SSE & Status

Every response includes an `sse.url` so clients can resume streaming (`/v1/execute/stream?runId=...`). `RunStatus.pause` surfaces HITL metadata when paused, and is cleared automatically after a successful resume.

- Tool lifecycle events (`response.tool_result.created` / `response.tool_result.done`) now appear on the main SSE stream immediately around runtime tool execution. Detailed payloads remain available via the telemetry stream when `X-Telemetry: verbose` is enabled.
