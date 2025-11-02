from datetime import datetime, timezone

import pytest

from app.runtime.state.backends.redis import RedisCheckpointBackend
from app.runtime.state.models import Checkpoint, RunStatus


@pytest.mark.parametrize("toggle", ["true", "TRUE", "1"])
def test_redis_backend_uses_fakeredis_when_emulator_enabled(monkeypatch: pytest.MonkeyPatch, toggle: str):
    monkeypatch.setenv("REDIS_EMULATOR", toggle)
    backend = RedisCheckpointBackend(url="fakeredis://", emulate=None, ttl_seconds=None, namespace="test")

    checkpoint = Checkpoint(
        run_id="run-sync",
        state={"value": 1},
        status=RunStatus.RUNNING,
        thread_id="thread-1",
        metadata={"source": "test"},
        created_at=datetime.now(timezone.utc),
        expires_at=None,
        token=None,
    )

    backend.save(checkpoint)
    loaded = backend.load("run-sync")
    assert loaded is not None
    assert loaded.run_id == "run-sync"
    assert loaded.state == {"value": 1}

    backend.delete("run-sync")
    assert backend.load("run-sync") is None
