
# PROMPT FOR CODEX

Implement the following PR and validate that everything has been completed fully and that all tests pass and all acceptance criteria is met. Feel free to modify the recommended code if there is a better and cleaner approach.

---

# PR-11 of 14 — Concurrent Orchestration + Merge Strategies (Synthesize | HighestScore | FirstBest)

## PR Title
Implement concurrent (fan‑out/fan‑in) orchestration with merge strategies and proper cancellation/timeout handling (PR 11 of 14)

## Description
This PR introduces a **concurrent orchestration node** that fans out to multiple child agents/tools and then **merges** their results according to a configurable strategy:
- **Synthesize**: Ask the LLM to synthesize the best final answer from all child outputs (optional custom prompt).
- **HighestScore**: Pick the child with the highest score/confidence (uses per‑child `confidence` if present; otherwise auto‑scores via a lightweight LLM rating prompt).
- **FirstBest**: Return the first child result that meets a threshold (or simply the first completed if no threshold is set).

It also adds robust **timeouts**, **max parallelism**, and **early cancellation** (e.g., cancel remaining children once a decision is made for FirstBest). Streaming considerations: child token streams are routed to **telemetry** to avoid interleaving in the primary ChatKit stream; the merged final is emitted on the **main Responses stream**.

The orchestration schema can be found at schemas\orchestration_ir.schema.json and examples of valid orchestration packages can be found in schemas\examples. Use the examples to ensure that the LangGraph app compiles correctly.

## Purpose
- Enable fast, resilient, and configurable fan‑out to specialized agents (e.g., SharePoint/OneDrive/Teams).
- Keep UI stable by streaming only the **final merged response** on the main SSE; send child progress to telemetry.
- Provide clean, extensible merge strategies.

## Scope
- Compiler: read `ConcurrentNode.data` and normalize settings.
- Runtime: concurrent executor, child result envelopes, merge orchestrator, cancellation, timeouts.
- Provider: small helper for **synthesis** and optional **scoring**.
- Telemetry: structured events for child start/end, timeouts, cancellations, merge decisions.
- Tests: deterministic unit tests for all strategies and edge cases.

---

## Files to Add / Modify

```
app/
├─ compiler/nodes/concurrent.py            # NEW: translate IR → runtime plan for concurrent nodes
├─ runtime/patterns/concurrent.py          # NEW: executor + merge strategies
├─ runtime/types.py                        # MOD: add ChildResult/ConcurrentResult types
├─ runtime/engine.py                       # MOD: call concurrent executor; emit telemetry + final Responses events
├─ providers/openai_like/synth.py          # NEW: synthesize + score helpers via /v1/responses
├─ telemetry/events.py                     # NEW: event constants + helpers
tests/
├─ runtime/test_concurrent_firstbest.py
├─ runtime/test_concurrent_highestscore.py
├─ runtime/test_concurrent_synthesize.py
├─ runtime/test_concurrent_timeouts_cancel.py
docs/
└─ CONCURRENT_ORCHESTRATION.md             # NEW: strategy semantics, config, and examples
```

---

## IR → Runtime (normalization rules)

The IR schema shows for `ConcurrentNode.data`:
```json
{
  "timeoutSeconds": 120,
  "merge": { "strategy": "Synthesize" | "HighestScore" | "FirstBest", "synthPrompt": "..." }
}
```
**Samples** sometimes use `timeoutSec` and `maxParallelism`. Normalize to:
- `timeout_seconds`: prefer `data.timeoutSeconds`; fallback to `data.timeoutSec`; default = 120.
- `max_parallelism`: prefer `data.maxParallelism` or `tools.parallelism` on each child agent (fallback 4).
- `merge.strategy`: required (default `"FirstBest"` if omitted).
- `merge.synthPrompt`: optional. If missing for `Synthesize`, use a robust default (see below).
- `cancel_remaining_on_decision`: default `true` for `FirstBest` and `HighestScore`; `false` for `Synthesize`.

