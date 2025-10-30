"""Placeholder redis-backed checkpoint backend.

The implementation intentionally raises at runtime to avoid pulling an
optional dependency in the open-source build. Integration tests can inject
their own subclass using fakeredis to validate behaviour without modifying
the main runtime package.
"""

from __future__ import annotations

from ..models import Checkpoint


class RedisCheckpointBackend:
    """Stub backend reserved for downstream deployments."""

    def __init__(self, *_args, **_kwargs) -> None:  # pragma: no cover - stub
        raise RuntimeError("Redis checkpoint backend not bundled in the OSS build")

    def save(self, _checkpoint: Checkpoint) -> None:  # pragma: no cover - stub
        raise RuntimeError("Redis checkpoint backend not bundled in the OSS build")

    def load(self, _run_id: str) -> Checkpoint | None:  # pragma: no cover - stub
        raise RuntimeError("Redis checkpoint backend not bundled in the OSS build")

    def delete(self, _run_id: str) -> None:  # pragma: no cover - stub
        raise RuntimeError("Redis checkpoint backend not bundled in the OSS build")


__all__ = ["RedisCheckpointBackend"]
