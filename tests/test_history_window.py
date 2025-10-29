import time

from app.history.window import window


def test_lastn() -> None:
    messages = [{"id": i} for i in range(5)]
    selected = window(messages, "LastN", n=2)
    assert [m["id"] for m in selected] == [3, 4]


def test_timebounded() -> None:
    now = time.time()
    messages = [{"ts": now - 10}, {"ts": now - 1}]
    selected = window(messages, "TimeBounded", duration_ms=2000, now=now)
    assert selected == [messages[1]]


def test_none_mode() -> None:
    assert window([{"id": 1}], "None") == []