Validation:
- Node must have ≥1 outbound edges to child agents/tools.
- If `Synthesize` but **no children**, return 400 from `/v1/validate` (already PR‑01 but re-check here).

---

## Runtime types (`app/runtime/types.py`)

```python
from __future__ import annotations
from typing import TypedDict, Literal, Any

class ChildResult(TypedDict, total=False):
    nodeId: str
    label: str
    status: Literal["completed","timeout","error","cancelled"]
    output_text: str | None
    confidence: float | None   # 0..1, if agent set one
    usage: dict | None
    error: str | None
    meta: dict                 # free-form, tool outputs, citations, etc.

class ConcurrentResult(TypedDict, total=False):
    strategy: Literal["Synthesize","HighestScore","FirstBest"]
    chosen: ChildResult | None
    children: list[ChildResult]
    merged_text: str | None    # for Synthesize
    rationale: str | None
```

---

## Concurrent executor (`app/runtime/patterns/concurrent.py`)

Key responsibilities:
- Launch all children respecting `max_parallelism` (use `asyncio.Semaphore`).
- Apply per‑child timeouts (use node timeout or child override if present).
- Stream child **LLM/tool** deltas to **telemetry** (`telemetry.concurrent.child.delta`), not to the main Responses stream.
- Collect `ChildResult` for each.
- Decide final according to strategy:
  - **FirstBest**: as soon as the first `completed` child arrives (optionally requiring `confidence >= threshold`, which you can make a hidden optional setting, default `None`), cancel others if `cancel_remaining_on_decision`.
  - **HighestScore**: when all finished (or deadline), pick highest `confidence`. If none provided, call `providers/openai_like/synth.score_candidates()` with a compact rubric; if tie, prefer earliest completed.
  - **Synthesize**: wait for all finished (or deadline), pass the child outputs to `synth.compose()` with a prompt; return merged text.
- Emit telemetry: `telemetry.concurrent.start`, `telemetry.concurrent.child.start`, `...end`, `...timeout`, `...cancelled`, `telemetry.concurrent.merge.decision`.

Pseudocode excerpt:
```python
async def run_concurrent(children, cfg, ctx) -> ConcurrentResult:
    sem = asyncio.Semaphore(cfg.max_parallelism)
    results: list[ChildResult] = []
    tasks = {}

    async def run_child(child):
        try:
            async with sem:
                # engine.run_subgraph_child handles streaming→telemetry; returns ChildResult
                return await engine_run_child(child, cfg.timeout_seconds, ctx)
        except asyncio.TimeoutError:
            return {"nodeId": child.id, "label": child.label, "status":"timeout", "output_text": None}
        except Exception as e:
            return {"nodeId": child.id, "label": child.label, "status":"error", "error": str(e)}

    for c in children:
        tasks[c.id] = asyncio.create_task(run_child(c))

    if cfg.strategy == "FirstBest":
        while tasks:
            done, _ = await asyncio.wait(tasks.values(), return_when=asyncio.FIRST_COMPLETED)
            for t in done:
                res = t.result()
                results.append(res)
                if res.get("status") == "completed":
                    chosen = res
                    if cfg.cancel_remaining_on_decision:
                        for k, task in list(tasks.items()):
                            if task is not t and not task.done():
                                task.cancel()
                        return {"strategy":"FirstBest","chosen":chosen,"children":results, "merged_text": None}
                    # else continue to gather remaining results for record
            tasks = {k:v for k,v in tasks.items() if not v.done()}
        # if none completed, pick any (e.g., least-bad)
        chosen = next((r for r in results if r["status"] == "completed"), results[0] if results else None)
        return {"strategy":"FirstBest","chosen":chosen,"children":results,"merged_text": None}

    # Wait for all tasks otherwise
    results.extend([await t for t in tasks.values()])

    if cfg.strategy == "HighestScore":
        chosen = pick_highest_score(results, ctx)
        return {"strategy":"HighestScore","chosen":chosen,"children":results,"merged_text": None}

    if cfg.strategy == "Synthesize":
        merged, rationale = await compose_synthesis(results, cfg.synth_prompt, ctx)
        return {"strategy":"Synthesize","chosen": None, "children": results, "merged_text": merged, "rationale": rationale}
```

