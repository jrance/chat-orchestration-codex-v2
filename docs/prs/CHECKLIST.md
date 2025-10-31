
# CHECKLIST — Codeless Orchestration Engine (PR‑01 → PR‑14a)

**Purpose:** A single, authoritative audit list for Codex (and reviewers) to verify **completeness, production readiness, and tenet compliance** across the entire project. Use this to *evaluate* the codebase; do not skip any section. Mark each checkbox as you confirm it locally.

> Run all commands using **uv** where shown. Keep tests deterministic (no real network).

---

## 0) Quick Outcome Gate (must pass before deeper review)
- [ ] `uv run pytest -q --cov=app --cov-report=term-missing` → **All tests pass**, coverage ≥ **80%** overall and on new modules.
- [ ] `uv run python -c "import json,sys; print('OK')"` → sanity check environment.
- [ ] `uv run uvicorn app.main:app --reload` → **/docs** and **/openapi.json** are reachable without errors.
  - Launch in a new shell, visit `http://127.0.0.1:8000/docs` and `http://127.0.0.1:8000/openapi.json`, then Ctrl+C to stop the server.
- [ ] No **uncommitted changes** after running tests (`git status` clean).
  - Quick clean-up: `git status -sb` → if files should land, `git add <paths> && git commit`; otherwise `git checkout -- <paths>` (or `git restore`).

---

## 1) Repo Hygiene & Tooling
- [ ] No `TODO`, `FIXME`, or commented‑out blocks left in committed code.
- [ ] Minimal dependencies: no unused packages; optional dev tools (`pytest`, `pytest-asyncio`, `pytest-cov`, optional `fakeredis`) are scoped to dev/test.
- [ ] `.env.example` exists and documents required vars; real `.env` not committed.
- [ ] VS Code ready: `.vscode/launch.json` supports F5 run with `.env` loading.
- [ ] Linting/formatting (optional but recommended): `ruff` or `flake8` + `black` configs present or documented.

---

## 2) Configuration & Secrets
- [ ] All config values read from `.env` via settings (e.g., Apigee OAuth URLs, client id/secret refs, CHECKPOINTER_BACKEND, etc.).
- [ ] **No secrets** hardcoded. Redaction enabled for logs if `LOG_REDACTION_ENABLED=true`.
- [ ] Reasonable defaults for local dev (e.g., `CHECKPOINTER_BACKEND=memory`).

---

## 3) API Surface (Versioned `/v1`)
- [ ] Endpoints implemented & versioned: `/v1/validate`, `/v1/compile`, `/v1/execute`, `/v1/execute/stream`, `/v1/execute/{runId}/resume`, `/v1/runs/{runId}`.
- [ ] **Headers** enforced/documented on all routes: `Authorization`, `X-Tenant-ID` (required), `X-Request-ID`, `X-Correlation-ID`, `X-Telemetry`, `X-Timestamp`.
- [ ] Request/response models use **Pydantic** schemas from `app/api/schemas.py`.
- [ ] Error envelopes consistent (`{ "error": { "code", "message", "details" } }`).

**OpenAPI**
- [ ] `/openapi.json` contains the new models (RunRequest, ResumeRequest, RunStatus, ErrorEnvelope).
- [ ] Shared header parameters included under `components.parameters` and referenced by all paths.
- [ ] Example payloads included; `/docs` renders accurately.

---

## 4) Streaming (OpenAI **Responses** Spec Fidelity)
- [ ] Main SSE uses **Responses** event names only: `response.created`, `response.output_text.delta`, `response.output_text.done`, `response.tool_call.*`, `response.tool_result.*`, `response.completed`, `response.error`.
- [ ] **Telemetry** stream is separate and controlled by `X-Telemetry: none|basic|verbose`.
- [ ] Tool call/results are relayed in stream (when present) and arguments/results are PII‑redacted when enabled.

---

## 5) Providers & HTTP Client (Apigee Gateway, Gemini‑compatible)
- [ ] Outbound client obtains **OAuth token** from Apigee, injects `Authorization: Bearer …` and **per‑request headers** (Client ID, `X-Tenant-ID`, `X-Request-ID`, `X-Correlation-ID`, `X-Timestamp`).
- [ ] Default headers + per‑request overrides are **configurable**.
- [ ] Timeouts, retries/backoff (where sensible) and **circuit‑breaker** hooks documented.
- [ ] Unit tests **mock** network; no real calls.

