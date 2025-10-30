"""Groupchat orchestration controller that coordinates moderated multi-agent turns."""

from __future__ import annotations

import copy
import time
from dataclasses import dataclass
from typing import Any, Mapping, MutableMapping, Optional, Sequence

from app.compiler.types import OrchestratorState
from app.providers.openai_like.moderator import (
    choose_next_speaker,
    generate_synthesis,
    has_consensus,
    is_satisfied,
)
from app.runtime.agents import codeless as codeless_agent
from app.runtime.context import get_runtime_context
from app.runtime.types import GroupChatResult, TurnRecord
from app.telemetry.events import (
    GROUPCHAT_RUN_COMPLETED,
    GROUPCHAT_TURN_COMPLETED,
    GROUPCHAT_TURN_DELTA,
    make_event,
)
from app.telemetry.streamer import TelemetryStreamer
from app.tools.types import ToolSpec

DEFAULT_SATISFACTION_RUBRIC = (
    "Stop when the panel has provided a concrete, actionable answer that directly resolves the user's request."
    " If important caveats or follow-up steps remain unaddressed, you are not satisfied."
)
DEFAULT_CONSENSUS_RUBRIC = (
    "All participants must indicate aligned recommendations or absence of objections."
    " If any conflicting guidance remains, consensus has not been reached."
)


@dataclass(slots=True)
class ParticipantInfo:
    """Metadata describing a single groupchat participant agent."""

    node_id: str
    label: str
    node: Mapping[str, Any]
    prompt: str
    tools: Sequence[ToolSpec]
    last_turn_index: int = -1


async def _publish(telemetry: TelemetryStreamer | None, event: str, payload: Mapping[str, Any]) -> None:
    if telemetry and telemetry.enabled():
        await telemetry.publish(make_event(event, payload))


def _normalize_usage(usage: Mapping[str, Any] | None) -> dict[str, int]:
    normalized: dict[str, int] = {}
    if not isinstance(usage, Mapping):
        return normalized
    for key in ("input_tokens", "output_tokens", "total_tokens"):
        value = usage.get(key)
        if isinstance(value, (int, float)):
            normalized[key] = int(value)
    return normalized


def _merge_usage(total: dict[str, int], usage: Mapping[str, Any] | None) -> dict[str, int]:
    result = dict(total)
    payload = _normalize_usage(usage)
    for key, value in payload.items():
        result[key] = result.get(key, 0) + int(value)
    return result


def _extract_user_question(messages: Sequence[Mapping[str, Any]]) -> str:
    for message in reversed(messages):
        if not isinstance(message, Mapping):
            continue
        if str(message.get("role") or "").lower() != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, Sequence):
            parts = []
            for part in content:
                if isinstance(part, Mapping) and isinstance(part.get("text"), str):
                    parts.append(part["text"])
                elif isinstance(part, str):
                    parts.append(part)
            if parts:
                return " ".join(parts)
    return ""


def _participant_meta(participants: Sequence[ParticipantInfo]) -> list[dict[str, Any]]:
    meta: list[dict[str, Any]] = []
    for participant in participants:
        meta.append(
            {
                "id": participant.node_id,
                "label": participant.label,
                "expertise": participant.node.get("description") or participant.node.get("label") or participant.node_id,
                "lastTurnIndex": participant.last_turn_index if participant.last_turn_index >= 0 else None,
            }
        )
    return meta


def _participant_stances(participants: Sequence[ParticipantInfo], transcript: Sequence[TurnRecord]) -> list[dict[str, Any]]:
    stances: list[dict[str, Any]] = []
    for participant in participants:
        last_turn: Optional[TurnRecord] = None
        for turn in reversed(transcript):
            if turn.get("speakerId") == participant.node_id and turn.get("status") != "error":
                last_turn = turn
                break
        headline = ""
        if last_turn and isinstance(last_turn.get("text"), str):
            headline = last_turn["text"][:280]
        stances.append({"id": participant.node_id, "label": participant.label, "headline": headline})
    return stances


def _find_participant(value: str, participants: Sequence[ParticipantInfo]) -> ParticipantInfo | None:
    if not value:
        return None
    needle = value.strip().lower()
    for participant in participants:
        if participant.node_id.lower() == needle or participant.label.lower() == needle:
            return participant
    return None


def _guidance_message(instruction: str, token_budget: int) -> str:
    parts = [instruction.strip() or "Add your unique insight. Do not repeat earlier points."]
    if token_budget > 0:
        parts.append(f"Keep the reply under roughly {token_budget} tokens.")
    parts.append("Stay concise and reference prior turns when helpful.")
    return "[ModeratorInstruction] " + " ".join(parts)


