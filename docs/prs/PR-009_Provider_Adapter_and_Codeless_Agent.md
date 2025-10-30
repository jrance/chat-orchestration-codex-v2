
# PROMPT FOR CODEX

Implement the following PR and validate that everything has been completed fully and that all tests pass and all acceptance criteria is met. Feel free to modify the recommended code if there is a better and cleaner approach.

---

# PR-09 of 14 — OpenAI‑Compatible Provider Adapter (via Apigee) + Codeless Agent LLM Execution

## PR Title
Implement OpenAI‑compatible provider adapter (Gemini behind Apigee) and wire real LLM execution into the Codeless Agent (PR 9 of 14)

## Description
This PR adds a production‑grade **provider adapter** that speaks the **OpenAI Responses API** over HTTP/SSE to a **Gemini OpenAI‑compatible endpoint** that sits behind **Apigee**. We implement OAuth token acquisition + caching, default header injection (tenant/correlation/request/timestamp/client), resilient retries/timeouts, and map streaming **SSE** frames into internal events that match the **Responses API** event taxonomy (e.g., `response.created`, `response.output_text.delta`, `response.completed`).

We then **replace the stub behavior** in the `agent.codeless` node with a real LLM call (sync + streaming). Phase‑1 scope includes basic prompt assembly (system + history windowing + user input), optional **JSON structured output** using `json_mode`, and **telemetry** at request/response boundaries. Tool calling and MCP integration land in a later PR.

> References:
> - OpenAI **Responses API** streaming events (`response.created`, `.output_text.delta`, `.completed`, etc.).
> - ChatKit expects **Responses‑style** events and will render them directly.
>
> These behaviors align with the official docs and changelog notes.

The orchestration schema can be found at schemas\orchestration_ir.schema.json and examples of valid orchestration packages can be found in schemas\examples. Use the examples to ensure that the LangGraph app compiles correctly.

## Purpose
- Provide a hardened, reusable HTTP client for our LLM calls with **Apigee OAuth + headers**.
- Deliver **real** LLM responses for the codeless agent (both sync and streaming).
- Emit proper **Responses** events so the front‑end (ChatKit) just works.

## Scope
- New **Apigee OAuth** client with in‑memory token cache.
- New **OpenAI‑like provider** that supports `/v1/responses`:
  - `create_response()` (sync) and `stream_response()` (async iterator).
- Update **codeless agent runtime** to invoke the provider (replace stub).
- Telemetry envelopes: `telemetry.llm.request/response` with optional redaction.
- Basic **structured output** (JSON schema + `json_mode` with retry/repair up to `maxRepairAttempts`).

## Goals
- Minimal dependencies; use `httpx` + stdlib only.
- Async by default; timeouts and bounded concurrency.
- ≥80% test coverage on new modules.

---

## Files to Add / Modify

```
app/
├─ clients/
│  ├─ __init__.py
│  ├─ apigee_oauth.py              # NEW: token fetch + cache
│  └─ http.py                      # NEW/MOD: AsyncClient factory w/ header injection
├─ providers/openai_like/
│  ├─ __init__.py
│  ├─ responses.py                 # NEW: create_response, stream_response
│  └─ sse.py                       # NEW: tiny SSE parser -> event dicts
├─ runtime/agents/
│  ├─ __init__.py
│  └─ codeless.py                  # NEW: assemble prompts; call provider; structured output
├─ compiler/nodes/agent_codeless.py # MOD: use runtime agent instead of stub
├─ telemetry/
│  ├─ __init__.py
│  ├─ redact.py                    # NEW: simple PII redaction helpers
│  └─ bus.py                       # NEW: lightweight publish/subscribe for telemetry
├─ config/settings.py              # MOD: add APIGEE/OPENAI config, timeouts, retry counts
tests/
├─ providers/test_apigee_oauth.py
├─ providers/test_openai_like_stream.py
├─ runtime/test_codeless_agent_llm.py
└─ e2e/test_execute_stream_end_to_end.py
docs/
└─ LLM_PROVIDER.md                 # NEW: adapter behavior, headers, examples
.env.example                       # MOD: add Apigee + base URL vars
```

