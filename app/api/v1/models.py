"""Pydantic models for v1 endpoints."""

from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class OrchestrationPackage(BaseModel):
    """Minimal representation of a LangGraph orchestration package."""

    meta: Dict[str, Any]
    nodes: list[Dict[str, Any]]
    edges: list[Dict[str, Any]]
    entryId: Optional[str] = None


class ValidateResponse(BaseModel):
    """Response envelope for validation requests."""

    ok: bool = False
    message: str = "Not implemented"
    normalized: Optional[Dict[str, Any]] = None


class CompileResponse(BaseModel):
    """Response envelope for compilation requests."""

    ok: bool = False
    graph_id: Optional[str] = None
    message: str = "Not implemented"


class ExecuteRequest(BaseModel):
    """Request payload for execution endpoints."""

    package: OrchestrationPackage
    input: Dict[str, Any] = Field(default_factory=dict)


class ExecuteResponse(BaseModel):
    """Response envelope for execution endpoints."""

    ok: bool = False
    runId: Optional[str] = None
    message: str = "Not implemented"
