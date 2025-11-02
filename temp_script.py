import asyncio
from app.api.deps import ExecutionContext, ExecutionHeaders
from app.runtime.engine import run_stream
from app.runtime import engine as engine_mod

async def main():
    original = engine_mod.stream_codeless
    async def _stream_stub(state, agent_node, prompt, **kwargs):
        yield {"type": "response.created", "status": "in_progress"}
        yield {"type": "response.output_text.delta", "delta": "stub "}
        yield {"type": "response.output_text.done"}
        yield {"type": "response.completed", "output_text": "stub response", "usage": {"output_tokens": 2}}
    engine_mod.stream_codeless = _stream_stub
    headers = ExecutionHeaders(tenant_id="tenant-1")
    context = ExecutionContext(headers=headers)
    events = []
    async for event in run_stream({
        "meta": {"id": "pkg", "name": "Test", "version": "1.0.0"},
        "nodes": [
            {
                "id": "agent",
                "kind": "agent.codeless",
                "label": "Agent",
                "data": {
                    "systemInstructions": "Be helpful",
                    "model": {"provider": "openai", "modelId": "gpt-4o"},
                    "context": {},
                    "tools": {"policy": "Disabled", "attached": []},
                },
            }
        ],
        "edges": [],
        "entryId": "agent",
    }, "hello", context, None):
        events.append(event.event)
    engine_mod.stream_codeless = original
    print(events)

asyncio.run(main())
