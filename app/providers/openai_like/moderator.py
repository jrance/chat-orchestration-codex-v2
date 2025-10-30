"""Moderator helper calls for groupchat orchestration."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Mapping, MutableMapping, Sequence

from app.providers.openai_like.responses import create_response
from app.runtime.context import RuntimeContext, get_runtime_context

DEFAULT_MODEL = "gpt-4o-mini"
BASE_SYSTEM_PROMPT = (
    "You are a diligent conversation moderator coordinating a short, focused exchange among expert assistants."
    " Select speakers that add new signal, enforce brevity, and keep the panel on task."
)
SATISFACTION_SYSTEM_PROMPT = (
    "You are a self-critical moderator. Review the transcript and decide if the user's request has been fully answered."
)
CONSENSUS_SYSTEM_PROMPT = (
    "You are checking for consensus across participants. Determine if all participants now agree on the key answer."
)
SYNTHESIS_SYSTEM_PROMPT = (
    "You are the moderator summarizing the group discussion into a single, direct answer for the user."
)

NEXT_SPEAKER_SCHEMA = {
    "name": "next_speaker_choice",
    "schema": {
        "type": "object",
        "required": ["speaker"],
        "properties": {
            "speaker": {"type": "string", "description": "Id or label of the chosen participant."},
            "instruction": {
                "type": "string",
                "description": "One short sentence guiding the participant on what to cover next.",
            },
        },
        "additionalProperties": False,
    },
}

SATISFACTION_SCHEMA = {
    "name": "moderator_satisfaction",
    "schema": {
        "type": "object",
        "required": ["satisfied"],
        "properties": {
            "satisfied": {"type": "boolean"},
            "rationale": {"type": "string"},
        },
        "additionalProperties": False,
    },
}

CONSENSUS_SCHEMA = {
    "name": "moderator_consensus",
    "schema": {
        "type": "object",
        "required": ["allAgree"],
        "properties": {
            "allAgree": {"type": "boolean"},
            "summary": {"type": "string"},
            "confidence": {
                "type": "number",
                "minimum": 0,
                "maximum": 1,
            },
        },
        "additionalProperties": False,
    },
}

SYNTHESIS_SCHEMA = {
    "name": "moderator_synthesis",
    "schema": {
        "type": "object",
        "required": ["answer"],
        "properties": {
            "answer": {"type": "string"},
            "confidence": {
                "type": "number",
                "minimum": 0,
                "maximum": 1,
            },
        },
        "additionalProperties": False,
    },
}

MAX_TURNS_FOR_SELECTION = 6
MAX_TURNS_FOR_STOPPING = 10


def _headers_from_context(ctx: RuntimeContext | None) -> Mapping[str, str]:
    if ctx is None:
        return {}
    return ctx.http_headers()


def _truncated_transcript(transcript: Sequence[Mapping[str, Any]], limit: int) -> list[dict[str, Any]]:
    tail = list(transcript)[-limit:]
    formatted: list[dict[str, Any]] = []
    for item in tail:
        if not isinstance(item, Mapping):
            continue
        formatted.append(
            {
                "speakerId": item.get("speakerId"),
                "speakerLabel": item.get("speakerLabel"),
                "role": item.get("role"),
                "text": item.get("text"),
            }
        )
    return formatted


def _serialize_participants(participants: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    serialized: list[dict[str, Any]] = []
    for participant in participants:
        if not isinstance(participant, Mapping):
            continue
        serialized.append(
            {
                "id": participant.get("id"),
                "label": participant.get("label"),
                "expertise": participant.get("expertise"),
                "lastTurnIndex": participant.get("lastTurnIndex"),
            }
        )
    return serialized


def _response_body(
    *,
    system_prompt: str,
    user_payload: Mapping[str, Any],
    schema: Mapping[str, Any],
    model: str,
    max_tokens: int,
) -> dict[str, Any]:
    return {
        "model": model,
        "input": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
        ],
        "response_format": {"type": "json_schema", "json_schema": schema},
        "json_mode": True,
        "temperature": 0.2,
        "max_output_tokens": max_tokens,
    }


def _extract_json(response: Mapping[str, Any]) -> MutableMapping[str, Any]:
    output = response.get("output")
    if isinstance(output, Sequence):
        for item in output:
            if not isinstance(item, Mapping):
                continue
            content = item.get("content")
            if not isinstance(content, Sequence):
                continue
            for block in content:
                if not isinstance(block, Mapping):
                    continue
                if "json" in block:
                    json_payload = block["json"]
                    if isinstance(json_payload, Mapping):
                        return dict(json_payload)
                    if isinstance(json_payload, str):
                        try:
                            return dict(json.loads(json_payload))
                        except json.JSONDecodeError:
                            continue
    raw_text = response.get("output_text") or response.get("content")
    if isinstance(raw_text, str) and raw_text:
        try:
            parsed = json.loads(raw_text)
            if isinstance(parsed, Mapping):
                return dict(parsed)
        except json.JSONDecodeError:
            return {}
    return {}


def _usage_from_response(response: Mapping[str, Any]) -> dict[str, Any]:
    usage = response.get("usage")
    if isinstance(usage, Mapping):
        return dict(usage)
    return {}


async def choose_next_speaker(
    moderator_prompt: str,
    participants: Sequence[Mapping[str, Any]],
    transcript: Sequence[Mapping[str, Any]],
    *,
    user_question: str,
    constraints: Mapping[str, Any] | None = None,
    model: str = DEFAULT_MODEL,
    max_attempts: int | None = None,
) -> Mapping[str, Any]:
    """Return the moderator's next-speaker decision."""

    runtime_ctx = get_runtime_context()
    headers = _headers_from_context(runtime_ctx)
    payload = {
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "moderatorPrompt": moderator_prompt,
        "question": user_question,
        "participants": _serialize_participants(participants),
        "recentTranscript": _truncated_transcript(transcript, MAX_TURNS_FOR_SELECTION),
        "constraints": dict(constraints) if isinstance(constraints, Mapping) else {},
    }
    body = _response_body(
        system_prompt=f"{BASE_SYSTEM_PROMPT}\nModerator guidance:\n{moderator_prompt.strip()}",
        user_payload=payload,
        schema=NEXT_SPEAKER_SCHEMA,
        model=model,
        max_tokens=160,
    )
    response = await create_response(body, context_headers=headers, max_attempts=max_attempts)
    if not isinstance(response, Mapping):
        return {"speaker": "", "instruction": "", "usage": {}}
    selection = _extract_json(response)
    return {
        "speaker": str(selection.get("speaker") or ""),
        "instruction": str(selection.get("instruction") or "").strip(),
        "usage": _usage_from_response(response),
    }


