import asyncio

import pytest

from app.runtime.patterns.concurrent import ConcurrentChildSpec, ConcurrentConfig, run_concurrent
from app.telemetry.models import TelemetryLevel
from app.telemetry.streamer import TelemetryStreamConfig, TelemetryStreamer


def _streamer() -> TelemetryStreamer:
    return TelemetryStreamer(TelemetryStreamConfig(run_id="run-1", level=TelemetryLevel.VERBOSE, redact=False))


def _child(label: str, delay: float) -> ConcurrentChildSpec:
    async def _runner():
        await asyncio.sleep(delay)
        return {"status": "completed", "output_text": f"{label} answer"}

    return ConcurrentChildSpec(node_id=label.lower(), label=label, runner=_runner)


@pytest.mark.anyio
async def test_firstbest_picks_first_completion_and_cancels_others():
    cfg = ConcurrentConfig(
        node_id="concurrent-1",
        label="Concurrent",
        strategy="FirstBest",
        timeout_seconds=5.0,
        max_parallelism=3,
        cancel_remaining_on_decision=True,
    )

    children = [
        _child("Alpha", 0.03),
        _child("Beta", 0.1),
        _child("Gamma", 0.15),
    ]

    streamer = _streamer()
    recorded = []

    async def _consume():
        async for event in streamer.stream():
            recorded.append(event)

    consumer = asyncio.create_task(_consume())
    result = await run_concurrent(children, cfg, telemetry=streamer)
    await streamer.close()
    await consumer

    assert result["strategy"] == "FirstBest"
    assert result["chosen"] is not None
    assert result["chosen"]["nodeId"] == "alpha"
    statuses = {child["nodeId"]: child["status"] for child in result["children"]}
    assert statuses["alpha"] == "completed"
    assert statuses["beta"] == "cancelled"
    assert statuses["gamma"] == "cancelled"

    cancel_events = [evt for evt in recorded if evt.event == "telemetry.concurrent.child.cancelled"]
    assert len(cancel_events) == 2
