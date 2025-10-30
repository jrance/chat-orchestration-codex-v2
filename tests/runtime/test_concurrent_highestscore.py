import asyncio

import pytest

from app.runtime.patterns.concurrent import ConcurrentChildSpec, ConcurrentConfig, run_concurrent


def _child(label: str, confidence: float | None = None) -> ConcurrentChildSpec:
    async def _runner():
        await asyncio.sleep(0.01)
        payload = {"status": "completed", "output_text": f"{label} output"}
        if confidence is not None:
            payload["confidence"] = confidence
        return payload

    return ConcurrentChildSpec(node_id=label.lower(), label=label, runner=_runner)


@pytest.mark.anyio
async def test_highestscore_uses_existing_confidence_without_scorer(monkeypatch: pytest.MonkeyPatch):
    cfg = ConcurrentConfig(
        node_id="conc",
        label="Concurrent",
        strategy="HighestScore",
        timeout_seconds=5.0,
        max_parallelism=2,
        cancel_remaining_on_decision=False,
    )

    children = [_child("Alpha", 0.3), _child("Beta", 0.8), _child("Gamma", 0.6)]
    called = False

    async def _scorer(_children, **_kwargs):
        nonlocal called
        called = True
        return {}

    result = await run_concurrent(children, cfg, scorer=_scorer)
    assert not called
    assert result["chosen"] is not None
    assert result["chosen"]["nodeId"] == "beta"
    assert result["chosen"]["confidence"] == pytest.approx(0.8)


@pytest.mark.anyio
async def test_highestscore_invokes_scorer_when_confidence_missing():
    cfg = ConcurrentConfig(
        node_id="conc",
        label="Concurrent",
        strategy="HighestScore",
        timeout_seconds=5.0,
        max_parallelism=2,
        cancel_remaining_on_decision=False,
    )

    children = [_child("Alpha"), _child("Beta"), _child("Gamma")]

    async def _scorer(results, **_kwargs):
        return {res["nodeId"]: score for res, score in zip(results, (0.2, 0.9, 0.5))}

    result = await run_concurrent(children, cfg, scorer=_scorer)
    assert result["chosen"] is not None
    assert result["chosen"]["nodeId"] == "beta"
    assert result["chosen"]["confidence"] == pytest.approx(0.9)