---

## Provider helpers (`app/providers/openai_like/synth.py`)

- `compose_synthesis(children, prompt, ctx)` — Builds a compact prompt that lists child outputs with labels and asks the model to produce a concise, well‑structured final answer. If any child has citations, request a unified citation block.
- `score_candidates(children, ctx)` — Rates each child’s output on a 0–100 scale using a tiny rubric (relevance, factuality based on provided content, clarity). Returns normalized 0..1 scores.

> Use the same `/v1/responses` adapter from PR‑09. These are short non‑streaming calls with low `max_tokens` and deterministic temperature (e.g., 0.1).

Default synthesis prompt (safe starter):
```
You are merging answers from several specialized agents. Read them all, reconcile conflicts, and produce ONE concise, factual answer for the user.
- Prefer answers with explicit policy references and citations.
- If a fact is uncertain or conflicting, note the uncertainty.
- Return only the final answer (no analysis). Keep formatting clean; use bullets or a small table if helpful.
```

---

## Engine integration (`app/runtime/engine.py`)

- Add a `run_concurrent_node(node, ctx)` that calls `runtime/patterns/concurrent.run_concurrent(...)` with children derived from graph topology.
- For **streaming** `/v1/execute/stream`:
  - While children run, send only **telemetry** events for their deltas (e.g., `telemetry.llm.delta`, labeled by child `nodeId`/`label`).
  - After merge completes, emit the final assistant stream in **Responses** format:
    - For `Synthesize`, stream the synthesized text (if the provider supports streaming), else send as one `response.output_text.delta` followed by `response.completed`.
    - For `FirstBest` / `HighestScore`, emit the **chosen child’s output** as the final stream.
  - Always conclude with `response.completed` and include `concurrent` metadata:
    ```json
    { "type":"response.completed",
      "response": { "id": "...", "metadata": { "concurrent": { "strategy":"HighestScore", "childrenCount":3, "chosenNodeId":"..." } } } }
    ```

---

## Compiler (`app/compiler/nodes/concurrent.py`)

- Parse `data`:
  - `timeout_seconds`, `max_parallelism`, `merge.strategy`, `merge.synthPrompt`, `cancel_remaining_on_decision`.
- Collect child node refs from outgoing edges.
- Store a **runtime plan** for the engine:
  ```python
  {
    "id": node.id,
    "kind": "concurrent",
    "label": node.label,
    "cfg": { ...normalized... },
    "children": [child_node_ids]
  }
  ```

---

## Tests

### `tests/runtime/test_concurrent_firstbest.py`
- Set three fake children with staggered completion times (100ms, 300ms, 500ms). Use `FirstBest` with `cancel_remaining_on_decision=True`.
- Assert only the first completion is chosen and others cancelled; verify telemetry shows cancellations.

### `tests/runtime/test_concurrent_highestscore.py`
- Provide child outputs with `confidence` fields: 0.3, 0.8, 0.6. Ensure HighestScore picks 0.8 without calling the scorer.
- Provide another case without confidences; ensure scorer is called and selection is stable.

### `tests/runtime/test_concurrent_synthesize.py`
- Provide three children with complementary content; ensure `compose_synthesis` returns merged text and rationale (non‑empty).

### `tests/runtime/test_concurrent_timeouts_cancel.py`
- One child sleeps beyond `timeout_seconds`; ensure status=`timeout` and merge still proceeds.
- For `FirstBest` with long‑runner and short‑runner, verify early cancel works.

> All tests should mock network calls and be **deterministic**. Use `asyncio.sleep` with virtual time or small real durations.

---

## Step‑By‑Step Implementation