### `.env.example` (append)
```
OPENAI_BASE_URL=https://apigee.yourdomain.example/openai   # OpenAI-compatible gateway
APIGEE_TOKEN_URL=https://apigee.yourdomain.example/oauth2/token
APIGEE_CLIENT_ID=your-client-id
APIGEE_CLIENT_SECRET=your-client-secret
APIGEE_AUDIENCE=your-apigee-audience
HTTP_TIMEOUT_SECONDS=30
HTTP_MAX_RETRIES=2
HTTP_RETRY_BACKOFF_MS=250
```

### `app/config/settings.py` (extend)
```python
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    # ...existing...
    openai_base_url: str = "http://localhost:8000"
    apigee_token_url: str | None = None
    apigee_client_id: str | None = None
    apigee_client_secret: str | None = None
    apigee_audience: str | None = None
    http_timeout_seconds: int = 30
    http_max_retries: int = 2
    http_retry_backoff_ms: int = 250

    model_config = SettingsConfigDict(env_file=".env", env_prefix="", extra="ignore")

settings = Settings()
```

### `app/clients/apigee_oauth.py`
```python
from __future__ import annotations
import time, asyncio, httpx
from .types import OAuthToken

class TokenCache:
    def __init__(self):
        self._lock = asyncio.Lock()
        self._entry: tuple[str, float] | None = None  # (token, expires_at)

    async def get(self) -> str | None:
        async with self._lock:
            if self._entry and self._entry[1] - 30 > time.time():  # 30s safety
                return self._entry[0]
            return None

    async def set(self, token: str, expires_in: int) -> None:
        async with self._lock:
            self._entry = (token, time.time() + expires_in)

_cache = TokenCache()

async def get_bearer_token(token_url: str, client_id: str, client_secret: str, audience: str | None = None) -> str:
    cached = await _cache.get()
    if cached:
        return cached
    data = {"grant_type": "client_credentials", "client_id": client_id, "client_secret": client_secret}
    if audience:
        data["audience"] = audience
    async with httpx.AsyncClient(timeout=15) as ac:
        r = await ac.post(token_url, data=data)
        r.raise_for_status()
        payload = r.json()
    token = payload["access_token"]
    expires_in = int(payload.get("expires_in", 3600))
    await _cache.set(token, expires_in)
    return token
```

### `app/clients/http.py`
```python
from __future__ import annotations
import uuid, datetime as dt, httpx, asyncio
from typing import Mapping
from app.config.settings import settings
from .apigee_oauth import get_bearer_token

def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()

async def openai_like_client(base_headers: Mapping[str, str] | None = None) -> httpx.AsyncClient:
    base_url = settings.openai_base_url.rstrip("/")
    headers = dict(base_headers or {})
    # Acquire OAuth if configured
    if settings.apigee_token_url and settings.apigee_client_id and settings.apigee_client_secret:
        token = await get_bearer_token(
            settings.apigee_token_url,
            settings.apigee_client_id,
            settings.apigee_client_secret,
            settings.apigee_audience,
        )
        headers.setdefault("Authorization", f"Bearer {token}")
    # Defaults
    headers.setdefault("X-Timestamp", _now_iso())
    # Caller should pass X-Tenant-Id, X-Correlation-Id, X-Request-Id, X-Client-Id
    return httpx.AsyncClient(
        base_url=base_url,
        headers=headers,
        timeout=httpx.Timeout(settings.http_timeout_seconds),
        http2=True,
        limits=httpx.Limits(max_keepalive_connections=50, max_connections=100),
    )
```

### `app/providers/openai_like/sse.py`
```python
from __future__ import annotations
import json
from typing import AsyncIterator

async def iter_sse_lines(resp) -> AsyncIterator[dict]:
    async for raw in resp.aiter_lines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("event:"):
            event = line.split(":", 1)[1].strip()
            # next line should be data
            data_line = await resp.aiter_lines().__anext__()
            assert data_line.startswith("data:")
            data = data_line.split(":", 1)[1].strip()
            try:
                payload = json.loads(data)
            except Exception:
                payload = {"raw": data}
            payload["type"] = event if "type" not in payload else payload["type"]
            yield payload
```

