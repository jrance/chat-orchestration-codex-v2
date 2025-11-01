"""Helpers for assembling OpenAI-compatible payloads."""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Mapping, Tuple

Turn = dict[str, Any]
SAFE_FN_RE = re.compile(r"[^a-zA-Z0-9_-]")


def sanitize_tool_name(raw: str) -> str:
    """Return a Chat API-safe tool function name."""

    sanitized = SAFE_FN_RE.sub("_", raw) or "tool"
    return sanitized[:64]


def _dedupe_tool_name(candidate: str, existing: Mapping[str, str]) -> str:
    """Ensure tool names remain unique after sanitization."""

    if candidate not in existing:
        return candidate

    suffix = 1
    base = candidate[:63]
    while True:
        suffix_text = f"_{suffix}"
        trimmed = base[: max(0, 64 - len(suffix_text))]
        name = f"{trimmed}{suffix_text}" if trimmed else sanitize_tool_name(suffix_text)
        if name not in existing:
            return name
        suffix += 1


def _map_tools_for_chat(tools: Iterable[Dict[str, Any]] | None) -> Tuple[List[Dict[str, Any]], Dict[str, str]]:
    chat_tools: List[Dict[str, Any]] = []
    reverse: Dict[str, str] = {}
    for tool in tools or []:
        internal_id = str(tool.get("id") or tool.get("name") or "tool")
        sanitized = sanitize_tool_name(internal_id)
        unique_name = _dedupe_tool_name(sanitized, reverse)
        reverse[unique_name] = internal_id
        chat_tools.append(
            {
                "type": "function",
                "function": {
                    "name": unique_name,
                    "description": str(tool.get("description") or ""),
                    "parameters": tool.get("parameters") or {"type": "object", "properties": {}},
                },
            }
        )
    return chat_tools, reverse


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
    *,
    tools: Iterable[Dict[str, Any]] | None = None,
    tool_choice: str | Dict[str, Any] | None = None,
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
    chat_tools, reverse_map = _map_tools_for_chat(tools or extras.pop("tools", None))
    if chat_tools:
        body["tools"] = chat_tools
    if tool_choice is not None:
        body["tool_choice"] = tool_choice

    visible_params = {k: v for k, v in extras.items() if v is not None and not str(k).startswith("_")}
    body.update(visible_params)
    if reverse_map:
        body["_tool_name_reverse_map"] = reverse_map
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
    extras.pop("tool_choice", None)
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
