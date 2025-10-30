"""Shared API schemas for run execution and status endpoints."""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, Optional

from pydantic import AliasChoices, BaseModel, Field, ConfigDict, field_validator


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

    orchestration: Dict[str, Any] = Field(
        alias="orchestration",
        validation_alias=AliasChoices("orchestration", "ir"),
        description="Normalized orchestration package describing agents, nodes, and edges.",
    )
    input: Any | None = None
    thread_id: str | None = Field(default=None, alias="threadId")
    options: Dict[str, Any] | None = None

    model_config = {
        "populate_by_name": True,
        "json_schema_extra": {
            "examples": [
                {
                    "orchestration": {
                        "meta": {"id": "pkg", "name": "Demo", "version": "1.0.0"},
                        "nodes": [],
                        "edges": [],
                        "entryId": "agent",
                    },
                    "input": "Summarize the latest release notes.",
                    "threadId": "thread-123",
                }
            ]
        },
    }


class ResumeRequest(BaseModel):
    """Request payload for resuming a paused run."""

    kind: ResumeKind
    message: str | Dict[str, Any] | None = Field(
        default=None,
        description="User-provided message when resuming with 'user_message'.",
    )
    choice: ResumeChoice | None = Field(
        default=None,
        description="Router selection when resuming a paused router node.",
    )
    metadata: Dict[str, Any] | None = Field(
        default=None,
        description="Additional metadata forwarded to the runtime for custom resumes.",
    )

    @field_validator("message")
    @classmethod
    def _ensure_non_empty(cls, value: Any) -> Any:
        if value is None:
            return value
        if isinstance(value, str):
            return value.strip() or value
        return value

    model_config = {
        "json_schema_extra": {
            "examples": [
                {"kind": "continue"},
                {"kind": "user_message", "message": "Please continue with option B."},
                {"kind": "router_choice", "choice": {"target": "Policy Agent"}},
            ]
        }
    }


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

    model_config = ConfigDict(
        populate_by_name=True,
        ser_json_exclude_none=True,
        json_schema_extra={
            "examples": [
                {
                    "runId": "run-123",
                    "threadId": "thread-123",
                    "status": "paused",
                    "sse": {"url": "/v1/execute/stream?runId=run-123"},
                    "pause": {
                        "reason": "router.ask_user",
                        "prompt": "Which policy should we cite?",
                        "targets": ["Policy Agent", "Benefits Agent"],
                    },
                    "usage": {"input_tokens": 42, "output_tokens": 18},
                }
            ]
        },
    )


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

    model_config = {
        "json_schema_extra": {
            "examples": [
                {"error": {"code": "RUN_NOT_FOUND", "message": "Unknown run", "status": 404}}
            ]
        }
    }


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
