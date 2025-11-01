import pytest

from app.providers.openai_like.payloads import build_chat_messages, build_responses_body


def test_build_responses_body_maps_turns_and_max_tokens() -> None:
    turns = [
        {"role": "system", "content": "policy"},
        {"role": "user", "content": "hello"},
    ]

    payload = build_responses_body(
        turns,
        model="gpt-4o",
        stream=True,
        temperature=0.2,
        max_tokens=256,
    )

    assert payload["model"] == "gpt-4o"
    assert payload["stream"] is True
    assert payload["max_output_tokens"] == 256
    assert "max_tokens" not in payload
    assert payload["input"][0]["role"] == "system"
    assert payload["input"][1]["content"] == "hello"


@pytest.mark.parametrize("stream_flag", [True, False])
def test_build_chat_messages_threads_tools(stream_flag: bool) -> None:
    turns = [
        {"role": "system", "content": "policy"},
        {"role": "assistant", "content": "partial"},
        {"role": "tool", "tool_call_id": "call-1", "name": "lookup", "content": "{}"},
    ]

    payload = build_chat_messages(
        turns,
        model="gpt-4o",
        stream=stream_flag,
        temperature=0.3,
        max_output_tokens=512,
    )

    assert payload["model"] == "gpt-4o"
    assert payload["stream"] is stream_flag
    assert payload["max_tokens"] == 512
    assert "max_output_tokens" not in payload
    assert [message["role"] for message in payload["messages"]] == ["system", "assistant", "tool"]
    assert payload["messages"][2]["tool_call_id"] == "call-1"
