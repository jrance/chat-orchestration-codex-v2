# DuckDuckGo Search Tool (`tool:ddgs.search`)

## Overview
The DDGS engine tool exposes DuckDuckGo's web, news, and Wikipedia verticals without requiring API keys. It runs synchronously via `duckduckgo_search` under the hood and is wrapped in an async handler that enforces timeouts and produces Responses-style lifecycle events.

## Parameters
| UI control | Engine argument | Notes |
|------------|-----------------|-------|
| Query | `query` | Required search text. |
| Vertical | `vertical` | `web`, `news`, or `wikipedia`. Wikipedia mode auto-adds `site:wikipedia.org`. |
| Max Results | `maxResults` | Integer 1-50 (default 5). |
| Safe Search | `safesearch` | `off`, `moderate`, `strict` (default `moderate`). |
| Region | `region` | DuckDuckGo region code (default `us-en`). |
| Time Limit | `timeLimit` | Freshness window: `""`, `d`, `w`, `m`, `y`. |
| Site Filter | `siteFilter` | Array of hostnames; combined via `site:` syntax. |
| Must Include | `mustInclude` | Array of keywords that must appear in snippet/title. |
| Timeout (hidden) | `timeoutMs` | Agent override (default 6000ms) for the worker thread. |

Tool responses are normalized to:

```json
{
  "query": "open source llms",
  "vertical": "news",
  "count": 3,
  "results": [
    {
      "title": "Open-source LLM milestone",
      "url": "https://news.example.com/story",
      "snippet": "Independent labs release...",
      "source": "Example News",
      "published_at": "2024-05-15T12:00:00Z"
    }
  ]
}
```

## Agent Wiring Example
```json
{
  "id": "agent:news-demo",
  "kind": "agent.codeless",
  "data": {
    "model": {"modelId": "gpt-4o", "temperature": 0.2},
    "tools": {
      "policy": "Auto",
      "maxCalls": 3,
      "entries": [
        {
          "id": "tool:ddgs.search",
          "args": {
            "vertical": "news",
            "timeLimit": "d",
            "maxResults": 5,
            "safesearch": "moderate"
          }
        }
      ]
    }
  }
}
```

## Lifecycle Events
For each invocation the agent emits:

1. `response.tool_call.created` – tool ID, call ID, and argument keys.
2. `response.tool_result.created` – tool ID, call ID, status, and result count (when available).
3. `response.tool_result.done` – tool ID, call ID, and final status.

Events are redacted automatically when runtime redaction is enabled and are mirrored on the telemetry stream for debugging.
