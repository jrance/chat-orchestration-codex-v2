"""Thread-safe in-memory checkpoint backend with TTL and size limits."""

from __future__ import annotations

import heapq
import threading
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterator, Tuple

from ..models import Checkpoint


class MemoryCheckpointBackend:
    """Store checkpoints in-process with TTL pruning and optional size cap."""

    def __init__(self, *, ttl_seconds: int | None = None, max_bytes: int | None = None) -> None:
        self._lock = threading.RLock()
        self._store: Dict[str, Checkpoint] = {}
        self._sizes: Dict[str, int] = {}
        self._ttl_seconds = ttl_seconds if ttl_seconds and ttl_seconds > 0 else None
        self._max_bytes = max_bytes if max_bytes and max_bytes > 0 else None
        self._total_bytes = 0

    # -- internal helpers -----------------------------------------------------

    def _apply_ttl(self, checkpoint: Checkpoint) -> Checkpoint:
        if self._ttl_seconds:
            expires = checkpoint.created_at + timedelta(seconds=self._ttl_seconds)
            checkpoint = checkpoint.with_updates(expires_at=expires)
        return checkpoint

    def _expire_locked(self) -> None:
        if not self._store:
            return
        now = datetime.now(timezone.utc)
        expired = [run_id for run_id, cp in self._store.items() if cp.expires_at and cp.expires_at <= now]
        for run_id in expired:
            self._delete_locked(run_id)

    def _delete_locked(self, run_id: str) -> None:
        existing = self._store.pop(run_id, None)
        if existing is None:
            return
        size = self._sizes.pop(run_id, 0)
        if size:
            self._total_bytes = max(0, self._total_bytes - size)

    def _evict_locked(self) -> None:
        if self._max_bytes is None:
            return
        if self._total_bytes <= self._max_bytes:
            return
        heap: list[Tuple[float, str]] = [
            (cp.created_at.timestamp(), run_id) for run_id, cp in self._store.items()
        ]
        heapq.heapify(heap)
        while self._total_bytes > self._max_bytes and heap:
            _, run_id = heapq.heappop(heap)
            self._delete_locked(run_id)

    # -- public API -----------------------------------------------------------

    def save(self, checkpoint: Checkpoint) -> None:
        """Persist a checkpoint snapshot."""

        snapshot = self._apply_ttl(checkpoint.copy())
        size = snapshot.size_bytes()

        with self._lock:
            self._expire_locked()
            previous = self._store.get(snapshot.run_id)
            if previous is not None:
                prior_size = self._sizes.get(snapshot.run_id, 0)
                self._total_bytes = max(0, self._total_bytes - prior_size)
            self._store[snapshot.run_id] = snapshot
            self._sizes[snapshot.run_id] = size
            self._total_bytes += size
            self._evict_locked()

    def load(self, run_id: str) -> Checkpoint | None:
        """Return a checkpoint copy if present and not expired."""

        with self._lock:
            self._expire_locked()
            snapshot = self._store.get(run_id)
            if snapshot is None:
                return None
            return snapshot.copy()

    def delete(self, run_id: str) -> None:
        """Remove persisted checkpoint for run."""

        with self._lock:
            self._delete_locked(run_id)

    def clear(self) -> None:
        """Remove all stored checkpoints (used primarily in tests)."""

        with self._lock:
            self._store.clear()
            self._sizes.clear()
            self._total_bytes = 0

    def prune_expired(self) -> None:
        """Explicitly prune expired checkpoints."""

        with self._lock:
            self._expire_locked()

    def run_ids(self) -> Iterator[str]:
        """Return an iterator of currently stored run identifiers."""

        with self._lock:
            self._expire_locked()
            return iter(list(self._store.keys()))

    @property
    def total_bytes(self) -> int:
        with self._lock:
            self._expire_locked()
            return self._total_bytes


__all__ = ["MemoryCheckpointBackend"]
