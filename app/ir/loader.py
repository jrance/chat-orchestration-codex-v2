"""Runtime IR loader that prepares agent prompts."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List, Mapping, Tuple

from app.prompt.assembler import assemble_system_prompt
from app.tools import ArgMode, list_tools
from app.tools.types import ArgSpec, ToolSpec
from app.validation.ir_validator import validate_and_normalize


def _tool_spec_from_node(node: dict[str, Any], base_specs: Dict[str, ToolSpec]) -> ToolSpec:
    data = node.get("data") or {}
    tool_id = str(data.get("toolId") or node.get("id") or "")
    base_spec = deepcopy(base_specs.get(tool_id) or {})

    spec: ToolSpec = {
        "id": tool_id,
        "name": str(data.get("name") or node.get("label") or base_spec.get("name") or tool_id),
        "description": str(
            data.get("description") or node.get("description") or base_spec.get("description") or ""
        ),
        "args_schema": deepcopy(
            data.get("argsSchema") or base_spec.get("args_schema") or {"type": "object", "properties": {}}
        ),
        "arg_behaviors": deepcopy(base_spec.get("arg_behaviors") or {}),
    }

    if "handler" in base_spec:
        spec["handler"] = base_spec["handler"]
    if data.get("timeoutMs") is not None:
        spec["timeout_ms"] = int(data["timeoutMs"])
    elif "timeout_ms" in base_spec:
        spec["timeout_ms"] = base_spec["timeout_ms"]

    overrides = data.get("parameterOverrides") or {}
    behaviors: Dict[str, ArgSpec] = spec.get("arg_behaviors") or {}
    for arg_name, override in overrides.items():
        if not isinstance(override, Mapping):
            continue
        mode = override.get("mode")
        value = override.get("value")
        if mode == ArgMode.LLM_HIDDEN.value:
            behaviors[arg_name] = {"mode": ArgMode.LLM_HIDDEN, "default": value}
        elif mode == ArgMode.AGENT_OVERRIDE.value:
            behaviors[arg_name] = {"mode": ArgMode.AGENT_OVERRIDE, "default": value}
    spec["arg_behaviors"] = behaviors
    return spec


def _resolve_tool_specs(node_by_id: Dict[str, dict]) -> Dict[str, ToolSpec]:
    base_specs = list_tools()
    result: Dict[str, ToolSpec] = {}
    for node_id, node in node_by_id.items():
        if node.get("kind") != "tool":
            continue
        result[node_id] = _tool_spec_from_node(node, base_specs)
    return result


def _resolve_mcp_servers(node_by_id: Dict[str, dict]) -> Dict[str, dict[str, Any]]:
    servers: Dict[str, dict[str, Any]] = {}
    for node_id, node in node_by_id.items():
        if node.get("kind") != "mcpServer":
            continue
        servers[node_id] = deepcopy(node.get("data") or {})
    return servers


def _edges_by_source(edges: List[dict]) -> Dict[str, List[str]]:
    mapping: Dict[str, List[str]] = {}
    for edge in edges:
        src = edge.get("from")
        dst = edge.get("to")
        if not isinstance(src, str) or not isinstance(dst, str):
            continue
        mapping.setdefault(src, []).append(dst)
    return mapping


def _normalize_concurrent_nodes(
    node_by_id: Dict[str, dict],
    edges: List[dict],
) -> Dict[str, dict[str, Any]]:
    by_source = _edges_by_source(edges)
    runtime: Dict[str, dict[str, Any]] = {}

    for node_id, node in node_by_id.items():
        if node.get("kind") != "concurrent":
            continue
        data = node.get("data") or {}
        merge_cfg = data.get("merge") or {}

        timeout = data.get("timeoutSeconds", data.get("timeoutSec", 120))
        try:
            timeout_seconds = float(timeout)
        except (TypeError, ValueError):
            timeout_seconds = 120.0
        if timeout_seconds <= 0:
            timeout_seconds = 120.0

        max_parallelism = data.get("maxParallelism")
        try:
            max_parallelism_value = int(max_parallelism)
        except (TypeError, ValueError):
            max_parallelism_value = 4
        if max_parallelism_value <= 0:
            max_parallelism_value = 4

        strategy = str(merge_cfg.get("strategy") or "FirstBest")
        synth_prompt = merge_cfg.get("synthPrompt")
        first_best_threshold = merge_cfg.get("firstBestThreshold")
        try:
            first_best_threshold_value = float(first_best_threshold) if first_best_threshold is not None else None
        except (TypeError, ValueError):
            first_best_threshold_value = None

        cancel_remaining = data.get("cancelRemainingOnDecision")
        if cancel_remaining is None:
            cancel_remaining = strategy in ("FirstBest", "HighestScore")

        children = [
            child_id
            for child_id in by_source.get(node_id, [])
            if isinstance(child_id, str) and child_id in node_by_id
        ]

        runtime[node_id] = {
            "id": node_id,
            "kind": "concurrent",
            "label": node.get("label") or "",
            "cfg": {
                "strategy": strategy,
                "timeout_seconds": timeout_seconds,
                "max_parallelism": max_parallelism_value,
                "cancel_remaining_on_decision": bool(cancel_remaining),
                "synth_prompt": synth_prompt if isinstance(synth_prompt, str) and synth_prompt.strip() else None,
                "first_best_threshold": first_best_threshold_value,
            },
            "children": children,
        }

    return runtime


def _normalize_router_nodes(
    node_by_id: Dict[str, dict],
    edges: List[dict],
) -> Dict[str, dict[str, Any]]:
    by_source = _edges_by_source(edges)
    runtime: Dict[str, dict[str, Any]] = {}

    for node_id, node in node_by_id.items():
        if node.get("kind") != "router":
            continue
        data = node.get("data") or {}

        children = [
            child_id
            for child_id in by_source.get(node_id, [])
            if isinstance(child_id, str) and child_id in node_by_id
        ]

        label_by_child: Dict[str, str] = {}
        target_to_child: Dict[str, str] = {}
        for child_id in children:
            child_node = node_by_id[child_id]
            label = str(child_node.get("label") or "").strip()
            if not label:
                label = child_id
            if label in target_to_child:
                raise ValueError(f"Router node '{node_id}' has duplicate child label '{label}'")
            target_to_child[label] = child_id
            label_by_child[child_id] = label

        if not target_to_child:
            raise ValueError(f"Router node '{node_id}' must have at least one outgoing edge")

        prompt = str(data.get("prompt") or "")
        min_conf_raw = data.get("minConfidence", 0.0)
        try:
            min_confidence = float(min_conf_raw)
        except (TypeError, ValueError):
            raise ValueError(f"Router node '{node_id}' has invalid minConfidence '{min_conf_raw}'") from None
        if min_confidence < 0 or min_confidence > 1:
            raise ValueError(f"Router node '{node_id}' minConfidence must be between 0 and 1")

        tie_break = str(data.get("tieBreak") or "HighestConfidence")
        if tie_break not in {"HighestConfidence", "DeterministicOrder", "PreferList"}:
            raise ValueError(f"Router node '{node_id}' has unsupported tieBreak '{tie_break}'")

        allow_below_min = bool(data.get("allowBelowMinForTieBreak", False))

        telemetry_cfg = data.get("telemetry") or {}
        telemetry_labels = telemetry_cfg.get("labels") if isinstance(telemetry_cfg, Mapping) else {}
        if telemetry_labels and not isinstance(telemetry_labels, Mapping):
            telemetry_labels = {}

        model_cfg = data.get("model") if isinstance(data.get("model"), Mapping) else {}

        provided_targets = []
        for target in data.get("targets") or []:
            if isinstance(target, str) and target.strip():
                provided_targets.append(target.strip())

        auto_sync = data.get("autoSyncEnum")
        auto_sync_enum = True if auto_sync is None else bool(auto_sync)

        if provided_targets:
            deterministic_order: List[str] = list(dict.fromkeys(provided_targets))
        else:
            deterministic_order = [label_by_child[child_id] for child_id in children]

        targets: List[str]
        if auto_sync_enum or not provided_targets:
            targets = [label_by_child[child_id] for child_id in children]
        else:
            targets = list(dict.fromkeys(provided_targets))

        for target_label in targets:
            if target_label not in target_to_child:
                raise ValueError(
                    f"Router node '{node_id}' target '{target_label}' is not reachable from its children"
                )

        route_schema_raw = data.get("routeSchema")
        route_schema: dict[str, Any]
        if isinstance(route_schema_raw, Mapping):
            route_schema = deepcopy(route_schema_raw)
        else:
            route_schema = {"type": "object"}

        properties = route_schema.setdefault("properties", {})
        if not isinstance(properties, Mapping):
            properties = {}
            route_schema["properties"] = properties
        target_prop = properties.get("target") if isinstance(properties, Mapping) else None
        if not isinstance(target_prop, Mapping):
            target_prop = {}
            properties["target"] = target_prop

        if auto_sync_enum:
            target_prop = dict(target_prop)
            target_prop["enum"] = list(dict.fromkeys(targets))
            properties["target"] = target_prop
        else:
            existing_enum = []
            for value in target_prop.get("enum", []):
                if isinstance(value, str) and value.strip():
                    existing_enum.append(value.strip())
            for value in existing_enum:
                if value not in target_to_child:
                    raise ValueError(
                        f"Router node '{node_id}' routeSchema target enum '{value}' is not a reachable child"
                    )

        tie_break_prefer: List[str] = []
        for value in data.get("tieBreakPrefer") or []:
            if isinstance(value, str):
                value_str = value.strip()
                if value_str and value_str in target_to_child:
                    tie_break_prefer.append(value_str)

        fallback_cfg = data.get("fallback") if isinstance(data.get("fallback"), Mapping) else {}
        fallback_mode = str(fallback_cfg.get("mode") or "Error")
        normalized_fallback: Dict[str, Any] = {"mode": fallback_mode}

        def _resolve_ref(ref: Any, *, field: str) -> Tuple[str | None, str | None]:
            if not isinstance(ref, str) or not ref.strip():
                return None, None
            value = ref.strip()
            if value in target_to_child:
                child_id = target_to_child[value]
                return value, child_id
            if value in label_by_child:
                # Provided as node id.
                return label_by_child[value], value
            raise ValueError(f"Router node '{node_id}' fallback {field} '{value}' is not a reachable child")

        default_child_label, default_child_id = _resolve_ref(fallback_cfg.get("defaultChild"), field="defaultChild")
        if default_child_id:
            normalized_fallback["default_child_label"] = default_child_label
            normalized_fallback["default_child_id"] = default_child_id

        safe_agent_label, safe_agent_id = _resolve_ref(fallback_cfg.get("safeAgentRef"), field="safeAgentRef")
        if safe_agent_id:
            normalized_fallback["safe_agent_label"] = safe_agent_label
            normalized_fallback["safe_agent_id"] = safe_agent_id

        if isinstance(fallback_cfg.get("prompt"), str) and fallback_cfg["prompt"].strip():
            normalized_fallback["prompt"] = fallback_cfg["prompt"].strip()

        cfg_entry = {
            "prompt_template": prompt,
            "route_schema": route_schema,
            "min_confidence": min_confidence,
            "tie_break": tie_break,
            "tie_break_prefer": tie_break_prefer,
            "allow_below_min_for_tiebreak": allow_below_min,
            "fallback": normalized_fallback,
            "telemetry_labels": dict(telemetry_labels) if isinstance(telemetry_labels, Mapping) else {},
            "deterministic_order": deterministic_order,
        }
        if model_cfg:
            cfg_entry["model"] = deepcopy(model_cfg)

        runtime[node_id] = {
            "id": node_id,
            "kind": "router",
            "label": node.get("label") or "",
            "cfg": cfg_entry,
            "targets": targets,
            "children": children,
            "target_to_child": target_to_child,
            "child_labels": label_by_child,
        }

    return runtime


def _prune_router_edges(node_by_id: Dict[str, dict], edges: List[dict]) -> List[dict]:
    pruned: List[dict] = []
    for edge in edges:
        src = edge.get("from")
        src_node = node_by_id.get(src)
        if isinstance(src_node, Mapping) and src_node.get("kind") == "router":
            continue
        pruned.append(edge)
    return pruned


def _prune_concurrent_edges(node_by_id: Dict[str, dict], edges: List[dict]) -> List[dict]:
    """Remove edges that originate from concurrent nodes since they are handled at runtime."""

    pruned: List[dict] = []
    for edge in edges:
        src = edge.get("from")
        src_node = node_by_id.get(src)
        if isinstance(src_node, Mapping) and src_node.get("kind") == "concurrent":
            continue
        pruned.append(edge)
    return pruned


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

    tool_specs = _resolve_tool_specs(node_by_id)
    agent_tool_specs: Dict[str, List[ToolSpec]] = {}
    agent_bindings = (normalized.get("resolved") or {}).get("agentToolBindings") or {}
    for agent_id, tool_ids in agent_bindings.items():
        agent_tool_specs[agent_id] = [tool_specs[tool_id] for tool_id in tool_ids if tool_id in tool_specs]

    runtime_concurrent = _normalize_concurrent_nodes(node_by_id, normalized["edges"])
    runtime_router = _normalize_router_nodes(node_by_id, normalized["edges"])
    pruned_edges = _prune_router_edges(node_by_id, normalized["edges"])
    pruned_edges = _prune_concurrent_edges(node_by_id, pruned_edges)

    plan = {
        "entryId": normalized["entryId"],
        "nodeById": node_by_id,
        "edges": pruned_edges,
        "resolved": normalized.get("resolved", {}),
        "warnings": warnings,
        "agentPrompts": agent_prompts,
        "toolSpecs": tool_specs,
        "agentToolSpecs": agent_tool_specs,
        "mcpServers": _resolve_mcp_servers(node_by_id),
        "runtimeConcurrent": runtime_concurrent,
        "runtimeRouter": runtime_router,
    }

    return True, plan, errors


__all__ = ["build_runtime_plan"]
