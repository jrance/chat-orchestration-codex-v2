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

### Endpoints (stubbed)
- POST `/v1/validate`
- POST `/v1/compile`
- POST `/v1/execute`
- GET `/v1/execute/{runId}/resume`
- POST `/v1/execute/stream` (SSE demo)

Next PRs add validation, compilation to LangGraph, execution, streaming mapping to Responses API, and telemetry.
