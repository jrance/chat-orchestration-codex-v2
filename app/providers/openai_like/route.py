"""Helpers for executing router classification calls against OpenAI-compatible APIs."""

from __future__ import annotations

import copy
from typing import Any, Mapping, Sequence, Tuple

from app.providers.openai_like.responses import create_response


def _model_parameters(model_cfg: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(model_cfg, Mapping):
        return {}
    params: dict[str, Any] = {}
    if model_cfg.get("temperature") is not None:
        params["temperature"] = model_cfg["temperature"]
    if model_cfg.get("topP") is not None:
        params["top_p"] = model_cfg["topP"]
    if model_cfg.get("maxTokens") is not None:
        params["max_output_tokens"] = model_cfg["maxTokens"]
    if model_cfg.get("stop"):
        params["stop"] = model_cfg["stop"]
    if model_cfg.get("seed") is not None:
        params["seed"] = model_cfg["seed"]
    return params


def _normalize_schema(route_schema: Mapping[str, Any] | None, targets: Sequence[str]) -> dict[str, Any]:
    schema = copy.deepcopy(route_schema) if isinstance(route_schema, Mapping) else {"type": "object"}
    properties = schema.setdefault("properties", {})
    if not isinstance(properties, dict):
        properties = {}
        schema["properties"] = properties
    target_prop = properties.get("target")
    if not isinstance(target_prop, dict):
        target_prop = {}
        properties["target"] = target_prop
    enum_values = []
    seen: set[str] = set()
    for target in targets:
        if not isinstance(target, str):
            continue
        value = target.strip()
        if not value or value in seen:
            continue
        seen.add(value)
        enum_values.append(value)
    if enum_values:
        target_prop["enum"] = enum_values
    return schema


async def run_router_llm(
    prompt: str,
    *,
    route_schema: Mapping[str, Any] | None,
    targets: Sequence[str],
    model: Mapping[str, Any] | None = None,
    context_headers: Mapping[str, str] | None = None,
    max_attempts: int | None = None,
) -> Tuple[dict[str, Any], dict[str, Any]]:
    """Invoke the Responses API to classify the next router target."""

    schema = _normalize_schema(route_schema, targets)
    model_cfg = model or {}
    model_id = str(model_cfg.get("modelId") or "gpt-4o-mini")

    body: dict[str, Any] = {
        "model": model_id,
        "input": [{"role": "system", "content": prompt}],
        "response_format": {"type": "json_schema", "json_schema": schema},
        "json_mode": True,
    }
    body.update(_model_parameters(model_cfg))

    response = await create_response(
        body,
        context_headers=context_headers,
        max_attempts=max_attempts,
    )
    return {}, response


__all__ = ["run_router_llm"]
