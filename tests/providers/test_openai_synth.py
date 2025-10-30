import json

import pytest

from app.providers.openai_like import DEFAULT_SYNTH_PROMPT
from app.providers.openai_like.synth import compose_synthesis, score_candidates
from app.runtime.types import ChildResult


def _children() -> list[ChildResult]:
    return [
        ChildResult({"nodeId": "a", "label": "Alpha", "status": "completed", "output_text": "First answer"}),
        ChildResult({"nodeId": "b", "label": "Beta", "status": "completed", "output_text": "Second answer"}),
    ]


@pytest.mark.anyio
async def test_compose_synthesis_uses_provider(monkeypatch: pytest.MonkeyPatch):
    captured_body = {}

    async def _stub_create_response(body, *, context_headers=None):
        captured_body.update(body)
        return {"output_text": "Merged answer", "metadata": {"rationale": "Rationale"}}

    monkeypatch.setattr("app.providers.openai_like.synth.create_response", _stub_create_response)

    merged, rationale = await compose_synthesis(_children(), prompt=None)
    assert DEFAULT_SYNTH_PROMPT in captured_body["input"][0]["content"]
    assert merged == "Merged answer"
    assert rationale == "Rationale"


@pytest.mark.anyio
async def test_score_candidates_parses_scores(monkeypatch: pytest.MonkeyPatch):
    async def _stub_create_response(body, *, context_headers=None):
        del body  # unused
        payload = {"scores": [{"nodeId": "a", "score": 87}, {"nodeId": "b", "score": 45}]}
        return {"output_text": json.dumps(payload)}

    monkeypatch.setattr("app.providers.openai_like.synth.create_response", _stub_create_response)

    scores = await score_candidates(_children())
    assert scores["a"] == pytest.approx(0.87)
    assert scores["b"] == pytest.approx(0.45)
