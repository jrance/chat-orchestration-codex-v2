"""Shared runtime type definitions for orchestration patterns."""

from __future__ import annotations

from typing import Any, Literal, TypedDict


class ChildResult(TypedDict, total=False):
    """Represents the outcome of an individual concurrent child execution."""

    nodeId: str
    label: str
    status: Literal["completed", "timeout", "error", "cancelled"]
    output_text: str | None
    confidence: float | None
    usage: dict | None
    error: str | None
    meta: dict[str, Any]


class ConcurrentResult(TypedDict, total=False):
    """Aggregated result returned by a concurrent orchestration node."""

    strategy: Literal["Synthesize", "HighestScore", "FirstBest"]
    chosen: ChildResult | None
    children: list[ChildResult]
    merged_text: str | None
    rationale: str | None


__all__ = ["ChildResult", "ConcurrentResult"]
