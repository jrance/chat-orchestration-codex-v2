# Codeless Orchestration Engine (Scaffold)

- FastAPI app with versioned `/v1` routes.
- OpenAPI docs at `/docs`.
- Env config via `.env` (see `.env.example`).
- No Docker; run with VS Code F5 or uvicorn.

## Quickstart
```bash
python -m venv .venv && . .venv/Scripts/activate  # Windows (or source .venv/bin/activate)
pip install -e ".[dev]"
cp .env.example .env
uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000/docs

### Endpoints
- POST `/v1/validate` – validates an orchestration IR (JSON Schema + topology) and returns a normalized plan snapshot.
- POST `/v1/execute` – executes an orchestration once and returns final output/usage metadata.
- POST `/v1/execute/stream` – streams OpenAI Responses-compatible SSE events; emits telemetry headers when enabled.
- POST `/v1/execute/{runId}/resume` – resumes a stored run using the in-memory checkpointer.
- GET `/v1/telemetry/stream` – subscribes to telemetry events emitted during execution.
- POST `/v1/compile` (stub – compiler lands in a later PR)

`/v1/validate` responds with:
- `ok`: boolean result
- `errors` / `warnings`: lists of human-readable strings
- `normalized`: includes `entryId`, `nodeById`, `edges`, and `resolved.agentToolBindings`

Set `ORCH_SCHEMA_PATH` in `.env` if you need to point validation at a custom schema location. See `docs/API_EXECUTE.md` for examples covering the execute and telemetry APIs.

## Apigee/OpenAI Gateway Client
- Configure Apigee and OpenAI-compatible endpoints via `.env` (see tokens, base URL, and HTTP tuning knobs in `.env.example`).
- `app.http.openai_client.OpenAICompatibleClient` wraps HTTPX with pooled connections, OAuth token caching, default headers, and retries for 429/5xx.
- `post_responses()` sends JSON to `/responses`; streaming helpers arrive in later PRs.

Next PRs add compilation to LangGraph, richer execution semantics, streaming mapping to the Responses API, and expanded telemetry.
