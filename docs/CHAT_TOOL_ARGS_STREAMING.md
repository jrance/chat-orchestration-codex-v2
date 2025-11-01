# Chat Tool Arguments In Streaming

OpenAI-style chat streaming delivers tool call arguments as _incremental_ string deltas. Each SSE frame may contain a tiny fragment such as `"{"`, `"query"`, or `"}"`. Joining the pieces before the turn is finished produces invalid JSON and leads to decoding errors.

## Bad: parse per-fragment

```python
for delta in stream:
    if "tool_calls" in delta:
        args_fragment = delta["tool_calls"][0]["function"]["arguments"]
        json.loads(args_fragment)  # raises on `"query"`
```

## Good: buffer until finish

```python
buffers[index].write(args_fragment)
...
if finish_reason == "tool_calls":
    full_json = buffers[index].getvalue()
    arguments = json.loads(full_json)
```

The engine now keeps one buffer per `(index, id)` pair, waits for `finish_reason == "tool_calls"`, and only then parses the final string. A lightweight bracket-balance guard catches providers that omit the finish reason but still emit complete JSON.

Remember that the model receives **sanitized** function names (see `docs/OPENAI_CHAT_TOOLS.md`). Use the reverse map to translate the sanitized name back to the internal tool id before executing the call.
