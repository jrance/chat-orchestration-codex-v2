# PROMPT FOR CODEX

Implement the following PR and validate that everything has been completed fully and that all tests pass and all acceptance criteria is met. Feel free to modify the recommended code if there is a better and cleaner approach.

---

# PR-13 of 14 — GroupChat Orchestration (Moderator, maxTurns, stopWhen)

## PR Title
Implement GroupChat orchestration with moderator control, maxTurns, and stopWhen (ModeratorSatisfied | AllAgree) plus streaming/telemetry (PR 13 of 14)

## Description
This PR introduces the **GroupChat** orchestration node that coordinates a short, focused multi‑agent discussion moderated by an LLM moderator. The moderator:
- selects the **next speaker** each turn,
- enforces **role rules** and brevity,
- evaluates **stop conditions** (`ModeratorSatisfied` or `AllAgree`),
- optionally produces a **final synthesis** answer.

Streaming design: All per‑turn utterances (participants + moderator prompts/decisions) are routed to the **telemetry** stream (with speaker labels) to avoid interleaving on the primary ChatKit stream. The **final answer** (either the moderator’s concluding message or the chosen participant’s answer) is emitted on the **main Responses stream**.

The orchestration schema can be found at schemas\orchestration_ir.schema.json and examples of valid orchestration packages can be found in schemas\examples. Use the examples to ensure that the LangGraph app compiles correctly.

## Purpose
- Provide a composable way to get experts (child agents) to collaborate under a moderator.
- Keep output stable for the chat UI while still exposing rich per‑turn telemetry for debugging and audit.

## Scope
- Compiler: normalize GroupChat config; collect children; validate topology.
- Runtime: groupchat controller (turn loop, next‑speaker policy, stop conditions).
- Provider: small helpers for moderator decisions and (optional) final synthesis.
- SSE/Telemetry: per‑turn deltas as telemetry; final assistant on main stream.
- Tests: turn limit, next‑speaker selection, both stop conditions, streaming routing.

---

## Files to Add / Modify

```
app/
├─ compiler/nodes/groupchat.py                 # NEW: normalize IR → runtime plan
├─ providers/openai_like/moderator.py          # NEW: moderator helpers (next speaker, satisfied?, consensus)
├─ runtime/patterns/groupchat.py               # NEW: controller loop
├─ runtime/engine.py                           # MOD: integrate groupchat node
├─ runtime/types.py                            # MOD: GroupChatResult, TurnRecord types
├─ telemetry/events.py                         # MOD: add groupchat event constants
tests/
├─ runtime/test_groupchat_turns_and_stop.py
├─ runtime/test_groupchat_allagree.py
├─ runtime/test_groupchat_streaming_routing.py
docs/
└─ GROUPCHAT_ORCHESTRATION.md                  # NEW: behavior, config, prompts, examples
```
> Note: keep names consistent with earlier PRs; adapt paths if your tree differs.

---

## IR → Runtime normalization

Given IR (excerpt):
```json
{
  "id": "gc-1",
  "kind": "groupchat",
  "label": "Policy+M365 Roundtable",
  "data": {
    "moderatorPrompt": "Coordinate experts and stop when satisfied.",
    "maxTurns": 6,
    "stopWhen": "ModeratorSatisfied"
  }
}
```
Normalize to:
```python
{
  "id": node.id,
  "kind": "groupchat",
  "label": node.label,
  "cfg": {
    "moderator_prompt": str,          # required
    "max_turns": int,                 # default 6
    "stop_when": "ModeratorSatisfied" | "AllAgree",
    "emit_synthesis": True,           # default True → moderator ends with final answer
    "speaker_budget_tokens": 400,     # soft guidance to participants
  },
  "participants": [child_node_ids],   # outgoing edges order = default order
}
```
Validation rules:
- ≥2 participant children (recommend 2–4).
- `maxTurns >= 1`.
- Unique labels across participants.
- If `stop_when="AllAgree"`, at least 2 participants are required.

---

## Provider helpers (`app/providers/openai_like/moderator.py`)

Three helpers using the existing `/v1/responses` adapter (non‑streaming, low tokens, `temperature=0.2`):

1) **choose_next_speaker()**
```
Input: moderator_prompt, participants(meta), recent transcript (last N turns), user question, constraints.
Output JSON (json_schema): { "speaker": "<label or id>", "instruction": "brief guidance for the speaker" }
```

2) **is_satisfied()**
```
Input: transcript, user question, satisfaction rubric.
Output JSON: { "satisfied": true|false, "rationale": "..." }
```

3) **has_consensus()**
```
Input: transcript (last M turns), participant stances (headlines), conflict rubric.
Output JSON: { "allAgree": true|false, "summary": "...", "confidence": 0..1 }
```

> These helpers must be small, cheap calls; limit `max_tokens` and keep prompts compact. Reuse system guidance constants in the module (documented in code).

---

## Runtime controller (`app/runtime/patterns/groupchat.py`)

