from datetime import datetime, timedelta

from app.runtime.state.backends.memory import MemoryCheckpointBackend
from app.runtime.state.models import Checkpoint, RunStatus


def test_memory_backend_save_and_delete():
    backend = MemoryCheckpointBackend(ttl_seconds=60)
    checkpoint = Checkpoint(run_id="run-memory", state={"foo": "bar"})

    backend.save(checkpoint)
    loaded = backend.load("run-memory")
    assert loaded is not None
    assert loaded.run_id == "run-memory"
    assert loaded.status is RunStatus.RUNNING

    backend.delete("run-memory")
    assert backend.load("run-memory") is None


def test_memory_backend_ttl(monkeypatch):
    backend = MemoryCheckpointBackend(ttl_seconds=1)
    checkpoint = Checkpoint(run_id="run-expire", state={"foo": "bar"})
    backend.save(checkpoint)

    import app.runtime.state.backends.memory as memory_module

    original_datetime = memory_module.datetime

    class FutureDateTime(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            base = original_datetime.now(tz)
            return base + timedelta(seconds=5)

    monkeypatch.setattr(memory_module, "datetime", FutureDateTime)
    try:
        assert backend.load("run-expire") is None
    finally:
        monkeypatch.setattr(memory_module, "datetime", original_datetime)

