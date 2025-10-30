import asyncio

import pytest

from app.runtime.patterns.concurrent import ConcurrentChildSpec, ConcurrentConfig, run_concurrent


def _child(label: str, text: str) -> ConcurrentChildSpec:
    async def _runner():
        await asyncio.sleep(0.01)
        return {"status": "completed", "output_text": text}

    return ConcurrentChildSpec(node_id=label.lower(), label=label, runner=_runner)


@pytest.mark.anyio
async def test_synthesize_strategy_merges_children_with_custom_prompt():
    cfg = ConcurrentConfig(
        node_id="conc",
        label="Concurrent",
        strategy="Synthesize",
        timeout_seconds=5.0,
        max_parallelism=3,
        cancel_remaining_on_decision=False,
        synth_prompt="Combine answers carefully",
    )

    children = [
        _child("Alpha", "Policy answer with references."),
        _child("Beta", "SharePoint data with citations."),
        _child("Gamma", "Teams conversation summary."),
    ]

    async def _synth(results, prompt, **_kwargs):
        assert prompt == "Combine answers carefully"
        merged = " | ".join(str(child.get("output_text") or "") for child in results)
        return merged, "Merged rationale"

    result = await run_concurrent(children, cfg, synthesizer=_synth)
    assert result["chosen"] is None
    assert result["merged_text"] == "Policy answer with references. | SharePoint data with citations. | Teams conversation summary."
    assert result["rationale"] == "Merged rationale"
