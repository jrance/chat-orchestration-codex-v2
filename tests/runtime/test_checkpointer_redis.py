import pytest

from app.config.settings import reload_settings
from app.runtime.state.checkpointer import get_checkpointer, reset_checkpointer
from app.runtime.state.models import RunStatus


@pytest.mark.anyio
async def test_redis_checkpointer_with_fakeredis(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CHECKPOINTER_BACKEND", "redis")
    monkeypatch.setenv("REDIS_URL", "fakeredis://")
    monkeypatch.setenv("REDIS_EMULATOR", "true")
    reload_settings()
    reset_checkpointer()

    manager = get_checkpointer()
    manager.save_checkpoint("redis-run", {"state": 1}, status=RunStatus.RUNNING, thread_id="thread-redis")
    loaded = manager.load_checkpoint("redis-run")
    assert loaded is not None
    assert loaded.run_id == "redis-run"
    assert loaded.status is RunStatus.RUNNING

    manager.mark_status("redis-run", RunStatus.COMPLETED)
    updated = manager.load_checkpoint("redis-run")
    assert updated is not None
    assert updated.status is RunStatus.COMPLETED

    manager.delete_checkpoint("redis-run")
    assert manager.load_checkpoint("redis-run") is None

    reset_checkpointer()
    monkeypatch.delenv("CHECKPOINTER_BACKEND")
    monkeypatch.delenv("REDIS_URL")
    monkeypatch.delenv("REDIS_EMULATOR")
    reload_settings()
