"""Helpers for assembling OpenAI-compatible payloads."""

from __future__ import annotations

from typing import Any, Iterable, Mapping

Turn = dict[str, Any]


def _clone_turn(turn: Mapping[str, Any]) -> Turn:
    cloned = dict(turn)
    role = cloned.get("role")
    if role is not None:
        cloned["role"] = str(role)
    return cloned


def _coerce_turns(turns: Iterable[Any] | Any) -> list[Turn]:
    if isinstance(turns, Mapping):
        return [_clone_turn(turns)]
    if isinstance(turns, str):
        return [{"role": "user", "content": turns}]
    if not isinstance(turns, Iterable) or isinstance(turns, (bytes, bytearray)):
        return []

    collected: list[Turn] = []
    for item in turns:
        if isinstance(item, Mapping):
            collected.append(_clone_turn(item))
        elif isinstance(item, str):
            collected.append({"role": "user", "content": item})
    return collected


def _normalize_params(params: Mapping[str, Any]) -> dict[str, Any]:
    normalized = {k: v for k, v in params.items() if v is not None}
    return normalized


def build_chat_messages(
    turns: Iterable[Turn] | Any,
    model: str,
    stream: bool,
    **params: Any,
) -> dict[str, Any]:
    """Return a Chat Completions-compatible payload."""

    messages = []
    for turn in _coerce_turns(turns):
        role = str(turn.get("role") or "user")
        message = dict(turn)
        message["role"] = role
        message.pop("input", None)
        message.pop("messages", None)
        messages.append(message)

    extras = dict(params)
    max_output = extras.pop("max_output_tokens", None)
    if max_output is not None:
        extras.setdefault("max_tokens", max_output)
    body = {
        "model": model,
        "messages": messages,
        "stream": bool(stream),
    }
    body.update(_normalize_params(extras))
    return body


def build_responses_body(
    turns: Iterable[Turn] | Any,
    model: str,
    stream: bool,
    **params: Any,
) -> dict[str, Any]:
    """Return a Responses API payload."""

    input_turns = _coerce_turns(turns)
    extras = dict(params)
    max_tokens = extras.pop("max_tokens", None)
    if max_tokens is not None:
        extras.setdefault("max_output_tokens", max_tokens)
    body = {
        "model": model,
        "input": input_turns,
        "stream": bool(stream),
    }
    body.update(_normalize_params(extras))
    return body


def normalize_turns(turns: Iterable[Any] | Any) -> list[Turn]:
    """Return a normalized list of turn dictionaries."""

    return _coerce_turns(turns)


__all__ = ["Turn", "build_chat_messages", "build_responses_body", "normalize_turns"]
