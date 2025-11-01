"""Utilities for assembling OpenAI Chat Completions messages."""

from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Mapping, Optional


class MessageBuilder:
    """Builds Chat Completions payloads with correct tool-calling choreography."""

    def __init__(self, *, system: Optional[str] = None) -> None:
        self._system = (system or "").strip()
        self._messages: List[Dict[str, Any]] = []

    @staticmethod
    def _merge_system(*parts: str) -> str:
        segments = [part.strip() for part in parts if part and str(part).strip()]
        if not segments:
            return ""
        merged: List[str] = []
        seen: set[str] = set()
        for segment in segments:
            if segment not in seen:
                merged.append(segment)
                seen.add(segment)
        return "\n\n".join(merged)

    def with_system(self, *parts: str) -> "MessageBuilder":
        """Merge additional system instructions."""

        self._system = self._merge_system(self._system, *parts)
        return self

    def add_user_text(self, text: Any) -> "MessageBuilder":
        """Append a user turn, stripping accidental 'text:' prefixes."""

        if isinstance(text, list):
            content = text
        else:
            raw = str(text or "")
            stripped = raw.lstrip()
            if stripped.lower().startswith("text:") and not stripped.lower().startswith("text://"):
                stripped = stripped[len("text:") :].lstrip()
            content = stripped
        self._messages.append({"role": "user", "content": content})
        return self

    def add_assistant_text(self, text: Any, **extras: Any) -> "MessageBuilder":
        """Append a plain assistant text message."""

        message: Dict[str, Any] = {"role": "assistant"}
        if isinstance(text, list):
            message["content"] = list(text)
        else:
            message["content"] = text if text is not None else ""
        message.update({k: v for k, v in extras.items() if v is not None})
        self._messages.append(message)
        return self

    def add_raw_message(self, message: Mapping[str, Any]) -> "MessageBuilder":
        """Append an arbitrary message dictionary."""

        payload = dict(message)
        payload["role"] = str(message.get("role") or "")
        self._messages.append(payload)
        return self

    def add_assistant_tool_calls(self, calls: Iterable[Mapping[str, Any]]) -> "MessageBuilder":
        """Append an assistant message requesting tool invocations."""

        tool_calls = []
        for call in calls:
            call_id = str(call.get("id") or call.get("tool_call_id") or call.get("name") or "")
            name = str(call.get("name") or "")
            arguments = call.get("arguments")
            if isinstance(arguments, str):
                arg_text = arguments
            else:
                try:
                    arg_text = json.dumps(arguments or {}, ensure_ascii=False)
                except (TypeError, ValueError):
                    arg_text = "{}"
            tool_calls.append(
                {
                    "id": call_id,
                    "type": "function",
                    "function": {"name": name, "arguments": arg_text},
                }
            )
        if tool_calls:
            self._messages.append({"role": "assistant", "content": "", "tool_calls": tool_calls})
        return self

    def add_tool_result(self, *, tool_call_id: str, name: str, result: Any) -> "MessageBuilder":
        """Append a tool result tied to a prior assistant.tool_calls entry."""

        if isinstance(result, str):
            content = result
        else:
            try:
                content = json.dumps(result, ensure_ascii=False)
            except (TypeError, ValueError):
                content = json.dumps({"value": result}, ensure_ascii=False)
        self._messages.append(
            {
                "role": "tool",
                "tool_call_id": tool_call_id,
                "name": name,
                "content": content,
            }
        )
        return self

    def build(self) -> List[Dict[str, Any]]:
        """Return the accumulated messages list."""

        assembled: List[Dict[str, Any]] = []
        if self._system:
            assembled.append({"role": "system", "content": self._system})
        assembled.extend(self._messages)
        return assembled


__all__ = ["MessageBuilder"]
