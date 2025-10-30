"""Checkpoint manager abstractions wrapping LangGraph savers."""

from __future__ import annotations

import copy
from typing import Any, Dict, Mapping, Optional

try:  # pragma: no cover - guard for build environments without langgraph
    from langgraph.checkpoint import MemorySaver  # type: ignore
except Exception:  # pragma: no cover
    MemorySaver = object  # type: ignore[assignment]

from app.config.settings import settings

from .backends import MemoryCheckpointBackend, RedisCheckpointBackend
from .models import Checkpoint, RunState, RunStatus


class Checkpointer:
    """Interface for orchestrator checkpoint managers."""

    def get_saver(self) -> Any:  # pragma: no cover - protocol hook
        raise NotImplementedError

    def save_checkpoint(
        self,
        run_id: str,
        state: Mapping[str, Any],
        *,
        status: RunStatus = RunStatus.RUNNING,
        thread_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        token: str | None = None,
    ) -> Checkpoint:
        raise NotImplementedError

    def load_checkpoint(self, run_id: str) -> Optional[Checkpoint]:
        raise NotImplementedError

    def delete_checkpoint(self, run_id: str) -> None:
        raise NotImplementedError

    def mark_status(
        self,
        run_id: str,
        status: RunStatus,
        *,
        metadata: Mapping[str, Any] | None = None,
    ) -> Optional[Checkpoint]:
        raise NotImplementedError


class CheckpointManager(Checkpointer):
    """Concrete manager combining backend persistence with LangGraph savers."""

    def __init__(
        self,
        backend: MemoryCheckpointBackend | RedisCheckpointBackend,
    ) -> None:
        self._backend = backend
        if MemorySaver is object:
            self._saver: Any = None
        else:
            self._saver = MemorySaver()  # type: ignore[call-arg]

    def get_saver(self) -> Any:
        if self._saver is None:
            raise RuntimeError("LangGraph MemorySaver unavailable; install langgraph>=1.0")
        return self._saver

    def save_checkpoint(
        self,
        run_id: str,
        state: Mapping[str, Any],
        *,
        status: RunStatus = RunStatus.RUNNING,
        thread_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        token: str | None = None,
    ) -> Checkpoint:
        snapshot = Checkpoint(
            run_id=run_id,
            state=copy.deepcopy(dict(state)),
            status=status,
            thread_id=thread_id,
            metadata=copy.deepcopy(dict(metadata)) if isinstance(metadata, Mapping) else {},
            token=token,
        )
        self._backend.save(snapshot)
        return snapshot

    def load_checkpoint(self, run_id: str) -> Optional[Checkpoint]:
        return self._backend.load(run_id)

    def delete_checkpoint(self, run_id: str) -> None:
        self._backend.delete(run_id)

    def mark_status(
        self,
        run_id: str,
        status: RunStatus,
        *,
        metadata: Mapping[str, Any] | None = None,
    ) -> Optional[Checkpoint]:
        checkpoint = self._backend.load(run_id)
        if checkpoint is None:
            return None
        merged = copy.deepcopy(checkpoint.metadata)
        if isinstance(metadata, Mapping):
            merged.update(copy.deepcopy(dict(metadata)))
        updated = checkpoint.with_updates(status=status, metadata=merged)
        self._backend.save(updated)
        return updated

    def restore_state(self, run_id: str) -> Optional[RunState]:
        checkpoint = self._backend.load(run_id)
        if checkpoint is None:
            return None
        return RunState.from_checkpoint(checkpoint)


_CHECKPOINTER: Optional[CheckpointManager] = None


def _build_backend(kind: str) -> MemoryCheckpointBackend | RedisCheckpointBackend:
    if kind == "memory":
        return MemoryCheckpointBackend(
            ttl_seconds=settings.state_ttl(),
            max_bytes=settings.state_max_bytes_limit(),
        )
    if kind == "redis":
        return RedisCheckpointBackend(
            url=settings.redis_url,
            ttl_seconds=settings.state_ttl(),
            namespace="orch",
            emulate=settings.redis_emulator,
        )
    raise ValueError(f"Unknown CHECKPOINTER_BACKEND '{kind}'")


def get_checkpointer() -> CheckpointManager:
    """Return singleton checkpoint manager based on settings."""

    global _CHECKPOINTER

    kind = (settings.checkpointer_backend or settings.checkpointer_kind or "memory").strip().lower()
    if not kind:
        kind = "memory"

    if _CHECKPOINTER is None or getattr(_CHECKPOINTER, "_kind", None) != kind:
        backend = _build_backend(kind)
        manager = CheckpointManager(backend)
        manager._kind = kind  # type: ignore[attr-defined]
        _CHECKPOINTER = manager
    return _CHECKPOINTER


def reset_checkpointer() -> None:
    """Reset cached checkpoint manager (used by tests)."""

    global _CHECKPOINTER
    _CHECKPOINTER = None


__all__ = [
    "Checkpoint",
    "CheckpointManager",
    "Checkpointer",
    "RunState",
    "RunStatus",
    "get_checkpointer",
    "reset_checkpointer",
]

