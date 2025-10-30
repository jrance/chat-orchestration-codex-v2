"""Helpers for composing and scoring concurrent orchestration outputs."""

from __future__ import annotations

import json
from typing import Any, Iterable, Mapping, Sequence

from app.providers.openai_like.responses import create_response
from app.runtime.context import RuntimeContext, get_runtime_context
from app.runtime.types import ChildResult

DEFAULT_SYNTH_PROMPT = (
    "You are merging answers from several specialized agents. Read them all, reconcile conflicts, "
    "and produce ONE concise, factual answer for the user.\n"
    "- Prefer answers with explicit policy references and citations.\n"
    "- If a fact is uncertain or conflicting, note the uncertainty.\n"
    "- Return only the final answer (no analysis). Keep formatting clean; use bullets or a small table if helpful."
)

SCORING_PROMPT = (
    "You are scoring agent answers on a 0-100 scale. For each answer, return JSON with "
    "`nodeId` and `score`. Consider relevance, factual accuracy based on provided content, and clarity. "
    "Do not invent facts. JSON format: {\"scores\": [{\"nodeId\": \"...\", \"score\": 87}, ...]}"
)

_DEFAULT_MODEL = "gpt-4o-mini"


def _headers_from_context(runtime_ctx: RuntimeContext | None) -> Mapping[str, str]:
    if runtime_ctx is None:
        runtime_ctx = get_runtime_context()
    if runtime_ctx is None:
        return {}
    return runtime_ctx.http_headers()


def _child_snapshot(children: Sequence[ChildResult]) -> str:
    parts: list[str] = []
    for idx, child in enumerate(children, start=1):
        label = child.get("label") or f"Child {idx}"
        status = child.get("status") or "completed"
        output = child.get("output_text") or ""
        parts.append(f"[{label}] status={status}\n{output}".strip())
    return "\n\n".join(parts)


async def compose_synthesis(
    children: Sequence[ChildResult],
    prompt: str | None = None,
    *,
    model: str | None = None,
    runtime_ctx: RuntimeContext | None = None,
) -> tuple[str, str]:
    """Call the provider to synthesize a merged answer and optional rationale."""

    headers = _headers_from_context(runtime_ctx)
    body = {
        "model": model or _DEFAULT_MODEL,
        "input": [
            {"role": "system", "content": prompt or DEFAULT_SYNTH_PROMPT},
            {
                "role": "user",
                "content": (
                    "Here are the candidate answers from child agents. "
                    "Merge them into a single high-quality response.\n\n"
                    f"{_child_snapshot(children)}"
                ),
            },
        ],
        "max_output_tokens": 512,
        "temperature": 0.1,
    }
    response = await create_response(body, context_headers=headers)
    output_text = str(response.get("output_text") or response.get("content") or "")
    metadata = response.get("metadata") if isinstance(response, Mapping) else {}
    rationale = ""
    if isinstance(metadata, Mapping):
        rationale = str(metadata.get("rationale") or "")
    return output_text, rationale


def _normalize_score(value: Any) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return 0.0
    if score <= 0:
        return 0.0
    if score >= 100:
        return 1.0
    return max(0.0, min(1.0, score / 100.0))


def _scores_from_response(payload: Mapping[str, Any]) -> Mapping[str, float]:
    raw_text = payload.get("output_text") or payload.get("content")
    if isinstance(raw_text, str) and raw_text:
        try:
            parsed = json.loads(raw_text)
        except json.JSONDecodeError:
            parsed = {}
    else:
        parsed = payload.get("scores")
    scores: dict[str, float] = {}
    if isinstance(parsed, Mapping):
        entries = parsed.get("scores")
    else:
        entries = parsed
    if isinstance(entries, Iterable):
        for item in entries:
            if not isinstance(item, Mapping):
                continue
            node_id = item.get("nodeId")
            if not isinstance(node_id, str):
                continue
            score = _normalize_score(item.get("score"))
            scores[node_id] = score
    return scores


async def score_candidates(
    children: Sequence[ChildResult],
    *,
    model: str | None = None,
    runtime_ctx: RuntimeContext | None = None,
) -> Mapping[str, float]:
    """Request scores for child outputs using the provider."""

    headers = _headers_from_context(runtime_ctx)
    child_descriptions = [
        {
            "nodeId": child.get("nodeId"),
            "label": child.get("label"),
            "status": child.get("status"),
            "output": child.get("output_text"),
        }
        for child in children
    ]
    body = {
        "model": model or _DEFAULT_MODEL,
        "input": [
            {"role": "system", "content": SCORING_PROMPT},
            {
                "role": "user",
                "content": json.dumps({"children": child_descriptions}, ensure_ascii=False),
            },
        ],
        "max_output_tokens": 200,
        "temperature": 0.1,
    }
    response = await create_response(body, context_headers=headers)
    if not isinstance(response, Mapping):
        return {}
    return _scores_from_response(response)


__all__ = ["DEFAULT_SYNTH_PROMPT", "compose_synthesis", "score_candidates"]
