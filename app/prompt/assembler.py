"""Prompt assembly utilities for agent system prompts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Optional

from app.config.settings import settings
from app.context.tokens import expand


def load_org_preamble() -> str:
    """Load the organization preamble from settings."""
    if settings.org_preamble_text:
        return settings.org_preamble_text.strip()

    path_value = settings.org_preamble_path
    if not path_value:
        return ""

    try:
        path = Path(path_value)
        if not path.is_file():
            return ""
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def assemble_system_prompt(
    agent_data: Mapping[str, Any],
    runtime_vars: Optional[Mapping[str, Any]] = None,
) -> str:
    """Compose the final system prompt for an agent."""
    runtime_vars = runtime_vars or {}
    sections: list[str] = []

    context_cfg = (agent_data.get("context") or {}) if agent_data else {}
    if context_cfg.get("injectOrgPreamble"):
        preamble = load_org_preamble()
        if preamble:
            sections.append("### ORGANIZATION PREAMBLE\n" + preamble)

    style = (agent_data.get("styleGuide") or "").strip()
    if style:
        sections.append("### STYLE GUIDE\n" + style)

    system_instructions = (agent_data.get("systemInstructions") or "").strip()
    if system_instructions:
        expanded = expand(system_instructions, runtime_vars=runtime_vars)
        if expanded:
            sections.append("### INSTRUCTIONS\n" + expanded)

    context_vars = context_cfg.get("vars") or {}
    if context_vars:
        sections.append(
            "### CONTEXT VARS\n" + json.dumps(context_vars, ensure_ascii=False)
        )

    return "\n\n".join(sections)


__all__ = ["assemble_system_prompt", "load_org_preamble"]