---

## 6) IR Validate & Compile
- [ ] `/v1/validate` performs **JSON‑Schema** validation of the orchestration IR + topology checks (entry, reachability, edge kinds).
- [ ] `/v1/compile` produces a normalized runtime plan (stable identifiers, resolved tool bindings, arg behaviors).

---

## 7) Agent Runtime — Patterns
**Sequential**
- [ ] Executes children in order; honors `breakOn` modes.

**Concurrent**
- [ ] Fans out respecting `max_parallelism` and `timeout_seconds`.
- [ ] Strategies implemented: `FirstBest` (early cancel), `HighestScore` (confidences or scorer), `Synthesize` (compose final via provider).
- [ ] Only the **final** message appears on main SSE; child tokens go to telemetry.
- [ ] Deterministic tests cover timeouts, cancellation, merge logic.

**Router**
- [ ] LLM routing via `response_format: json_schema`; `autoSyncEnum` enforces target set = child **labels**.
- [ ] Tie‑breakers: `HighestConfidence`, `DeterministicOrder`, `PreferList`; `allowBelowMinForTieBreak` respected.
- [ ] Fallbacks: `AskUserClarify` (pause), `DefaultChild`, `SafeAgent`, `Error`.

**GroupChat**
- [ ] Moderator helpers: `choose_next_speaker`, `is_satisfied`, `has_consensus`.
- [ ] `maxTurns` and `stopWhen` implemented; per‑turn deltas on **telemetry** only.
- [ ] Final synthesis (or best participant) emitted on main SSE.

---

## 8) Tools & MCP
- [ ] **Tool Registry** with JSON Schema exposure filtered for **LLMHidden** args.
- [ ] **LLMHidden** (schema‑hidden, injected at call) and **AgentOverride** (visible, overwritten at invoke) behaviors verified.
- [ ] Tool policy enforced: `Disabled | Auto | AlwaysAsk | Heuristic`, `maxCallsPerTurn`, `timeoutMs`, `parallelism`, PII redaction.
- [ ] MCP: minimal client/registry present; sample echo or stub tool works; tests do not need real MCP.

---

## 9) Context & History
- [ ] Default **context tokens** centralized (e.g., `{user_details}`) and expanded at runtime.
- [ ] Per‑agent **history window** (None | LastN | TimeBounded) applied; truncation deterministic.
- [ ] Structured Output (phase‑1): JSON schema + json_mode tagging supported; widget metadata surfaced for ChatKit.

---

## 10) Persistence & HITL (PR‑14 + PR‑014a)
- [ ] Checkpointer interface implemented; **memory** backend default with TTL.
- [ ] **Redis** backend usable (fakeredis in tests) and selectable via `CHECKPOINTER_BACKEND=redis` without import errors.
- [ ] Engine saves a checkpoint **before** side effects and at safe points.
- [ ] Router `AskUserClarify` and GroupChat pauses set `status=paused` and emit `hitl` metadata.
- [ ] `/v1/execute/{runId}/resume` validates `ResumeRequest`, calls `engine.resume_run(...)`, and **actually resumes** execution.
- [ ] Idempotent resume: checkpoint written on resume to avoid duplicate effects.

---

## 11) Logging & Telemetry
- [ ] **Structured logging** (request/response IDs, tenant) with redaction enabled when configured.
- [ ] Telemetry event set includes: router start/decision/fallback, concurrent child start/end/cancel/timeout, groupchat turn deltas, tool call/result, LLM request/response (summaries in basic mode; payload samples only in verbose with PII redaction).

---

## 12) Error Handling & Security
- [ ] Clear error taxonomy (`ROUTER_NO_ROUTE`, `RUN_NOT_FOUND`, `RUN_NOT_PAUSED`, etc.).
- [ ] No stack traces or secrets leak to clients; sensitive details available only in logs with redaction.
- [ ] Input validation on all APIs; size/time limits applied sensibly.
- [ ] Tenant filtering is enforced and forwarded to downstream calls.

---