### `app/providers/openai_like/responses.py`
```python
from __future__ import annotations
import httpx, asyncio, json
from typing import AsyncIterator, Dict, Any, List
from app.clients.http import openai_like_client
from app.telemetry.bus import publish_telemetry

async def create_response(messages: List[Dict[str, Any]], model: str, extra_headers: Dict[str, str] | None = None, response_format: Dict[str, Any] | None = None) -> Dict[str, Any]:
    headers = extra_headers or {}
    async with await openai_like_client(headers) as ac:
        body = {"model": model, "input": [{"role": m["role"], "content": m["content"]} for m in messages]}
        if response_format:
            body["response_format"] = response_format
        publish_telemetry({"type":"telemetry.llm.request","payload":{"url":"/v1/responses","body":{**body,"input":"<omitted>"}}})
        r = await ac.post("/v1/responses", json=body)
        r.raise_for_status()
        out = r.json()
        publish_telemetry({"type":"telemetry.llm.response","payload":{"status":r.status_code}})
        return out

async def stream_response(messages: List[Dict[str, Any]], model: str, extra_headers: Dict[str, str] | None = None, response_format: Dict[str, Any] | None = None) -> AsyncIterator[Dict[str, Any]]:
    headers = dict(extra_headers or {})
    headers["Accept"] = "text/event-stream"
    headers["Content-Type"] = "application/json"
    async with await openai_like_client(headers) as ac:
        body = {"model": model, "input": [{"role": m["role"], "content": m["content"]} for m in messages], "stream": True}
        if response_format:
            body["response_format"] = response_format
        publish_telemetry({"type":"telemetry.llm.request","payload":{"url":"/v1/responses","stream":True}})
        async with ac.stream("POST", "/v1/responses", json=body) as resp:
            resp.raise_for_status()
            async for evt in resp.aiter_lines():
                if not evt:
                    continue
                if evt.startswith("event:"):
                    event = evt.split(":",1)[1].strip()
                    data_line = await resp.aiter_lines().__anext__()
                    data = data_line.split(":",1)[1].strip()
                    try:
                        payload = json.loads(data)
                    except Exception:
                        payload = {"raw": data}
                    payload["type"] = event if "type" not in payload else payload["type"]
                    yield payload
```

### `app/telemetry/bus.py`
```python
from __future__ import annotations
import asyncio
from typing import AsyncIterator, Dict, Any

_channels: dict[str, "asyncio.Queue[dict]"] = {}

def _chan(run_id: str) -> "asyncio.Queue[dict]":
    q = _channels.get(run_id)
    if q is None:
        q = asyncio.Queue()
        _channels[run_id] = q
    return q

def publish_telemetry(evt: Dict[str, Any], run_id: str | None = None) -> None:
    if not run_id:
        run_id = "__default__"
    q = _chan(run_id)
    try:
        q.put_nowait(evt)
    except Exception:
        pass

async def subscribe(run_id: str) -> AsyncIterator[dict]:
    q = _chan(run_id)
    while True:
        item = await q.get()
        yield item
```

