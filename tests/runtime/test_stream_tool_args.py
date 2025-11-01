from app.runtime.agents.codeless import PendingFunctionCall, _parse_tool_arguments


def test_arguments_assembly_done_tail_only():
    call = PendingFunctionCall(index=0)
    call.update(
        {
            "type": "response.function_call_arguments.delta",
            "arguments": '{"query":"latest Ukraine',
        }
    )
    call.update(
        {
            "type": "response.function_call_arguments.delta",
            "arguments": ' news","vertical":"news"}',
        }
    )
    call.update(
        {
            "type": "response.function_call_arguments.done",
            "arguments": "}",
        }
    )

    args = _parse_tool_arguments(call.arguments_text())
    assert args.get("query") == "latest Ukraine news"
    assert args.get("vertical") == "news"


def test_arguments_concat_two_objects_picks_last():
    args = _parse_tool_arguments('{"a": 1}{"b": 2}')
    assert args == {"b": 2}
