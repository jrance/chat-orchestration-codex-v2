"""Checkpoint manager abstractions wrapping LangGraph savers."""

from __future__ import annotations

import copy
import os
from functools import lru_cache
from typing import Any, Dict, Mapping, Optional, Type

from app.config.settings import settings

from .backends import MemoryCheckpointBackend, RedisCheckpointBackend
from .models import Checkpoint, RunState, RunStatus


@lru_cache(maxsize=1)
def _resolve_inmemory_saver() -> Type[Any]:
    """Return an in-memory saver class that matches the installed LangGraph version."""

    try:
        from langgraph.checkpoint.memory import InMemorySaver  # type: ignore

        return InMemorySaver
    except Exception:
        pass

    try:
        from langgraph.checkpoint.memory import MemorySaver  # type: ignore

        return MemorySaver
    except Exception:
        pass

    try:
        from langgraph.checkpoint import MemorySaver  # type: ignore

        return MemorySaver
    except Exception as exc:  # pragma: no cover - escalated failure path
        raise ImportError(
            "LangGraph memory checkpointer not found. Install a compatible version of langgraph."
        ) from exc


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
        self._saver: Any | None = None

        try:
            saver_cls = _resolve_inmemory_saver()
        except ImportError:
            self._saver = None
        else:
            self._saver = saver_cls()

    def get_saver(self) -> Any:
        if self._saver is None:
            raise RuntimeError(
                "LangGraph in-memory saver unavailable; ensure langgraph is installed and compatible."
            )
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


class InMemoryCheckpointer(CheckpointManager):
    """Checkpoint manager backed by the in-memory saver."""

    def __init__(self) -> None:
        backend = MemoryCheckpointBackend(
            ttl_seconds=settings.state_ttl(),
            max_bytes=settings.state_max_bytes_limit(),
        )
        super().__init__(backend)


class RedisCheckpointer(CheckpointManager):
    """Checkpoint manager backed by Redis (optionally fakeredis for tests)."""

    def __init__(self) -> None:
        backend = RedisCheckpointBackend(
            url=settings.redis_url,
            ttl_seconds=settings.state_ttl(),
            namespace="orch",
            emulate=settings.redis_emulator if settings.redis_emulator else None,
        )
        super().__init__(backend)


_CHECKPOINTER: Optional[CheckpointManager] = None
_CHECKPOINTER_KIND: Optional[str] = None


def _resolve_kind() -> str:
    env_kind = os.getenv("CHECKPOINTER_KIND")
    if env_kind and env_kind.strip():
        return env_kind.strip().lower()

    settings_kind = getattr(settings, "checkpointer_kind", None)
    if settings_kind and str(settings_kind).strip():
        return str(settings_kind).strip().lower()

    backend_kind = getattr(settings, "checkpointer_backend", None)
    if backend_kind and str(backend_kind).strip():
        return str(backend_kind).strip().lower()

    return "memory"


def get_checkpointer() -> CheckpointManager:
    """Return singleton checkpoint manager honouring legacy env precedence."""

    global _CHECKPOINTER, _CHECKPOINTER_KIND

    kind = _resolve_kind()

    if kind not in {"memory", "redis"}:
        raise ValueError(f"Unknown checkpointer kind: {kind}")

    if _CHECKPOINTER is None or _CHECKPOINTER_KIND != kind:
        if kind == "memory":
            _CHECKPOINTER = InMemoryCheckpointer()
        else:
            _CHECKPOINTER = RedisCheckpointer()
        _CHECKPOINTER_KIND = kind

    return _CHECKPOINTER


def reset_checkpointer() -> None:
    """Reset cached checkpoint manager (used by tests)."""

    global _CHECKPOINTER, _CHECKPOINTER_KIND
    _CHECKPOINTER = None
    _CHECKPOINTER_KIND = None


__all__ = [
    "_resolve_inmemory_saver",
    "Checkpoint",
    "CheckpointManager",
    "Checkpointer",
    "InMemoryCheckpointer",
    "RedisCheckpointer",
    "RunState",
    "RunStatus",
    "get_checkpointer",
    "reset_checkpointer",
]
