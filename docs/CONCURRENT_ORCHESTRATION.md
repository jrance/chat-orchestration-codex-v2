Concurrent orchestration nodes fan-out to multiple child agents or tools and merge their results when execution completes (or a winning answer is found). The runtime coordinates these child executions with bounded concurrency, per-child timeouts, and merge strategies that determine the final response emitted back to the caller.

## Merge Strategies
- **FirstBest** – Return the first child that finishes successfully. Optional confidence thresholds can be applied; remaining children are cancelled when a winner is found (configurable).
- **HighestScore** – Wait for all children to finish, then pick the response with the highest confidence. When confidences are missing, the runtime requests lightweight ratings through the provider and normalizes them to 0–1.
- **Synthesize** – Wait for every child (or timeout) and ask the provider to compose a single merged answer using the child outputs as context.

All strategies emit structured telemetry covering child lifecycle (start, completion, timeout, cancellation, error) and the final merge decision.

## Configuration (`ConcurrentConfig`)

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `node_id` | `str` | required | Unique identifier of the concurrent node. |
| `label` | `str` | `""` | Human-readable label used in telemetry and synthesis prompts. |
| `strategy` | `Literal["FirstBest","HighestScore","Synthesize"]` | `FirstBest` | Merge strategy selector. |
| `timeout_seconds` | `float` | `120.0` | Maximum time each child is allowed to run before timing out. |
| `max_parallelism` | `int` | `4` | Upper bound on concurrent child executions. |
| `cancel_remaining_on_decision` | `bool` | Strategy-dependent | Cancel unfinished children once a decision is made (FirstBest/HighestScore default to `True`; Synthesize to `False`). |
| `synth_prompt` | `str \| None` | provider default | Optional custom synthesis prompt. |
| `first_best_threshold` | `float \| None` | `None` | Minimum confidence required to accept a FirstBest child. |

Each child is represented by a `ConcurrentChildSpec` that bundles a runnable coroutine along with metadata (node id, label, and optional timeout override).

## Example

```python
import asyncio

from app.runtime.patterns.concurrent import ConcurrentChildSpec, ConcurrentConfig, run_concurrent


async def sharepoint_runner():
    return {"status": "completed", "output_text": "SharePoint answer", "confidence": 0.7}


async def onedrive_runner():
    return {"status": "completed", "output_text": "OneDrive summary", "confidence": 0.6}


async def main():
    cfg = ConcurrentConfig(
        node_id="m365-concurrent",
        label="M365 Specialists",
        strategy="HighestScore",
        timeout_seconds=30.0,
        max_parallelism=2,
        cancel_remaining_on_decision=True,
    )

    children = [
        ConcurrentChildSpec(node_id="sharepoint", label="SharePoint Agent", runner=sharepoint_runner),
        ConcurrentChildSpec(node_id="onedrive", label="OneDrive Agent", runner=onedrive_runner),
    ]

    result = await run_concurrent(children, cfg)
    print(result["chosen"]["label"])  # "SharePoint Agent"


asyncio.run(main())
```

The concurrent executor automatically publishes telemetry describing the child lifecycle and merge decision, which can be streamed to clients for real-time insight into orchestration progress.
