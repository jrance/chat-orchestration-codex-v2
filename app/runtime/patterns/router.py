"""Routing orchestration helpers used by router nodes."""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Mapping, MutableMapping, Optional, Sequence, Tuple

from app.compiler.types import OrchestratorState
from app.providers.openai_like.route import run_router_llm
from app.runtime.context import get_runtime_context
from app.runtime.types import PauseMetadata, RouterDecision
from app.telemetry.events import (
    ROUTER_DECISION,
    ROUTER_FALLBACK,
    ROUTER_RUN_STARTED,
    make_event,
)
from app.telemetry.streamer import TelemetryStreamer


class RouterError(Exception):
    """Base exception raised when router orchestration fails."""


class NoRouteError(RouterError):
    """Raised when no eligible target can be resolved for the router."""


@dataclass(slots=True)
class RouterConfig:
    """Resolved configuration for a router node."""

    node_id: str
    label: str
    prompt_template: str
    route_schema: Mapping[str, Any]
    min_confidence: float
    tie_break: str
    tie_break_prefer: Sequence[str]
    allow_below_min: bool
    fallback: Mapping[str, Any]
    telemetry_labels: Mapping[str, str]
    deterministic_order: Sequence[str]
    model: Mapping[str, Any] | None = None


@dataclass(slots=True)
class ChoiceResult:
    """Represents the outcome of applying LLM + tie-break rules."""

    target_label: str | None
    source: str
    tie_break_used: str | None
    used_allow_below: bool


def _unique(sequence: Iterable[str]) -> list[str]:
    seen: MutableMapping[str, None] = {}
    for item in sequence:
        if item not in seen:
            seen[item] = None
    return list(seen.keys())


def _render_prompt(template: str, targets: Sequence[str]) -> str:
    if not template:
        return ""
    placeholder = "<targets>"
    if placeholder not in template:
        return template
    if not targets:
        return template.replace(placeholder, "the available options")
    if len(targets) == 1:
        rendered = targets[0]
    elif len(targets) == 2:
        rendered = " vs. ".join(targets)
    else:
        rendered = ", ".join(targets[:-1]) + f", or {targets[-1]}"
    return template.replace(placeholder, rendered)


