import asyncio

import pytest

from app.runtime.patterns.concurrent import ConcurrentChildSpec, ConcurrentConfig, run_concurrent


def _timeout_child(label: str, delay: float) -> ConcurrentChildSpec:
    async def _runner():
        await asyncio.sleep(delay)
        return {"status": "completed", "output_text": f"{label} done"}

    return ConcurrentChildSpec(node_id=label.lower(), label=label, runner=_runner)


@pytest.mark.anyio
async def test_child_timeout_returns_timeout_status():
    cfg = ConcurrentConfig(
        node_id="conc",
        label="Concurrent",
        strategy="HighestScore",
        timeout_seconds=0.05,
        max_parallelism=2,
        cancel_remaining_on_decision=False,
    )

    fast = _timeout_child("Fast", 0.01)
    slow = _timeout_child("Slow", 0.2)
    children = [fast, slow]

    async def _scorer(results, **_kwargs):
        return {res["nodeId"]: 1.0 if res["nodeId"] == "fast" else 0.1 for res in results}

    result = await run_concurrent(children, cfg, scorer=_scorer)
    statuses = {child["nodeId"]: child["status"] for child in result["children"]}
    assert statuses["fast"] == "completed"
    assert statuses["slow"] == "timeout"
    assert result["chosen"] is not None
    assert result["chosen"]["nodeId"] == "fast"


@pytest.mark.anyio
async def test_firstbest_cancels_long_running_children():
    cfg = ConcurrentConfig(
        node_id="conc",
        label="Concurrent",
        strategy="FirstBest",
        timeout_seconds=5.0,
        max_parallelism=2,
        cancel_remaining_on_decision=True,
    )

    async def _fast_runner():
        await asyncio.sleep(0.02)
        return {"status": "completed", "output_text": "fast"}

    async def _slow_runner():
        await asyncio.sleep(0.5)
        return {"status": "completed", "output_text": "slow"}

    fast = ConcurrentChildSpec(node_id="fast", label="Fast", runner=_fast_runner)
    slow = ConcurrentChildSpec(node_id="slow", label="Slow", runner=_slow_runner)

    result = await run_concurrent([fast, slow], cfg)
    statuses = {child["nodeId"]: child["status"] for child in result["children"]}
    assert statuses["fast"] == "completed"
    assert statuses["slow"] == "cancelled"
    assert result["chosen"] is not None
    assert result["chosen"]["nodeId"] == "fast"
