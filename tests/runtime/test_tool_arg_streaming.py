import json

from app.runtime.agents.codeless import (
    PendingToolCalls,
    ToolRuntime,
    _parse_tool_arguments,
)


def _build_runtime() -> ToolRuntime:
    return ToolRuntime(
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


def test_accumulates_argument_fragments_without_ids():
    calls = PendingToolCalls()
    # initial chunk carries identifier with empty args
    calls.upsert(
        {
            "type": "response.tool_call.arguments.delta",
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
                    "type": "response.tool_call.arguments.delta",
                    "function_name": "tool_ddgs_search",
                    "arguments": part,
                }
            )

    plan = calls.to_plan(_build_runtime(), turn_finished=True)
    assert len(plan) == 1
    args = json.loads(plan[0].arguments_json)
    assert args["vertical"] == "news"
    assert "query" in args and "Ukraine" in args["query"]


def test_done_event_replaces_buffer_with_final_mapping():
    calls = PendingToolCalls()
    calls.upsert(
        {
            "type": "response.tool_call.arguments.delta",
            "id": "call_456",
            "function_name": "tool_ddgs_search",
            "arguments": '{"query":"incomplete"}',
        }
    )
    final_arguments = {"query": "latest news", "vertical": "news"}
    calls.upsert(
        {
            "type": "response.tool_call.arguments.done",
            "id": "call_456",
            "function_name": "tool_ddgs_search",
            "arguments": final_arguments,
        }
    )
    plan = calls.to_plan(_build_runtime(), turn_finished=True)
    assert len(plan) == 1
    expected_json = json.dumps(final_arguments)
    assert plan[0].arguments_json == expected_json
    assert plan[0].arguments == final_arguments


def test_duplicate_final_json_recovers_last_object():
    calls = PendingToolCalls()
    calls.upsert(
        {
            "type": "response.tool_call.arguments.delta",
            "id": "call_789",
            "function_name": "tool_ddgs_search",
            "arguments": "",
        }
    )
    duplicate_payload = '{"foo": 1}{"foo": 2}'
    calls.upsert(
        {
            "type": "response.tool_call.arguments.done",
            "id": "call_789",
            "function_name": "tool_ddgs_search",
            "arguments": duplicate_payload,
        }
    )
    plan = calls.to_plan(_build_runtime(), turn_finished=True)
    assert len(plan) == 1
    assert plan[0].arguments["foo"] == 2
    assert plan[0].arguments_json == '{"foo": 2}'

    recovered = _parse_tool_arguments(duplicate_payload)
    assert recovered == {"foo": 2}