def _coerce_confidence(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number < 0:
        number = 0.0
    if number > 1:
        number = 1.0
    return number


def _sanitize_llm_payload(payload: Mapping[str, Any] | None) -> Tuple[str | None, float | None, str | None]:
    if not isinstance(payload, Mapping):
        return None, None, None
    target_value = payload.get("target")
    if isinstance(target_value, str):
        target = target_value.strip() or None
    else:
        target = None
    confidence = _coerce_confidence(payload.get("confidence"))
    rationale_value = payload.get("rationale")
    rationale = rationale_value if isinstance(rationale_value, str) else None
    return target, confidence, rationale


def choose_target(
    decision: Mapping[str, Any] | None,
    targets: Sequence[str],
    *,
    min_confidence: float,
    tie_break: str,
    tie_break_prefer: Sequence[str],
    allow_below_min: bool,
    deterministic_order: Sequence[str],
) -> ChoiceResult:
    """Return the selected target label (or None) after applying tie-break logic."""

    valid_targets = list(targets)
    llm_target, llm_conf, _ = _sanitize_llm_payload(decision)
    meets_min = llm_conf is not None and llm_conf >= min_confidence

    if llm_target in valid_targets and meets_min:
        return ChoiceResult(target_label=llm_target, source="llm", tie_break_used=None, used_allow_below=False)

    candidates: list[str] = []
    if llm_target in valid_targets and allow_below_min:
        candidates.append(llm_target)

    strategy = tie_break or "HighestConfidence"
    strategy = strategy if strategy in {"HighestConfidence", "DeterministicOrder", "PreferList"} else "HighestConfidence"

    if strategy == "PreferList":
        for label in tie_break_prefer:
            if label in valid_targets and label not in candidates:
                candidates.append(label)
        for label in deterministic_order:
            if label in valid_targets and label not in candidates:
                candidates.append(label)
        if candidates:
            chosen = candidates[0]
            source = "llm" if chosen == llm_target and meets_min else "tie_break"
            return ChoiceResult(chosen, source, "PreferList", allow_below_min and chosen == llm_target)
        return ChoiceResult(None, "none", "PreferList", False)

    if strategy == "DeterministicOrder":
        for label in deterministic_order:
            if label in valid_targets and label not in candidates:
                candidates.append(label)
        if candidates:
            chosen = candidates[0]
            source = "llm" if chosen == llm_target and meets_min else "tie_break"
            return ChoiceResult(chosen, source, "DeterministicOrder", allow_below_min and chosen == llm_target)
        return ChoiceResult(None, "none", "DeterministicOrder", False)

    # HighestConfidence. When multiple candidates appear, prefer one with the highest observed confidence.
    weighted: list[Tuple[str, float]] = []
    for label in _unique([llm_target] + list(valid_targets)):
        if label not in valid_targets:
            continue
        score = llm_conf if label == llm_target and llm_conf is not None else 0.0
        if score >= min_confidence or allow_below_min:
            weighted.append((label, score))

    if not weighted:
        return ChoiceResult(None, "none", "HighestConfidence", False)

    weighted.sort(key=lambda item: item[1], reverse=True)
    chosen_label, chosen_score = weighted[0]
    source = "llm" if chosen_label == llm_target and meets_min else "tie_break"
    used_allow = allow_below_min and chosen_label == llm_target and chosen_score < min_confidence
    return ChoiceResult(chosen_label, source, "HighestConfidence", used_allow)


def _clarify_prompt(targets: Sequence[str]) -> str:
    if not targets:
        return "Please clarify what you need help with so we can route you to the best agent."
    if len(targets) == 1:
        return f"Please clarify if your request is related to {targets[0]}."
    if len(targets) == 2:
        return f"Please clarify which area you need help with: {targets[0]} vs. {targets[1]}."
    joined = ", ".join(targets[:-1])
    return f"Please clarify which area you need help with: {joined}, or {targets[-1]}."


async def _publish(telemetry: TelemetryStreamer | None, event: str, payload: Mapping[str, Any]) -> None:
    if telemetry is None or not telemetry.enabled():
        return
    await telemetry.publish(make_event(event, payload))


def _resolve_target_node(
    target_label: str | None,
    *,
    target_to_child: Mapping[str, str],
    child_labels: Mapping[str, str],
) -> Tuple[str | None, str | None]:
    if target_label:
        node_id = target_to_child.get(target_label)
        if node_id:
            return target_label, node_id
        # Attempt reverse lookup where label matches node id directly
        if target_label in child_labels:
            return child_labels[target_label], target_label
    return None, None


def _apply_fallback(
    *,
    config: RouterConfig,
    raw_decision: Mapping[str, Any] | None,
    choice: ChoiceResult,
    target_to_child: Mapping[str, str],
    child_labels: Mapping[str, str],
    targets: Sequence[str],
) -> Tuple[str | None, str | None, str | None, PauseMetadata | None, bool]:
    fallback_cfg = config.fallback or {}
    mode = str(fallback_cfg.get("mode") or "Error")
    mode_upper = mode

    if choice.target_label:
        label, node_id = _resolve_target_node(choice.target_label, target_to_child=target_to_child, child_labels=child_labels)
        if label and node_id:
            return label, node_id, None, None, False

    if mode_upper == "DefaultChild":
        node_id = fallback_cfg.get("default_child_id") or fallback_cfg.get("defaultChildId")
        label = fallback_cfg.get("default_child_label") or fallback_cfg.get("defaultChild") or None
        if not node_id:
            # Attempt to interpret the configured value as a label
            ref = fallback_cfg.get("defaultChild") or ""
            if isinstance(ref, str) and ref:
                label = ref
                node_id = target_to_child.get(ref) or (ref if ref in child_labels else None)
        if not node_id:
            raise NoRouteError("ROUTER_NO_ROUTE")
        if not label:
            label = child_labels.get(node_id, node_id)
        return label, node_id, "DefaultChild", None, True

    if mode_upper == "SafeAgent":
        node_id = fallback_cfg.get("safe_agent_id") or fallback_cfg.get("safeAgentId")
        label = fallback_cfg.get("safe_agent_label") or fallback_cfg.get("safeAgentRef") or None
        if not node_id:
            ref = fallback_cfg.get("safeAgentRef")
            if isinstance(ref, str) and ref:
                node_id = target_to_child.get(ref) or (ref if ref in child_labels else None)
                label = label or ref
        if not node_id:
            raise NoRouteError("ROUTER_NO_ROUTE")
        if not label:
            label = child_labels.get(node_id, node_id)
        return label, node_id, "SafeAgent", None, True

    if mode_upper == "AskUserClarify":
        prompt = fallback_cfg.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            prompt = _clarify_prompt(targets)
        hitl_payload: PauseMetadata = {
            "status": "awaiting_user_input",
            "reason": "router.ask_user",
            "prompt": prompt,
            "targets": list(targets),
        }
        return None, None, "AskUserClarify", hitl_payload, True

    # Default: raise error
    raise NoRouteError("ROUTER_NO_ROUTE")


def _extract_json_payload(response: Mapping[str, Any]) -> Mapping[str, Any]:
    output = response.get("output")
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, Mapping):
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, Mapping):
                    continue
                if "json" in block:
                    json_payload = block["json"]
                    if isinstance(json_payload, Mapping):
                        return json_payload
                    if isinstance(json_payload, str):
                        try:
                            return json.loads(json_payload)
                        except json.JSONDecodeError:
                            continue
                text_payload = block.get("text")
                if isinstance(text_payload, str):
                    try:
                        return json.loads(text_payload)
                    except json.JSONDecodeError:
                        continue
    text = response.get("output_text")
    if isinstance(text, str):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {}
    return {}


