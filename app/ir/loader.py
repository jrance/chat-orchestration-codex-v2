"""Runtime IR loader that prepares agent prompts."""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Tuple

from app.prompt.assembler import assemble_system_prompt
from app.validation.ir_validator import validate_and_normalize


def build_runtime_plan(
    ir: Dict[str, Any],
    runtime_vars: Mapping[str, Any] | None = None,
) -> Tuple[bool, Dict[str, Any], List[str]]:
    """Validate the IR and build a runtime plan with assembled prompts."""
    ok, normalized, errors, warnings = validate_and_normalize(ir)
    if not ok or not normalized:
        return False, {}, errors

    runtime_vars = runtime_vars or {}
    node_by_id = normalized["nodeById"]
    agent_prompts: Dict[str, str] = {}

    for node_id, node in node_by_id.items():
        if node.get("kind") != "agent.codeless":
            continue
        data = node.get("data") or {}
        agent_prompts[node_id] = assemble_system_prompt(data, runtime_vars=runtime_vars)

    plan = {
        "entryId": normalized["entryId"],
        "nodeById": node_by_id,
        "edges": normalized["edges"],
        "resolved": normalized.get("resolved", {}),
        "warnings": warnings,
        "agentPrompts": agent_prompts,
    }

    return True, plan, errors


__all__ = ["build_runtime_plan"]