### `app/runtime/agents/codeless.py`
```python
from __future__ import annotations
from typing import Dict, Any, List
from app.providers.openai_like.responses import create_response, stream_response
from app.telemetry.bus import publish_telemetry

def _assemble_messages(state: Dict[str, Any], sys: str, history_n: int | None = None, user_input: str | None = None) -> List[Dict[str, Any]]:
    msgs: List[Dict[str, Any]] = []
    if sys:
        msgs.append({"role":"system","content":sys})
    history = list(state.get("messages") or [])
    if history_n is not None and history_n >= 0:
        history = history[-history_n:]
    msgs.extend(history)
    if user_input:
        msgs.append({"role":"user","content": user_input})
    return msgs

async def invoke_llm(state: Dict[str, Any], agent_node: Dict[str, Any], stream: bool = False, context_headers: Dict[str, str] | None = None):
    data = agent_node.get("data") or {}
    model = data.get("model", {}).get("modelId") or "gpt-4o"
    sys = data.get("systemInstructions") or ""
    hw = (data.get("context") or {}).get("historyWindow") or {}
    history_n = hw.get("n") if hw.get("mode") == "LastN" else None

    messages = _assemble_messages(state, sys, history_n=history_n)
    resp_format = None
    so = data.get("structuredOutput") or {}
    if so.get("enabled") and so.get("schema"):
        resp_format = {"type": "json_schema", "json_schema": {"name": "agent_output", "schema": so["schema"]}}

    if not stream:
        out = await create_response(messages, model=model, extra_headers=context_headers, response_format=resp_format)
        # Extract final text for appending to state.messages
        text = None
        try:
            # Responses API: output_text in response.output_text
            text = out.get("output_text") or out.get("content") or None
        except Exception:
            pass
        if text:
            msgs = list(state.get("messages") or [])
            msgs.append({"role":"assistant","content": text})
            return {**state, "messages": msgs}
        return state

    async def gen():
        async for evt in stream_response(messages, model=model, extra_headers=context_headers, response_format=resp_format):
            yield evt
    return gen
```

### `app/compiler/nodes/agent_codeless.py` (replace stub)
```python
from __future__ import annotations
from typing import Any
from langgraph.graph import StateGraph
from ..types import OrchestratorState
from app.runtime.agents.codeless import invoke_llm

def add_agent_node(g: StateGraph, node_id: str, agent_data: dict, plan: dict) -> None:
    async def agent_fn(state: OrchestratorState) -> OrchestratorState:
        # Non-streaming path used by app.invoke(); streaming handled in engine run_stream
        new_state = await invoke_llm(state, {"id": node_id, "data": agent_data}, stream=False, context_headers=plan.get("headers") or {})
        return new_state  # type: ignore
    g.add_node(node_id, agent_fn)
```

---

## Tests

### `tests/providers/test_apigee_oauth.py`
```python
import pytest, httpx, respx, asyncio
from app.clients.apigee_oauth import get_bearer_token

@respx.mock
@pytest.mark.asyncio
async def test_token_is_cached_and_reused():
    route = respx.post("https://apigee.example/oauth2/token").mock(
        return_value=httpx.Response(200, json={"access_token":"t1","expires_in":3600})
    )
    t1 = await get_bearer_token("https://apigee.example/oauth2/token","id","secret","aud")
    t2 = await get_bearer_token("https://apigee.example/oauth2/token","id","secret","aud")
    assert t1 == "t1" and t2 == "t1"
    assert route.called
    # only one HTTP call thanks to cache
    assert route.call_count == 1
```

### `tests/providers/test_openai_like_stream.py`
```python
import pytest, httpx, respx, json, asyncio
from app.providers.openai_like.responses import stream_response

@respx.mock
@pytest.mark.asyncio
async def test_stream_parses_responses_events():
    def sse_bytes():
        yield b"event: response.created\n"
        yield b"data: {\"id\":\"resp_1\"}\n\n"
        yield b"event: response.output_text.delta\n"
        yield b"data: {\"delta\":\"He\"}\n\n"
        yield b"event: response.output_text.delta\n"
        yield b"data: {\"delta\":\"llo\"}\n\n"
        yield b"event: response.completed\n"
        yield b"data: {\"status\":\"completed\"}\n\n"

    respx.post("https://apigee.yourdomain.example/openai/v1/responses").mock(
        return_value=httpx.Response(200, content=b"".join(sse_bytes()), headers={"Content-Type":"text/event-stream"})
    )
    msgs = [{"role":"user","content":"hi"}]
    chunks = []
    async for evt in stream_response(msgs, model="gpt-4o", extra_headers={"X-Tenant-Id":"t"}):
        chunks.append(evt["type"])
    assert "response.created" in chunks and "response.completed" in chunks
```