def build_router_runner(
    node: Mapping[str, Any],
    *,
    config: Mapping[str, Any],
    child_ids: Sequence[str],
    targets: Sequence[str],
    target_to_child: Mapping[str, str],
    child_labels: Mapping[str, str],
) -> Any:
    """Construct an async LangGraph node runner for router orchestration."""

    node_id = str(node.get("id") or "")
    label = str(node.get("label") or node_id)
    cfg = RouterConfig(
        node_id=node_id,
        label=label,
        prompt_template=str(config.get("prompt_template") or ""),
        route_schema=config.get("route_schema") or {},
        min_confidence=float(config.get("min_confidence") or 0.0),
        tie_break=str(config.get("tie_break") or "HighestConfidence"),
        tie_break_prefer=list(config.get("tie_break_prefer") or []),
        allow_below_min=bool(config.get("allow_below_min_for_tiebreak")),
        fallback=config.get("fallback") or {},
        telemetry_labels=config.get("telemetry_labels") or {},
        deterministic_order=list(config.get("deterministic_order") or list(targets)),
        model=config.get("model") or {},
    )

    async def _run(state: OrchestratorState) -> OrchestratorState:
        base_state = copy.deepcopy(state)

        pending_choices = base_state.get("pending_router_choice")
        resume_payload: Any | None = None
        if isinstance(pending_choices, Mapping):
            if cfg.node_id in pending_choices:
                resume_payload = pending_choices[cfg.node_id]
                remaining = dict(pending_choices)
                remaining.pop(cfg.node_id, None)
                if remaining:
                    base_state["pending_router_choice"] = remaining
                else:
                    base_state.pop("pending_router_choice", None)
        elif pending_choices is not None and cfg.node_id == pending_choices:
            resume_payload = pending_choices
            base_state.pop("pending_router_choice", None)

        if resume_payload is not None:
            target_hint: str | None = None
            target_node_id: str | None = None
            target_label: str | None = None
            if isinstance(resume_payload, Mapping):
                target_node_id = resume_payload.get("targetNodeId") or resume_payload.get("target_node_id")
                target_label = resume_payload.get("targetLabel") or resume_payload.get("target_label")
                target_hint = (
                    resume_payload.get("target")
                    or target_label
                    or target_node_id
                )
            else:
                target_hint = str(resume_payload)

            if target_node_id and target_node_id in child_labels:
                target_label = child_labels.get(target_node_id, target_node_id)
            elif target_label and target_label in target_to_child:
                target_node_id = target_to_child[target_label]
            elif target_hint:
                if target_hint in target_to_child:
                    target_node_id = target_to_child[target_hint]
                    target_label = target_hint
                elif target_hint in child_labels:
                    target_node_id = target_hint
                    target_label = child_labels.get(target_hint, target_hint)
                else:
                    lowered = target_hint.lower()
                    for label, node_id in target_to_child.items():
                        if label.lower() == lowered:
                            target_node_id = node_id
                            target_label = label
                            break
            if not target_node_id:
                raise NoRouteError("ROUTER_RESUME_TARGET_NOT_FOUND")

            base_state["status"] = "running"
            base_state.pop("pause_metadata", None)
            base_state.pop("pause_reason", None)
            base_state["route"] = {
                "target": target_node_id,
                "label": target_label,
                "source": "resume",
            }
            scratch = base_state.setdefault("scratch", {})
            router_store = scratch.setdefault("router", {})
            router_entry = copy.deepcopy(router_store.get(cfg.node_id) or {})
            decision_payload = copy.deepcopy(router_entry.get("decision") or {})
            decision_payload.update(
                {
                    "nodeId": cfg.node_id,
                    "label": cfg.label,
                    "targetLabel": target_label,
                    "targetNodeId": target_node_id,
                    "source": "resume",
                    "fallbackMode": None,
                }
            )
            router_entry["decision"] = decision_payload
            router_entry["targets"] = list(targets)
            router_entry["target_to_child"] = dict(target_to_child)
            router_entry["child_labels"] = dict(child_labels)
            router_entry["children"] = list(child_ids)
            router_store[cfg.node_id] = router_entry
            return base_state

        runtime_ctx = get_runtime_context()
        telemetry: TelemetryStreamer | None = runtime_ctx.telemetry if runtime_ctx else None
        headers = runtime_ctx.http_headers() if runtime_ctx else {}

        rendered_prompt = _render_prompt(cfg.prompt_template, targets)

        await _publish(
            telemetry,
            ROUTER_RUN_STARTED,
            {
                "nodeId": cfg.node_id,
                "label": cfg.label,
                "targets": list(targets),
                "minConfidence": cfg.min_confidence,
                "tieBreak": cfg.tie_break,
                "allowBelowMin": cfg.allow_below_min,
                "labels": dict(cfg.telemetry_labels),
            },
        )

        raw_response = await run_router_llm(
            rendered_prompt,
            route_schema=cfg.route_schema,
            targets=list(targets),
            model=cfg.model,
            context_headers=headers,
        )
        response_payload, provider_response = raw_response
        parsed_payload = _extract_json_payload(provider_response) or {}
        effective_decision = parsed_payload or response_payload or {}

        llm_target, llm_confidence, llm_rationale = _sanitize_llm_payload(effective_decision)
        choice = choose_target(
            effective_decision,
            targets,
            min_confidence=cfg.min_confidence,
            tie_break=cfg.tie_break,
            tie_break_prefer=cfg.tie_break_prefer,
            allow_below_min=cfg.allow_below_min,
            deterministic_order=cfg.deterministic_order,
        )

        target_label, target_node_id, fallback_mode, hitl_payload, used_fallback = _apply_fallback(
            config=cfg,
            raw_decision=effective_decision,
            choice=choice,
            target_to_child=target_to_child,
            child_labels=child_labels,
            targets=targets,
        )

        if fallback_mode == "AskUserClarify":
            source = "paused"
        elif fallback_mode:
            source = "fallback"
        else:
            source = choice.source

        decision_payload: RouterDecision = {
            "nodeId": cfg.node_id,
            "label": cfg.label,
            "targetLabel": target_label,
            "targetNodeId": target_node_id,
            "confidence": llm_confidence,
            "rationale": llm_rationale,
            "source": source,
            "tieBreak": choice.tie_break_used,
            "fallbackMode": fallback_mode,
            "allowBelowMinForTieBreak": cfg.allow_below_min,
            "llmTarget": llm_target,
            "llmConfidence": llm_confidence,
            "llmRationale": llm_rationale,
            "usedFallback": used_fallback,
            "hitl": hitl_payload,
            "raw": dict(effective_decision),
        }

        await _publish(
            telemetry,
            ROUTER_DECISION,
            {
                "nodeId": cfg.node_id,
                "label": cfg.label,
                "llmTarget": llm_target,
                "llmConfidence": llm_confidence,
                "chosenTarget": target_label,
                "chosenNodeId": target_node_id,
                "source": decision_payload["source"],
                "tieBreak": choice.tie_break_used,
                "fallbackMode": fallback_mode,
                "labels": dict(cfg.telemetry_labels),
            },
        )

        if fallback_mode and fallback_mode != "AskUserClarify":
            await _publish(
                telemetry,
                ROUTER_FALLBACK,
                {
                    "nodeId": cfg.node_id,
                    "label": cfg.label,
                    "mode": fallback_mode,
                    "chosenNodeId": target_node_id,
                    "labels": dict(cfg.telemetry_labels),
                },
            )

        if fallback_mode == "AskUserClarify":
            await _publish(
                telemetry,
                ROUTER_FALLBACK,
                {
                    "nodeId": cfg.node_id,
                    "label": cfg.label,
                    "mode": fallback_mode,
                    "labels": dict(cfg.telemetry_labels),
                },
            )

        scratch = base_state.setdefault("scratch", {})
        router_store = scratch.setdefault("router", {})
        router_store[cfg.node_id] = {
            "decision": decision_payload,
            "targets": list(targets),
            "target_to_child": dict(target_to_child),
            "child_labels": dict(child_labels),
            "children": list(child_ids),
            "raw": provider_response,
            "llm": dict(effective_decision),
        }

        base_state["route"] = {
            "target": target_node_id or target_label,
            "label": target_label,
            "confidence": llm_confidence,
            "source": decision_payload["source"],
        }

        if hitl_payload:
            base_state["status"] = "paused"
            base_state["pause_reason"] = "router.ask_user"
            base_state["pause_metadata"] = {"hitl": hitl_payload}
        else:
            base_state.pop("pause_metadata", None)

        return base_state

    return _run


__all__ = ["RouterError", "NoRouteError", "build_router_runner", "choose_target"]
