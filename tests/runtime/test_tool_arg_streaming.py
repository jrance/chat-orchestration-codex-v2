import json

from app.runtime.agents.codeless import PendingToolCalls, ToolRuntime


def test_accumulates_argument_fragments_without_ids():
    calls = PendingToolCalls()
    # initial chunk carries identifier with empty args
    calls.upsert(
        {
            "type": "response.function_call_arguments.delta",
            "id": "call_123",
            "function_name": "tool_ddgs_search",
            "arguments": "",
        }
    )
    fragments = [
        '{"',
        "query",
        '":"',
        "latest",
        " news",
        " on",
        " Ukraine",
        '","',
        "vertical",
        '":"',
        "news",
        '","',
        "max",
        "Results",
        '":"',
        "5",
        '","',
        "s",
        "af",
        "earch",
        '":"',
        "moder",
        "ate",
        '","',
        "region",
        '":"',
        "us",
        "-en",
        '","',
        "time",
        "Limit",
        '":"',
        "d",
        '"}',
    ]
    for part in fragments:
        calls.upsert(
            {
                "type": "response.function_call_arguments.delta",
                "function_name": "tool_ddgs_search",
                "arguments": part,
            }
        )

    runtime = ToolRuntime(
        specs={"tool:ddgs.search": {"id": "tool:ddgs.search", "name": "DuckDuckGo Search"}},
        payload=[],
        policy="Auto",
        max_calls=None,
        timeout_ms=None,
        parallelism=1,
        redact=False,
        enabled=True,
        mcp_servers={},
        name_reverse={"tool_ddgs_search": "tool:ddgs.search"},
        sanitized_names={"tool:ddgs.search": "tool_ddgs_search"},
    )

    plan = calls.to_plan(runtime, turn_finished=True)
    assert len(plan) == 1
    args = json.loads(plan[0].arguments_json)
    assert args["vertical"] == "news"
    assert "query" in args and "Ukraine" in args["query"]
