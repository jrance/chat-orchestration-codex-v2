# OpenAI Chat Tool Payloads

The OpenAI Chat Completions API expects tools to be declared with the `function`
shape. Each entry must look like:

```json
{
  "type": "function",
  "function": {
    "name": "tool_ddgs_search",
    "description": "DuckDuckGo Search",
    "parameters": {
      "type": "object",
      "properties": {
        "query": {"type": "string"}
      }
    }
  }
}
```

## Name Sanitization

Tool identifiers in the catalog can include characters that are invalid for the
Chat API. When building the outbound payload we:

- Replace any character outside `a-z`, `A-Z`, `0-9`, `_`, or `-` with `_`.
- Truncate the name to 64 characters (per API requirement).
- Deduplicate collisions by appending a counter suffix.

The original identifier is preserved in a reverse map
(`_tool_name_reverse_map`) so that streamed or synchronous tool calls can be
routed back to the correct engine tool.

> The reverse map is removed before the payload is sent to OpenAI; it only
> exists in-process so the runtime can translate tool call names back to
> their internal IDs.

## Tool Choice Policy

When an agent's tool policy is `Auto`, we forward `tool_choice: "auto"` in the
Chat payload. Other policies omit this field so the model does not attempt to
invoke tools.

## Summary

1. Declare tools using `{"type":"function","function":{...}}`.
2. Always sanitize function names; keep the reverse mapping internally.
3. Set `tool_choice: "auto"` when the agent policy allows automatic tool calls.