Responsibilities:
- Maintain a `transcript: list[TurnRecord]` where a turn is `{speakerId, speakerLabel, role: "assistant"|"moderator", text, usage, ts}`.
- Loop up to `cfg.max_turns` turns:
  1. Ask moderator for **next speaker** (`choose_next_speaker`). If invalid, fallback to round‑robin.
  2. Execute the chosen participant **agent** once for a short message (pass `speaker_budget_tokens`). Route its token deltas to telemetry with events like:
     - `telemetry.groupchat.turn.delta` (fields: runId, nodeId, speakerId, label, delta, turnIndex)
     - `telemetry.groupchat.turn.completed` (fields: usage, totalTokens, etc.)
  3. Append to `transcript`.
  4. Evaluate **stop condition**:
     - `ModeratorSatisfied` → call `is_satisfied`; if true, break.
     - `AllAgree` → call `has_consensus`; if true, break.
- On loop end:
  - If `cfg.emit_synthesis=True`: ask the moderator for a **final synthesis** answer based on the transcript and user question; return that as the **final assistant** message.
  - Else: pick the **best participant** final answer (e.g., last turn or highest confidence if available).

Edge cases:
- Participant error/timeout → record `turn` as `status="error"` and continue, unless all fail.
- If zero successful turns → return a polite fallback answer and log an orchestration warning.

---

## Engine integration (`app/runtime/engine.py`)

- Add `run_groupchat_node(node, ctx)` that calls the controller and manages streaming.
- **Streaming rules**:
  - During the roundtable, **only telemetry** is emitted per token (`telemetry.groupchat.turn.delta`).
  - When the final answer is ready, emit on the **main Responses SSE** as:
    - `response.output_text.delta` chunks (or one shot if non‑streaming), then `response.completed`.
  - Include metadata on `response.completed`:
    ```json
    { "type":"response.completed",
      "response": { "id":"...", "metadata": {
        "groupchat": { "turns": N, "stopWhen":"ModeratorSatisfied", "finalSpeaker":"Moderator", "participants":[...labels...] }
      } } }
    ```

---

## Tests

### `tests/runtime/test_groupchat_turns_and_stop.py`
- Configure `maxTurns=3`, `stopWhen=ModeratorSatisfied`.
- Mock moderator to pick alternating speakers, and to be satisfied at turn 2.
- Assert total turns == 2 and final answer emitted once on main stream.

### `tests/runtime/test_groupchat_allagree.py`
- `stopWhen=AllAgree` with three participants; mock consensus true after 2nd turn.
- Assert `has_consensus` invoked and loop stops early.

### `tests/runtime/test_groupchat_streaming_routing.py`
- Verify deltas from participants go to telemetry only, and final answer goes to main Responses stream.
- Confirm `response.completed` metadata includes `groupchat` fields.

> All tests should be deterministic with mocked provider calls. No network.

---

## Docs (`docs/GROUPCHAT_ORCHESTRATION.md`)

Include:
- Config table and defaults.
- Example IR and expected behavior.
- Moderator prompt tips (include `{participants}`, `{rules}`, `{user_question}` tokens).
- Streaming diagrams: telemetry vs final stream.
- Troubleshooting (e.g., oscillating speakers, no consensus).

---

## Step‑By‑Step Implementation

1. **Compiler**: implement `groupchat.py` to normalize config and collect participants in edges order.
2. **Provider helpers**: create `moderator.py` with compact prompts + `json_schema` responses for the three decisions.
3. **Runtime**: implement controller loop with per‑turn execution and stop checks; write transcript.
4. **Engine**: wire node execution; ensure SSE routing logic matches telemetry vs main stream.
5. **Docs/Examples**: author `GROUPCHAT_ORCHESTRATION.md` with copy‑pasteable snippets.
6. **Tests**: add the three test modules; enforce ≥80% coverage on changed files.

---

## Acceptance Criteria
- [ ] GroupChat compiles and validates; requires ≥2 participants and `maxTurns>=1`.
- [ ] Moderator picks next speaker; stable fallback (round‑robin) if invalid.
- [ ] Stop conditions honored: `ModeratorSatisfied` and `AllAgree` implemented and tested.
- [ ] Per‑turn deltas go to telemetry; only final answer is streamed on the main Responses SSE.
- [ ] `response.completed` contains `groupchat` metadata.
- [ ] Unit tests pass; coverage ≥ 80% on new/modified code.
- [ ] Documentation clearly explains configuration and examples.

---

## Validation
```bash
uv run pytest -q --cov=app --cov-report=term-missing

# Manual smoke:
# - Build a simple groupchat with Policy + M365 agents and a neutral moderator.
# - Ask a policy question; verify 1–3 turns and early stop with a final moderator synthesis.
```

---

## Manual Pre/Post Items
- Ensure participant labels are short and descriptive; they appear in telemetry and prompts.
- If you need the moderator’s **final answer style**, put guidance into `moderatorPrompt` (e.g., “Use bullets; cite sources if present”).

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
