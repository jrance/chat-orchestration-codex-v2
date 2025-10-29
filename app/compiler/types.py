"""Shared types for orchestration compiler."""

from __future__ import annotations

from typing import Any, Dict, List, Literal, TypedDict

Role = Literal["system", "user", "assistant", "tool"]


class Message(TypedDict, total=False):
    """Represents a chat message within orchestrator state."""

    role: Role
    content: str | dict | list
    ts: float


class OrchestratorState(TypedDict, total=False):
    """State carried between LangGraph nodes."""

    run_id: str
    input: Dict[str, Any]
    messages: List[Message]
    scratch: Dict[str, Any]
    route: Dict[str, Any]
    result: Dict[str, Any]