async def is_satisfied(
    transcript: Sequence[Mapping[str, Any]],
    *,
    user_question: str,
    rubric: str,
    model: str = DEFAULT_MODEL,
    max_attempts: int | None = None,
) -> Mapping[str, Any]:
    """Ask the moderator whether the conversation has fully addressed the request."""

    runtime_ctx = get_runtime_context()
    headers = _headers_from_context(runtime_ctx)
    payload = {
        "question": user_question,
        "rubric": rubric,
        "recentTranscript": _truncated_transcript(transcript, MAX_TURNS_FOR_STOPPING),
    }
    body = _response_body(
        system_prompt=SATISFACTION_SYSTEM_PROMPT,
        user_payload=payload,
        schema=SATISFACTION_SCHEMA,
        model=model,
        max_tokens=120,
    )
    response = await create_response(body, context_headers=headers, max_attempts=max_attempts)
    if not isinstance(response, Mapping):
        return {"satisfied": False, "rationale": "", "usage": {}}
    result = _extract_json(response)
    satisfied = bool(result.get("satisfied"))
    rationale = str(result.get("rationale") or "").strip()
    return {"satisfied": satisfied, "rationale": rationale, "usage": _usage_from_response(response)}


async def has_consensus(
    transcript: Sequence[Mapping[str, Any]],
    *,
    participant_stances: Sequence[Mapping[str, Any]],
    rubric: str,
    model: str = DEFAULT_MODEL,
    max_attempts: int | None = None,
) -> Mapping[str, Any]:
    """Ask the moderator whether all participants agree on the answer."""

    runtime_ctx = get_runtime_context()
    headers = _headers_from_context(runtime_ctx)
    payload = {
        "rubric": rubric,
        "participantStances": list(participant_stances),
        "recentTranscript": _truncated_transcript(transcript, MAX_TURNS_FOR_STOPPING),
    }
    body = _response_body(
        system_prompt=CONSENSUS_SYSTEM_PROMPT,
        user_payload=payload,
        schema=CONSENSUS_SCHEMA,
        model=model,
        max_tokens=140,
    )
    response = await create_response(body, context_headers=headers, max_attempts=max_attempts)
    if not isinstance(response, Mapping):
        return {"allAgree": False, "summary": "", "confidence": 0.0, "usage": {}}
    result = _extract_json(response)
    all_agree = bool(result.get("allAgree"))
    summary = str(result.get("summary") or "").strip()
    confidence_raw = result.get("confidence")
    if isinstance(confidence_raw, (int, float)):
        confidence = max(0.0, min(1.0, float(confidence_raw)))
    else:
        confidence = 0.0
    return {
        "allAgree": all_agree,
        "summary": summary,
        "confidence": confidence,
        "usage": _usage_from_response(response),
    }


async def generate_synthesis(
    transcript: Sequence[Mapping[str, Any]],
    *,
    user_question: str,
    moderator_prompt: str,
    model: str = DEFAULT_MODEL,
    max_attempts: int | None = None,
) -> Mapping[str, Any]:
    """Ask the moderator to produce a concise final synthesis answer."""

    runtime_ctx = get_runtime_context()
    headers = _headers_from_context(runtime_ctx)
    payload = {
        "question": user_question,
        "moderatorPrompt": moderator_prompt,
        "transcript": _truncated_transcript(transcript, MAX_TURNS_FOR_STOPPING),
    }
    body = _response_body(
        system_prompt=f"{SYNTHESIS_SYSTEM_PROMPT}\nModerator guidance:\n{moderator_prompt.strip()}",
        user_payload=payload,
        schema=SYNTHESIS_SCHEMA,
        model=model,
        max_tokens=220,
    )
    response = await create_response(body, context_headers=headers, max_attempts=max_attempts)
    if not isinstance(response, Mapping):
        return {"answer": "", "confidence": 0.0, "usage": {}}
    result = _extract_json(response)
    answer = str(result.get("answer") or "").strip()
    confidence_raw = result.get("confidence")
    confidence = float(confidence_raw) if isinstance(confidence_raw, (int, float)) else 0.0
    confidence = max(0.0, min(1.0, confidence))
    return {
        "answer": answer,
        "confidence": confidence,
        "usage": _usage_from_response(response),
    }


__all__ = [
    "choose_next_speaker",
    "generate_synthesis",
    "has_consensus",
    "is_satisfied",
]
