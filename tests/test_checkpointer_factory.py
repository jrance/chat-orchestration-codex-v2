import pytest

from app.config.settings import settings
from app.runtime.checkpointer import InMemoryCheckpointer, get_checkpointer, reset_checkpointer


@pytest.fixture(autouse=True)
def _reset_checkpointer(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("CHECKPOINTER_KIND", raising=False)
    reset_checkpointer()
    yield
    monkeypatch.delenv("CHECKPOINTER_KIND", raising=False)
    reset_checkpointer()


def test_factory_returns_memory_checkpointer(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "checkpointer_backend", "redis", raising=False)
    monkeypatch.setenv("CHECKPOINTER_KIND", "memory")
    reset_checkpointer()

    checkpointer = get_checkpointer()
    assert isinstance(checkpointer, InMemoryCheckpointer)


def test_factory_raises_for_unknown_kind(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CHECKPOINTER_KIND", "unknown")
    reset_checkpointer()
    with pytest.raises(ValueError):
        get_checkpointer()


def test_memory_saver_presence(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "checkpointer_kind", "memory", raising=False)
    reset_checkpointer()
    checkpointer = get_checkpointer()

    from app.runtime.state.checkpointer import _resolve_inmemory_saver

    saver = checkpointer.get_saver()
    saver_cls = _resolve_inmemory_saver()

    assert isinstance(saver, saver_cls)


def test_factory_returns_redis_checkpointer(monkeypatch: pytest.MonkeyPatch):
    fakeredis = pytest.importorskip("fakeredis")
    monkeypatch.setenv("CHECKPOINTER_KIND", "redis")
    monkeypatch.setenv("REDIS_URL", "fakeredis://")
    monkeypatch.setenv("REDIS_EMULATOR", "true")
    reset_checkpointer()

    from app.runtime.state.checkpointer import RedisCheckpointer

    checkpointer = get_checkpointer()
    assert isinstance(checkpointer, RedisCheckpointer)