### `tests/runtime/test_codeless_agent_llm.py`
```python
import pytest, httpx, respx
from app.runtime.agents.codeless import invoke_llm

@respx.mock
@pytest.mark.asyncio
async def test_invoke_llm_appends_assistant_message():
    respx.post("https://apigee.yourdomain.example/openai/v1/responses").mock(
        return_value=httpx.Response(200, json={"output_text":"Hello there"})
    )
    state = {"messages":[{"role":"user","content":"hi"}]}
    agent_node = {"data":{"systemInstructions":"Be brief","model":{"modelId":"gpt-4o"}, "context":{"historyWindow":{"mode":"LastN","n":10}}}}
    out = await invoke_llm(state, agent_node, stream=False, context_headers={"X-Tenant-Id":"t"})
    assert any(m.get("role")=="assistant" for m in out["messages"])
```

### `tests/e2e/test_execute_stream_end_to_end.py`
```python
# smoke test that the StreamingResponse endpoint emits Responses-style events
# (reuse PR-08 endpoint tests; extend if needed)
```

---

## Step‑By‑Step Implementation

1. **Settings & .env**: Add Apigee and base URL settings; wire defaults.
2. **OAuth client**: Implement client‑credentials flow with in‑memory cache; safety‑margin on expiry.
3. **HTTP client**: Factory returns `httpx.AsyncClient` with Authorization + default headers (timestamp; caller provides tenant/correlation/request/client IDs).
4. **Provider adapter**: Implement `create_response()` and `stream_response()` for `/v1/responses`; add minimal SSE parser (event + data).
5. **Codeless agent**: Assemble messages (system + truncated history + user), optional `response_format` for JSON schema; call provider (sync/stream). Append assistant text in non‑streaming path.
6. **Compiler node**: Replace stub agent node to call the runtime agent (non‑streaming). Streaming is surfaced via the engine (PR‑08).
7. **Telemetry**: Publish `telemetry.llm.request/response`; honor future redaction flags.
8. **Tests**: Token caching, SSE parsing, agent integration; e2e smoke for streaming endpoint.
9. **Docs**: Add `docs/LLM_PROVIDER.md` with usage and examples.

---

## Acceptance Criteria
- [ ] `openai_like_client` acquires & caches Apigee OAuth token and injects default headers.
- [ ] `create_response()` returns JSON for non‑streaming; `stream_response()` yields **Responses API** event dicts (`response.*`).
- [ ] Codeless agent uses the provider and appends assistant output in non‑streaming execution.
- [ ] SSE stream from `/v1/execute/stream` passes through `response.created`, `response.output_text.delta`, and `response.completed` when provider streams.
- [ ] Telemetry events are published for request/response boundaries (with future redaction hooks).
- [ ] Tests pass with ≥80% coverage on new modules.

---

## Validation
```bash
# unit
uv run pytest -q --cov=app --cov-report=term-missing

# manual smoke: start server, then
curl -N -s -X POST http://localhost:8000/v1/execute/stream \
  -H "Content-Type: application/json" \
  -H "X-Tenant-Id: tenant-123" -H "X-Telemetry: basic" \
  -d @sample_ir_and_input.json | head -n 40
```

---

## Manual Pre/Post Items
- Ensure `.env` contains valid Apigee credentials and `OPENAI_BASE_URL` to your gateway.
- Confirm PR‑08 endpoints are merged (we reuse those routes).
- If you use a corporate proxy or mTLS, add those hooks in `app/clients/http.py`.

---

## PR Tenets (Append to all PRs)

- Ensure code is **clean, readable, and maintainable**; prefer clarity over cleverness.  
- Favor **async I/O** and efficient concurrency where it improves real latency.  
- Keep dependencies **minimal** and version‑pinned where stability matters.  
- Design for **extensibility**: new orchestration nodes and providers plug in without major refactors.  
- Provide **great tests**: fast, isolated, and meaningful (≥80% coverage).  
- Log **structured** events with correlation IDs; never log secrets; redact PII when enabled.  
- Fail **gracefully** with actionable error messages and consistent error envelopes.  
- Keep **OpenAPI docs** accurate and example‑backed; debugging starts with docs.  
- Make **configuration explicit** (.env first; injectable for tests).  
- Align with **OpenAI Responses streaming** semantics for maximum UI compatibility.
