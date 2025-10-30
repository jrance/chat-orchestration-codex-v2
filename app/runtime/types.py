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


class PauseMetadata(TypedDict, total=False):
    """Metadata persisted when a run is paused awaiting user input."""

    status: Literal["awaiting_user_input"]
    reason: str
    prompt: str | None
    targets: list[str]


class RouterDecision(TypedDict, total=False):
    """Decision payload recorded after evaluating a router node."""

    nodeId: str
    label: str
    targetLabel: str | None
    targetNodeId: str | None
    confidence: float | None
    rationale: str | None
    source: Literal["llm", "tie_break", "fallback", "paused", "none"]
    tieBreak: Literal["HighestConfidence", "DeterministicOrder", "PreferList"] | None
    fallbackMode: Literal["AskUserClarify", "DefaultChild", "SafeAgent", "Error"] | None
    allowBelowMinForTieBreak: bool
    llmTarget: str | None
    llmConfidence: float | None
    llmRationale: str | None
    usedFallback: bool
    hitl: PauseMetadata | None
    raw: dict[str, Any]


__all__ = ["ChildResult", "ConcurrentResult", "PauseMetadata", "RouterDecision"]
