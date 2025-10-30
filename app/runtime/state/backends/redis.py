"""Redis-backed checkpoint backend with fakeredis fallback for tests."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Mapping

from ..models import Checkpoint, RunStatus

try:  # pragma: no cover - optional dependency
    import redis  # type: ignore
except Exception:  # pragma: no cover
    redis = None

try:  # pragma: no cover - optional dependency
    import fakeredis  # type: ignore
except Exception:  # pragma: no cover
    fakeredis = None


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:  # pragma: no cover - defensive
        return None


class RedisCheckpointBackend:
    """Persist checkpoints in Redis with optional fakeredis emulation."""

    def __init__(
        self,
        *,
        url: str | None = None,
        ttl_seconds: int | None = None,
        namespace: str = "orch",
        emulate: bool | None = None,
    ) -> None:
        self._ttl_seconds = ttl_seconds if ttl_seconds and ttl_seconds > 0 else None
        self._namespace = namespace.strip(":") if namespace else "orch"
        resolved_url = url or os.getenv("REDIS_URL") or "redis://localhost:6379/0"

        emulate_env = os.getenv("REDIS_EMULATOR")
        emulate_flag = emulate
        if emulate_flag is None:
            emulate_flag = False
            if emulate_env and emulate_env.lower() in {"1", "true", "yes"}:
                emulate_flag = True
            if resolved_url.startswith("fakeredis://"):
                emulate_flag = True

        if emulate_flag:
            if fakeredis is None:  # pragma: no cover - runtime guard
                raise RuntimeError("fakeredis is required for REDIS_EMULATOR mode")
            self._client = fakeredis.FakeStrictRedis(decode_responses=True)
        else:
            if redis is None:  # pragma: no cover - runtime guard
                raise RuntimeError("redis (redis-py) is required for Redis backend")
            self._client = redis.Redis.from_url(resolved_url, decode_responses=True)  # type: ignore[arg-type]

    # -- internal helpers -------------------------------------------------

    def _key(self, run_id: str) -> str:
        return f"{self._namespace}:runs:{run_id}"

    @staticmethod
    def _serialize(checkpoint: Checkpoint) -> Mapping[str, Any]:
        return {
            "run_id": checkpoint.run_id,
            "state": checkpoint.state,
            "status": checkpoint.status.value,
            "thread_id": checkpoint.thread_id,
            "metadata": checkpoint.metadata,
            "created_at": checkpoint.created_at.isoformat(),
            "expires_at": checkpoint.expires_at.isoformat() if checkpoint.expires_at else None,
            "token": checkpoint.token,
        }

    @staticmethod
    def _deserialize(payload: Mapping[str, Any]) -> Checkpoint:
        status = RunStatus.from_raw(str(payload.get("status") or ""), RunStatus.RUNNING)
        created_at = _parse_datetime(str(payload.get("created_at") or "")) or datetime.now(timezone.utc)
        expires_at = _parse_datetime(payload.get("expires_at"))
        metadata = dict(payload.get("metadata") or {})
        state = payload.get("state") or {}
        if not isinstance(state, dict):
            state = {}
        return Checkpoint(
            run_id=str(payload.get("run_id") or ""),
            state=state,  # type: ignore[arg-type]
            status=status,
            thread_id=payload.get("thread_id"),
            metadata=metadata,
            created_at=created_at,
            expires_at=expires_at,
            token=payload.get("token"),
        )

    # -- public API -------------------------------------------------------

    def save(self, checkpoint: Checkpoint) -> None:
        """Persist the checkpoint snapshot into Redis."""

        key = self._key(checkpoint.run_id)
        payload = json.dumps(self._serialize(checkpoint), separators=(",", ":"), ensure_ascii=False)
        if self._ttl_seconds:
            self._client.set(key, payload, ex=self._ttl_seconds)
        else:
            self._client.set(key, payload)

    def load(self, run_id: str) -> Checkpoint | None:
        """Load a checkpoint snapshot for ``run_id`` if present."""

        data = self._client.get(self._key(run_id))
        if not data:
            return None
        if isinstance(data, bytes):  # pragma: no cover - decode guard
            data = data.decode("utf-8")
        try:
            payload = json.loads(data)
        except json.JSONDecodeError:  # pragma: no cover - corrupted payload
            return None
        if not isinstance(payload, Mapping):
            return None
        return self._deserialize(payload)

    def delete(self, run_id: str) -> None:
        """Remove the stored checkpoint for ``run_id``."""

        self._client.delete(self._key(run_id))


__all__ = ["RedisCheckpointBackend"]
