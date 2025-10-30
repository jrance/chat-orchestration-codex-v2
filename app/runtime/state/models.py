"""Dataclasses describing persisted run state and checkpoints."""

from __future__ import annotations

import copy
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Dict, Mapping, MutableMapping

from app.compiler.types import OrchestratorState


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class RunStatus(str, Enum):
    """Lifecycle status for an orchestrator run."""

    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    ERROR = "error"

    @classmethod
    def from_raw(cls, raw: str | None, default: "RunStatus" = RUNNING) -> "RunStatus":
        if not raw:
            return default
        try:
            return cls(str(raw).strip().lower())
        except ValueError:
            return default


@dataclass(slots=True)
class Checkpoint:
    """Snapshot of the orchestrator's graph state."""

    run_id: str
    state: OrchestratorState
    status: RunStatus = RunStatus.RUNNING
    thread_id: str | None = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=_utcnow)
    expires_at: datetime | None = None
    token: str | None = None

    def copy(self) -> "Checkpoint":
        """Return a deep copy of the checkpoint."""

        return Checkpoint(
            run_id=self.run_id,
            state=copy.deepcopy(self.state),
            status=self.status,
            thread_id=self.thread_id,
            metadata=copy.deepcopy(self.metadata),
            created_at=self.created_at,
            expires_at=self.expires_at,
            token=self.token,
        )

    def with_updates(self, **kwargs: Any) -> "Checkpoint":
        """Return a modified copy with provided field overrides."""

        payload: Dict[str, Any] = {}
        for key, value in kwargs.items():
            if hasattr(self, key):
                payload[key] = value
        return replace(self, **payload)

    def size_bytes(self) -> int:
        """Approximate payload size for eviction heuristics."""

        try:
            import json

            encoded = json.dumps(self.state, separators=(",", ":"), ensure_ascii=False)
            return len(encoded.encode("utf-8"))
        except Exception:
            return 0


@dataclass(slots=True)
class RunState:
    """Durable representation of orchestrator state + metadata."""

    run_id: str
    thread_id: str | None
    status: RunStatus
    state: OrchestratorState
    metadata: Dict[str, Any] = field(default_factory=dict)
    updated_at: datetime = field(default_factory=_utcnow)
    created_at: datetime = field(default_factory=_utcnow)
    pause_metadata: Dict[str, Any] = field(default_factory=dict)
    pause_reason: str | None = None
    token: str | None = None

    @classmethod
    def from_checkpoint(cls, checkpoint: Checkpoint) -> "RunState":
        """Hydrate a run state from a checkpoint snapshot."""

        metadata = dict(checkpoint.metadata)
        pause_meta = metadata.pop("pause_metadata", {})
        pause_reason = metadata.pop("pause_reason", None)
        return cls(
            run_id=checkpoint.run_id,
            thread_id=checkpoint.thread_id,
            status=checkpoint.status,
            state=copy.deepcopy(checkpoint.state),
            metadata=metadata,
            pause_metadata=copy.deepcopy(pause_meta) if isinstance(pause_meta, Mapping) else {},
            pause_reason=pause_reason if isinstance(pause_reason, str) else None,
            token=checkpoint.token,
        )

    def copy(self) -> "RunState":
        return RunState(
            run_id=self.run_id,
            thread_id=self.thread_id,
            status=self.status,
            state=copy.deepcopy(self.state),
            metadata=copy.deepcopy(self.metadata),
            updated_at=self.updated_at,
            created_at=self.created_at,
            pause_metadata=copy.deepcopy(self.pause_metadata),
            pause_reason=self.pause_reason,
            token=self.token,
        )

    def as_checkpoint(self, *, ttl_seconds: int | None = None) -> Checkpoint:
        """Create a checkpoint snapshot representation."""

        expires_at = None
        if ttl_seconds and ttl_seconds > 0:
            expires_at = _utcnow() + timedelta(seconds=int(ttl_seconds))
        metadata = dict(self.metadata)
        if self.pause_metadata:
            metadata["pause_metadata"] = copy.deepcopy(self.pause_metadata)
        if self.pause_reason:
            metadata["pause_reason"] = self.pause_reason
        return Checkpoint(
            run_id=self.run_id,
            state=copy.deepcopy(self.state),
            status=self.status,
            thread_id=self.thread_id,
            metadata=metadata,
            token=self.token,
            expires_at=expires_at,
        )


def mark_paused(state: OrchestratorState, metadata: Mapping[str, Any] | None) -> None:
    """Update raw orchestrator state to reflect paused metadata."""

    if metadata is None:
        return
    pause_meta = state.setdefault("pause_metadata", {})
    if isinstance(pause_meta, MutableMapping):
        pause_meta.update(copy.deepcopy(metadata))


def now_ts() -> float:
    """Return a monotonic-ish timestamp for TTL evaluation."""

    return time.time()


__all__ = ["Checkpoint", "RunState", "RunStatus", "mark_paused", "now_ts"]