1. **Compiler**: add `concurrent.py` to normalize settings and record children.
2. **Types**: add `ChildResult` and `ConcurrentResult` to `runtime/types.py` and export for engine & tests.
3. **Provider helpers**: implement `synth.py` using PR‑09 adapter for `compose_synthesis` and `score_candidates`.
4. **Executor**: implement `runtime/patterns/concurrent.py` with semaphore, timeouts, decision logic, and telemetry.
5. **Engine**: wire `run_concurrent_node(...)` into the execution plan; integrate with streaming rules (telemetry vs main stream).
6. **OpenAPI docs**: update `docs/CONCURRENT_ORCHESTRATION.md` with examples and config table.
7. **Tests & coverage**: add the four test modules; keep coverage ≥ 80% for new code.

---

## Acceptance Criteria
- [ ] Concurrent node fans out to all children respecting `max_parallelism` and `timeout_seconds`.
- [ ] `FirstBest` returns the first successful child and cancels others (when configured).
- [ ] `HighestScore` selects the highest confidence; falls back to scorer when confidences missing.
- [ ] `Synthesize` waits for all child results (or deadline) and returns a merged answer via provider helper.
- [ ] Child token streams go to **telemetry**, not the main Responses stream; main stream only emits the final answer.
- [ ] Telemetry includes start/end/timeout/cancel and final merge decision.
- [ ] Unit tests pass; coverage ≥ 80% on new modules.
- [ ] Docs explain strategies and provide runnable examples.

---

## Validation
```bash
uv run pytest -q --cov=app --cov-report=term-missing

# Manual smoke with Sample Orchestration 1 (M365 Agent concurrent):
# - Configure FirstBest and ensure one child is picked quickly.
# - Configure HighestScore and ensure best child (by confidence or scorer) is returned.
# - Configure Synthesize and verify the merged answer appears on the main stream.
```

---

## Manual Pre/Post Items
- Ensure child agent nodes attached to your Concurrent node have sensible `label`s to show in telemetry and synthesis prompts.
- If you want strict non‑cancellation (gather all results for audit), set `cancel_remaining_on_decision=false`.
- For corporate latency, consider lowering `timeout_seconds` per environment.

---

## PR Tenets (Append to All PRs)
- Ensure code is clean, readable, and well-structured; keep functions small and focused.  
- Prefer async I/O; make hot paths non-blocking and bound concurrency where needed.  
- Type-annotate everything; validate inputs/outputs with Pydantic models.  
- Keep dependencies minimal and justified; remove anything unused.  
- Maintain API versioning (`/v1/*`) and accurate OpenAPI docs.  
- Write comprehensive tests (unit + light integration); keep coverage ≥ 80%.  
- Make tests deterministic (no network, time, or randomness without fixtures/mocks).  
- Implement clear error taxonomy; never leak stack traces or secrets in responses.  
- Use correlation/request IDs end-to-end; log enough to trace a request.  
- Honor configuration via `.env`; never hardcode secrets or tenant data.  
- Propagate required headers (tenant, correlation, auth) to downstream services.  
- Follow JSON-Schema & topology rules for the IR; fail fast with actionable messages.  
- Keep the orchestration compiler/extensibility simple (new nodes/tools are plug-in friendly).  
- Match OpenAI **Responses** SSE event names verbatim; don’t invent new shapes.  
- Respect telemetry level header (`none|basic|verbose`) and apply PII redaction when enabled.  
- Add timeouts, retries (with backoff), and circuit-breaker logic where appropriate.  
- Favor clarity over micro-optimizations; document any intentional trade-offs.  
- No commented-out code or TODOs; use issues/PR descriptions for follow-ups.  
- Keep PRs small, atomic, and self-contained with runnable validation steps.  
- Provide clear docs for junior devs (what it does, why, how to run/test).  
- Ensure graceful startup/shutdown and idempotent behavior on resume/retry.  
- Leave the system in a working state: `uvicorn app.main:app --reload` + `/docs` must work.
