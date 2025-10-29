"""Thread-safe in-memory run state store."""

from __future__ import annotations

import copy
from threading import RLock
from typing import Dict, Optional

from .base import RunStateRecord, RunStateStore


class InMemoryRunStateStore(RunStateStore):
    """Simple dictionary-backed run state store for local/testing use."""

    def __init__(self) -> None:
        self._records: Dict[str, RunStateRecord] = {}
        self._lock = RLock()

    def put_state(self, record: RunStateRecord) -> None:
        with self._lock:
            self._records[record.run_id] = RunStateRecord(
                run_id=record.run_id,
                state=copy.deepcopy(record.state),
                metadata=copy.deepcopy(record.metadata),
            )

    def get_state(self, run_id: str) -> Optional[RunStateRecord]:
        with self._lock:
            stored = self._records.get(run_id)
            if stored is None:
                return None
            return RunStateRecord(
                run_id=stored.run_id,
                state=copy.deepcopy(stored.state),
                metadata=copy.deepcopy(stored.metadata),
            )

    def delete_state(self, run_id: str) -> None:
        with self._lock:
            self._records.pop(run_id, None)