## 13) Documentation (Developer‑friendly)
- [ ] **AGENTS.md**: core principles and project overview.
- [ ] **TOOLS_AND_MCP.md**: authoring tools, LLMHidden/AgentOverride, registry patterns.
- [ ] **CONCURRENT_ORCHESTRATION.md**
- [ ] **ROUTER_ORCHESTRATION.md**
- [ ] **GROUPCHAT_ORCHESTRATION.md**
- [ ] **HITL_AND_PERSISTENCE.md**
- [ ] **API docs**: Examples for each endpoint; cURL snippets for pause/resume and streaming.
- [ ] All docs are concise, copy‑pasteable, and accurate to current code.

---

## 14) Production Readiness Smoke (end‑to‑end)
1) **Validate** a sample IR (from the spec) with `/v1/validate` (expect 200).
2) **Execute** a simple sequential agent; confirm streaming tokens and `response.completed`.
3) **Router pause/resume**: configure `AskUserClarify`, stream until pause, then POST `/v1/execute/{runId}/resume` with `{"kind":"router_choice","choice":{"target":"Policy Agent"}}` → run completes.
4) **Concurrent**: set `FirstBest` with staggered children; observe early cancel telemetry and final stream equals first good child.
5) **GroupChat**: run 2–3 turns with `ModeratorSatisfied`; only final synthesis appears on main stream.
6) **Tools**: attach a tool with `LLMHidden` & `AgentOverride` args; confirm visible schema omission and invocation override behavior.
7) **Redis backend**: set `CHECKPOINTER_BACKEND=redis` with `REDIS_URL=fakeredis://` and ensure tests pass using the stub.

---

## 15) Tenets Audit (must be true)
- [ ] Code is **clean, readable, and modular**; small focused functions; type annotations everywhere.
- [ ] **Async‑first** I/O with bounded concurrency and cancellation where appropriate.
- [ ] **Minimal deps**; everything justified; no unused imports/files.
- [ ] **/v1** API versioning; OpenAPI reflects reality; examples included.
- [ ] Comprehensive **tests**; deterministic; coverage ≥ 80%.
- [ ] **Error taxonomy** present; safe messages to clients; secrets never leaked.
- [ ] **Headers** propagated end‑to‑end (tenant, correlation, request id).
- [ ] IR **schema + topology** enforced; failures give actionable messages.
- [ ] Compiler/extensibility simple; new nodes/tools plug in without refactor.
- [ ] **Responses** SSE event names match spec; no invented shapes.
- [ ] **Telemetry levels** respected; PII redaction applied in verbose mode.
- [ ] Timeouts/retries/circuit‑breaker (where sensible) documented.
- [ ] Trade‑offs documented; clarity favored over micro‑optimizations.
- [ ] PRs are small, atomic, and self‑contained; validation steps runnable.
- [ ] Docs are clear for junior devs; how to run/test is obvious.
- [ ] Graceful startup/shutdown; idempotent resume/retry behavior.
- [ ] App runs cleanly: `uvicorn app.main:app --reload` + `/docs` work.

---

## 16) Common Failure Modes (quick checks)
- [ ] Resume endpoint returns cached state (fix: call `engine.resume_run` with validated payload).
- [ ] Redis backend raises on import (fix: use fakeredis for tests; no RuntimeError on selection).
- [ ] Tools schema leaks **LLMHidden** args (fix: filter in visible schema function).
- [ ] Telemetry interleaves with main stream (fix: route per design; only final answer on main stream).
- [ ] Router targets vs child labels mismatch (fix: enable `autoSyncEnum` validation).

---

## Commands Reference
```bash
# Run tests
uv run pytest -q --cov=app --cov-report=term-missing

# Start API
uv run uvicorn app.main:app --reload

# Validate, Execute (sample)
curl -X POST http://localhost:8000/v1/validate -H "Content-Type: application/json" -d @ir.json
curl -N "http://localhost:8000/v1/execute/stream?runId=RUN123" -H "Authorization: Bearer TOKEN" -H "X-Tenant-ID: demo"

# Resume paused run
curl -X POST http://localhost:8000/v1/execute/RUN123/resume -H "Content-Type: application/json" \
  -H "X-Tenant-ID: demo" -d '{ "kind":"router_choice", "choice":{"target":"Policy Agent"} }'
```

---

### Final Verdict
When **all** boxes above are checked, this codebase should be **complete**, **maintainable**, and **production‑ready** per the project’s requirements and tenets.
