import copy
from typing import Iterable, Sequence

from app.api.deps import ExecutionHeaders
from app.telemetry.models import TelemetryLevel


def default_headers() -> ExecutionHeaders:
    return ExecutionHeaders(
        tenant_id="tenant-test",
        telemetry=TelemetryLevel.NONE,
        correlation_id="corr-id",
        request_id="req-id",
        timestamp="2024-01-01T00:00:00Z",
        client_id=None,
        extra_headers={},
    )


def _agent_node(node_id: str, label: str) -> dict[str, object]:
    return {
        "id": node_id,
        "kind": "agent.codeless",
        "label": label,
        "data": {
            "systemInstructions": f"You are {label}. Provide crisp insights.",
            "styleGuide": "",
            "model": {
                "provider": "openai",
                "modelId": "gpt-4o-mini",
                "temperature": 0.1,
                "topP": 1,
                "maxTokens": 128,
                "stop": [],
            },
            "context": {"historyWindow": {"mode": "LastN", "n": 5}},
            "tools": {"policy": "Disabled", "attached": []},
        },
    }


def build_groupchat_ir(
    participants: Sequence[str],
    *,
    max_turns: int = 3,
    stop_when: str = "ModeratorSatisfied",
    emit_synthesis: bool = True,
    speaker_budget: int = 80,
) -> dict[str, object]:
    nodes: list[dict[str, object]] = [
        {
            "id": "groupchat",
            "kind": "groupchat",
            "label": "Panel",
            "data": {
                "moderatorPrompt": "Guide experts to a concise answer.",
                "maxTurns": max_turns,
                "stopWhen": stop_when,
                "emitSynthesis": emit_synthesis,
                "speakerBudgetTokens": speaker_budget,
            },
        }
    ]
    edges: list[dict[str, str]] = []

    for index, participant_id in enumerate(participants, start=1):
        label = participant_id.title()
        nodes.append(_agent_node(participant_id, label))
        edges.append({"id": f"gc-{index}", "from": "groupchat", "to": participant_id})

    return {
        "meta": {"id": "pkg", "name": "GroupChat", "version": "1.0.0"},
        "nodes": copy.deepcopy(nodes),
        "edges": copy.deepcopy(edges),
        "entryId": "groupchat",
    }


__all__ = ["build_groupchat_ir", "default_headers"]
