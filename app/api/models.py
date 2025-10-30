"""Shared API models across v1 endpoints."""

from __future__ import annotations

from typing import Any, Dict

from pydantic import BaseModel, Field


class ToolCallPayload(BaseModel):
    """Normalized tool call envelope."""

    id: str
    name: str
    arguments: Dict[str, Any] = Field(default_factory=dict)


class ToolResultPayload(BaseModel):
    """Normalized tool result envelope."""

    id: str
    name: str
    output: Any


__all__ = ["ToolCallPayload", "ToolResultPayload"]
