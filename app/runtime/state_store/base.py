"""Protocol describing run state persistence."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Protocol

from app.compiler.types import OrchestratorState


@dataclass(slots=True)
class RunStateRecord:
    """Container for run state plus optional metadata/telemetry."""

    run_id: str
    state: OrchestratorState
    metadata: Dict[str, Any] = field(default_factory=dict)


class RunStateStore(Protocol):
    """Storage interface for orchestrator run states."""

    def put_state(self, record: RunStateRecord) -> None:
        """Persist the provided run state record."""

    def get_state(self, run_id: str) -> Optional[RunStateRecord]:
        """Fetch a run state record by identifier."""

    def delete_state(self, run_id: str) -> None:
        """Remove any stored state for ``run_id``."""
