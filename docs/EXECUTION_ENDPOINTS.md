# Execution Endpoint Styles

The orchestration engine supports two wire formats when invoking the upstream
gateway. Both flows share authentication, retry, and telemetry behaviour; only
the request payload shape and endpoint path differ.

## Responses API (default)

- **Env**: `OPENAI_API_STYLE=responses`
- **Endpoint**: `POST /v1/responses`
- **Payload**: canonical `{"model","input","stream"}` plus optional keys such as
  `tools`, `response_format`, and `max_output_tokens`.
- **Streaming**: SSE frames (`response.created`, `response.output_text.delta`,
  `response.completed`, …) are relayed unchanged to the runtime.

Use this mode when the upstream service supports the OpenAI Responses schema
and event taxonomy. This is the recommended configuration for new deployments.

## Chat Completions compatibility

- **Env**: `OPENAI_API_STYLE=chat`
- **Endpoint**: `POST /v1/chat/completions`
- **Payload**: the engine maps canonical turns to `"messages"` and converts
  `max_output_tokens` → `max_tokens`. Tool configuration is forwarded verbatim.
- **Streaming**: incoming chat deltas are normalised into Responses-style event
  frames so downstream consumers continue to receive `response.*` events.

Toggle this mode when the gateway only exposes legacy Chat Completions routes.
The engine automatically reshapes the upstream JSON into the Responses format
(`output_text`, `usage`, tool call metadata) that the rest of the runtime
expects.

## Automatic fallback

Set `OPENAI_RESPONSES_FALLBACK_TO_CHAT=true` (default) to retry a failed
Responses request once as Chat Completions when the gateway returns a 400 that
mentions missing `"messages"` or unrecognised `"input"` fields. A debug log
records the attempted payload keys and a hint for future configuration updates.

Disable the fallback (`false`) when you want hard failures any time the gateway
rejects the canonical Responses schema.