def build_groupchat_runner(
    node: Mapping[str, Any],
    runtime_meta: Mapping[str, Any],
    *,
    node_by_id: Mapping[str, Mapping[str, Any]],
    agent_prompts: Mapping[str, str],
    agent_tool_specs: Mapping[str, Sequence[ToolSpec]],
    mcp_servers: Mapping[str, Mapping[str, Any]],
):
    """Return an async LangGraph node that executes a moderated groupchat."""

    node_id = str(node.get("id") or "")
    cfg = runtime_meta.get("cfg") or {}
    participants_cfg = runtime_meta.get("participants") or []

    participants: list[ParticipantInfo] = []
    for child_id in participants_cfg:
        child_node = node_by_id.get(child_id)
        if not isinstance(child_node, Mapping) or child_node.get("kind") != "agent.codeless":
            raise ValueError(f"Unsupported participant '{child_id}' for groupchat node '{node_id}'")
        participants.append(
            ParticipantInfo(
                node_id=child_id,
                label=str(child_node.get("label") or child_id),
                node=child_node,
                prompt=agent_prompts.get(child_id, ""),
                tools=agent_tool_specs.get(child_id, []),
            )
        )

    if not participants:
        raise ValueError(f"Groupchat node '{node_id}' requires at least one participant")

    max_turns = int(cfg.get("max_turns") or 1)
    stop_when = str(cfg.get("stop_when") or "ModeratorSatisfied")
    emit_synthesis = bool(cfg.get("emit_synthesis", True))
    moderator_prompt = str(cfg.get("moderator_prompt") or "")
    speaker_budget = int(cfg.get("speaker_budget_tokens") or 0)
    node_label = str(node.get("label") or node_id)

    async def _run(state: OrchestratorState) -> OrchestratorState:
        runtime_ctx = get_runtime_context()
        telemetry = runtime_ctx.telemetry if runtime_ctx else None

        conversation_messages = list(state.get("messages") or [])
        transcript: list[TurnRecord] = []
        total_usage: dict[str, int] = {}
        satisfaction_payload: dict[str, Any] | None = None
        consensus_payload: dict[str, Any] | None = None

        last_index = -1
        user_question = _extract_user_question(conversation_messages)
        stop_reason = "MaxTurns"

        for turn_index in range(max_turns):
            try:
                decision = await choose_next_speaker(
                    moderator_prompt,
                    _participant_meta(participants),
                    transcript,
                    user_question=user_question,
                    constraints={
                        "turnIndex": turn_index,
                        "maxTurns": max_turns,
                        "speakerBudgetTokens": speaker_budget,
                    },
                )
            except Exception:
                decision = {}

            speaker_value = str(decision.get("speaker") or "").strip()
            participant = _find_participant(speaker_value, participants)
            if participant is None:
                next_index = (last_index + 1) % len(participants)
                participant = participants[next_index]
            try:
                last_index = participants.index(participant)
            except ValueError:
                last_index = 0

            instruction = str(decision.get("instruction") or "").strip()
            participant.last_turn_index = turn_index

            guidance = _guidance_message(instruction, speaker_budget)
            agent_state = copy.deepcopy(state)
            agent_messages = list(conversation_messages)
            agent_messages.append({"role": "system", "content": guidance})
            agent_state["messages"] = agent_messages

            turn_text = ""
            usage_summary: Mapping[str, Any] | None = None
            turn_status: str = "completed"
            turn_error: str | None = None

            try:
                invocation = await codeless_agent.invoke_llm(
                    agent_state,
                    participant.node,
                    participant.prompt,
                    attached_tools=participant.tools,
                    mcp_servers=mcp_servers,
                )
                usage_summary = invocation.usage
                response_messages = list(invocation.state.get("messages") or agent_messages)
                filtered_messages = [
                    msg
                    for msg in response_messages
                    if not (
                        isinstance(msg, Mapping)
                        and str(msg.get("role") or "").lower() == "system"
                        and msg.get("content") == guidance
                    )
                ]
                conversation_messages = filtered_messages
                state["messages"] = conversation_messages
                turn_text = invocation.output_text or ""
                total_usage = _merge_usage(total_usage, usage_summary)
            except Exception as exc:  # pragma: no cover - defensive guard
                turn_status = "error"
                turn_error = str(exc)

            record: TurnRecord = {
                "speakerId": participant.node_id,
                "speakerLabel": participant.label,
                "role": "assistant",
                "text": turn_text,
                "usage": _normalize_usage(usage_summary),
                "ts": time.time(),
                "status": turn_status,
                "instruction": instruction or None,
            }
            if turn_error:
                record["error"] = turn_error
            transcript.append(record)

            await _publish(
                telemetry,
                GROUPCHAT_TURN_DELTA,
                {
                    "runId": runtime_ctx.run_id if runtime_ctx else None,
                    "nodeId": node_id,
                    "turnIndex": turn_index,
                    "speakerId": participant.node_id,
                    "label": participant.label,
                    "delta": turn_text,
                },
            )
            await _publish(
                telemetry,
                GROUPCHAT_TURN_COMPLETED,
                {
                    "runId": runtime_ctx.run_id if runtime_ctx else None,
                    "nodeId": node_id,
                    "turnIndex": turn_index,
                    "speakerId": participant.node_id,
                    "label": participant.label,
                    "status": turn_status,
                    "usage": _normalize_usage(usage_summary),
                },
            )

            if stop_when == "ModeratorSatisfied":
                try:
                    satisfaction_payload = await is_satisfied(
                        transcript,
                        user_question=user_question,
                        rubric=DEFAULT_SATISFACTION_RUBRIC,
                    )
                except Exception:
                    satisfaction_payload = {"satisfied": False}
                if bool(satisfaction_payload.get("satisfied")):
                    stop_reason = "ModeratorSatisfied"
                    break
            elif stop_when == "AllAgree":
                try:
                    consensus_payload = await has_consensus(
                        transcript,
                        participant_stances=_participant_stances(participants, transcript),
                        rubric=DEFAULT_CONSENSUS_RUBRIC,
                    )
                except Exception:
                    consensus_payload = {"allAgree": False}
                if bool(consensus_payload.get("allAgree")):
                    stop_reason = "AllAgree"
                    break

        final_text = ""
        final_speaker_label = "Moderator"
        final_speaker_id = "moderator"
        final_role = "moderator"

        if emit_synthesis:
            try:
                synthesis = await generate_synthesis(
                    transcript,
                    user_question=user_question,
                    moderator_prompt=moderator_prompt,
                )
            except Exception:
                synthesis = {"answer": ""}
            final_text = str(synthesis.get("answer") or "").strip()
            total_usage = _merge_usage(total_usage, synthesis.get("usage") if isinstance(synthesis, Mapping) else None)
            transcript.append(
                TurnRecord(
                    {
                        "speakerId": "moderator",
                        "speakerLabel": "Moderator",
                        "role": "moderator",
                        "text": final_text,
                        "usage": _normalize_usage(synthesis.get("usage") if isinstance(synthesis, Mapping) else None),
                        "ts": time.time(),
                        "status": "completed",
                    }
                )
            )
            conversation_messages.append({"role": "assistant", "content": final_text})
            state["messages"] = conversation_messages
        else:
            for turn in reversed(transcript):
                if turn.get("status") == "completed" and isinstance(turn.get("text"), str) and turn["text"].strip():
                    final_text = turn["text"]
                    final_speaker_label = str(turn.get("speakerLabel") or final_speaker_label)
                    final_speaker_id = str(turn.get("speakerId") or final_speaker_id)
                    final_role = "assistant"
                    break

        if not final_text:
            final_text = (
                "I'm sorry, the panel could not confidently answer the request. "
                "Please provide additional context or try a different question."
            )
            final_speaker_label = "Moderator"
            final_speaker_id = "moderator"
            final_role = "moderator"
            conversation_messages.append({"role": "assistant", "content": final_text})
            state["messages"] = conversation_messages

        participant_turns = sum(
            1 for turn in transcript if str(turn.get("role") or "").lower() != "moderator"
        )

        groupchat_payload: GroupChatResult = {
            "nodeId": node_id,
            "label": node_label,
            "stopWhen": stop_when,  # type: ignore[assignment]
            "stopReason": stop_reason,
            "turns": transcript,
            "finalSpeaker": final_speaker_label,
            "finalSpeakerId": final_speaker_id,
            "finalText": final_text,
            "participants": [participant.label for participant in participants],
            "emitSynthesis": emit_synthesis,
            "usage": total_usage or None,
            "satisfaction": satisfaction_payload,
            "consensus": consensus_payload,
            "turnCount": participant_turns,
        }

        scratch = state.setdefault("scratch", {})
        agents_store: MutableMapping[str, Any] = scratch.setdefault("agents", {})
        groupchat_store: MutableMapping[str, Any] = scratch.setdefault("groupchat", {})

        agents_store[node_id] = {
            "last_output_text": final_text,
            "last_response": copy.deepcopy(groupchat_payload),
            "usage": total_usage or None,
            "groupchat": copy.deepcopy(groupchat_payload),
            "role": final_role,
        }
        groupchat_store[node_id] = copy.deepcopy(groupchat_payload)

        await _publish(
            telemetry,
            GROUPCHAT_RUN_COMPLETED,
            {
                "runId": runtime_ctx.run_id if runtime_ctx else None,
                "nodeId": node_id,
                "label": node_label,
                "stopReason": stop_reason,
                "turnCount": len(transcript),
            },
        )

        return state

    return _run


__all__ = ["build_groupchat_runner"]
