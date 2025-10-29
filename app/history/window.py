"""History windowing utilities."""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional


Message = Dict[str, Any]


def last_n(messages: List[Message], n: int) -> List[Message]:
    """Return the last ``n`` messages (empty if ``n`` is <= 0)."""
    if n <= 0:
        return []
    return messages[-n:]


def time_bounded(
    messages: List[Message],
    duration_ms: int,
    now: Optional[float] = None,
) -> List[Message]:
    """Return messages whose ``ts`` is within ``duration_ms`` of ``now``."""
    if duration_ms <= 0:
        return []
    current = now if now is not None else time.time()
    cutoff = current - (duration_ms / 1000.0)
    return [
        message
        for message in messages
        if float(message.get("ts", 0.0)) >= cutoff
    ]


def window(
    messages: List[Message],
    mode: Optional[str],
    n: int | None = None,
    duration_ms: int | None = None,
    now: Optional[float] = None,
) -> List[Message]:
    """Return a bounded message list according to the selected window mode."""
    if mode is None:
        return []
    lowered = str(mode).lower()
    if lowered == "none":
        return []
    if lowered == "lastn":
        return last_n(messages, int(n or 0))
    if lowered == "timebounded":
        return time_bounded(messages, int(duration_ms or 0), now=now)
    return []


__all__ = ["last_n", "time_bounded", "window"]
