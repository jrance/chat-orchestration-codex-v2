import pytest

from app.config.settings import settings
from app.runtime.checkpointer import (
    InMemoryCheckpointer,
    get_checkpointer,
    reset_checkpointer,
)


@pytest.fixture(autouse=True)
def _reset_checkpointer():
    reset_checkpointer()
    yield
    reset_checkpointer()


def test_factory_returns_memory_checkpointer(monkeypatch):
    monkeypatch.setattr(settings, "checkpointer_kind", "memory", raising=False)
    checkpointer = get_checkpointer()
    assert isinstance(checkpointer, InMemoryCheckpointer)


def test_factory_raises_for_unknown_kind(monkeypatch):
    monkeypatch.setattr(settings, "checkpointer_kind", "unknown", raising=False)
    with pytest.raises(ValueError):
        get_checkpointer()


def test_memory_saver_presence(monkeypatch):
    monkeypatch.setattr(settings, "checkpointer_kind", "memory", raising=False)
    checkpointer = get_checkpointer()

    try:
        from langgraph.checkpoint import MemorySaver  # type: ignore
    except Exception:
        with pytest.raises(RuntimeError):
            checkpointer.get_saver()
        return

    saver = checkpointer.get_saver()
    assert isinstance(saver, MemorySaver)
