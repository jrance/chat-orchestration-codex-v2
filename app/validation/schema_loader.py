"""Utilities for loading the orchestration IR JSON schema."""

from __future__ import annotations

import json
import os
from typing import Any

from app.config.settings import settings

_cached_schema: dict[str, Any] | None = None
_cached_mtime: float | None = None


def load_schema() -> dict[str, Any]:
    """Load the orchestration JSON schema, caching by mtime for dev hot reload."""
    global _cached_schema, _cached_mtime

    path = settings.orch_schema_path
    stat = os.stat(path)

    if _cached_schema is not None and _cached_mtime == stat.st_mtime:
        return _cached_schema

    with open(path, "r", encoding="utf-8") as handle:
        _cached_schema = json.load(handle)

    _cached_mtime = stat.st_mtime
    return _cached_schema

