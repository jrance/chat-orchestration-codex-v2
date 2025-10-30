"""Shared API schemas for run execution and status endpoints."""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field, field_validator


class RunStatusEnum(str, Enum):
    """Lifecycle states returned from run-status endpoints."""

    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    ERROR = "error"


class ResumeKind(str, Enum):
    """Kinds of resume payloads supported by the runtime."""

    USER_MESSAGE = "user_message"
    ROUTER_CHOICE = "router_choice"
    MODERATION_ACK = "moderation_ack"
    CONTINUE = "continue"


class ResumeChoice(BaseModel):
    """Router resume payload describing the target branch."""

    target: str = Field(..., description="Router label or node id to continue with.")


class RunRequest(BaseModel):
    """Request payload for starting a new run."""

    orchestration: Dict[str, Any] = Field(alias="orchestration")
    input: Any | None = None
    thread_id: str | None = Field(default=None, alias="threadId")
    options: Dict[str, Any] | None = None

    model_config = {"populate_by_name": True}


class ResumeRequest(BaseModel):
    """Request payload for resuming a paused run."""

    kind: ResumeKind
    message: str | Dict[str, Any] | None = None
    choice: ResumeChoice | None = None
    metadata: Dict[str, Any] | None = None

    @field_validator("message")
    @classmethod
    def _ensure_non_empty(cls, value: Any) -> Any:
        if value is None:
            return value
        if isinstance(value, str):
            return value.strip() or value
        return value


class SSEInfo(BaseModel):
    """Server-sent-events metadata returned with run status."""

    url: str


class PauseInfo(BaseModel):
    """HITL pause metadata surfaced to clients."""

    reason: str | None = None
    prompt: str | None = None
    targets: list[str] | None = None
    metadata: Dict[str, Any] | None = None


class RunStatus(BaseModel):
    """Standardized run status envelope."""

    run_id: str = Field(alias="runId")
    thread_id: str = Field(alias="threadId")
    status: RunStatusEnum
    sse: SSEInfo
    pause: PauseInfo | None = None
    usage: Dict[str, Any] | None = None
    metadata: Dict[str, Any] | None = None

    model_config = {"populate_by_name": True}


class ErrorDetail(BaseModel):
    """Structured error payload returned by the API."""

    code: str
    message: str
    status: int | None = None
    run_id: str | None = Field(default=None, alias="runId")

    model_config = {"populate_by_name": True}


class ErrorEnvelope(BaseModel):
    """Top-level error envelope aligning with OpenAI style."""

    error: ErrorDetail


__all__ = [
    "ErrorDetail",
    "ErrorEnvelope",
    "PauseInfo",
    "ResumeChoice",
    "ResumeKind",
    "ResumeRequest",
    "RunRequest",
    "RunStatus",
    "RunStatusEnum",
    "SSEInfo",
]
