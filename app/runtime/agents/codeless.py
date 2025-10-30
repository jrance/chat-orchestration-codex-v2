"""Codeless agent runtime integration with the OpenAI-like provider."""

from __future__ import annotations

import copy
import json
import time
from dataclasses import dataclass
from typing import Any, AsyncIterator, Dict, List, Mapping, Optional, Tuple

from app.compiler.types import OrchestratorState
from app.history.window import window as window_messages
from app.providers.openai_like import create_response, stream_response
from app.runtime.context import RuntimeContext, get_runtime_context
from app.telemetry.models import TelemetryEvent
from app.telemetry.streamer import TelemetryStreamer


Messages = List[Dict[str, Any]]


@dataclass(slots=True)
class LLMResult:
    """Outcome of a synchronous LLM invocation."""

    state: OrchestratorState
    response: Dict[str, Any]
    output_text: str
    usage: Dict[str, Any]


def _runtime_context() -> Tuple[RuntimeContext | None, dict[str, str], Optional[TelemetryStreamer]]:
    ctx = get_runtime_context()
    if ctx is None:
        return None, {}, None
    return ctx, ctx.http_headers(), ctx.telemetry


def _coerce_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def _coerce_message(message: Mapping[str, Any]) -> Dict[str, Any]:
    role = str(message.get("role") or "user")
    content = message.get("content")
    if isinstance(content, list):
        return {"role": role, "content": content}
    return {"role": role, "content": _coerce_text(content)}


def _history_messages(state: OrchestratorState, history_cfg: Mapping[str, Any]) -> Messages:
    messages = list(state.get("messages") or [])
    mode = history_cfg.get("mode")
    n = history_cfg.get("n")
    duration_ms = history_cfg.get("durationMs")
    if mode:
        messages = window_messages(messages, mode, n=n, duration_ms=duration_ms)
    coerced = [_coerce_message(msg) for msg in messages]
    return coerced


def _structured_output_config(data: Mapping[str, Any]) -> Tuple[Optional[Dict[str, Any]], int]:
    so = data.get("structuredOutput") or {}
    if not so.get("enabled"):
        return None, 0
    schema = so.get("schema") or {}
    if not schema:
        return None, 0
    max_repairs = int(so.get("maxRepairAttempts") or 0)
    json_schema = {
        "name": str(so.get("schemaName") or "agent_output"),
        "schema": schema,
    }
    return {"type": "json_schema", "json_schema": json_schema}, max_repairs


def _model_parameters(model_cfg: Mapping[str, Any]) -> Dict[str, Any]:
    params: Dict[str, Any] = {}
    if "temperature" in model_cfg:
        params["temperature"] = model_cfg["temperature"]
    if "topP" in model_cfg:
        params["top_p"] = model_cfg["topP"]
    if model_cfg.get("maxTokens"):
        params["max_output_tokens"] = model_cfg["maxTokens"]
    if model_cfg.get("stop"):
        params["stop"] = model_cfg["stop"]
    if model_cfg.get("seed") is not None:
        params["seed"] = model_cfg["seed"]
    return params


def _assemble_messages(state: OrchestratorState, agent_data: Mapping[str, Any], prompt: str) -> Messages:
    history_cfg = (agent_data.get("context") or {}).get("historyWindow") or {}
    messages = _history_messages(state, history_cfg)
    assembled: Messages = []
    if prompt:
        assembled.append({"role": "system", "content": prompt})
    assembled.extend(messages)
    return assembled


def _build_request(
    state: OrchestratorState,
    agent_node: Mapping[str, Any],
    prompt: str,
) -> Tuple[Dict[str, Any], Messages, Dict[str, Any], int]:
    data = agent_node.get("data") or {}
    model_cfg = data.get("model") or {}
    messages = _assemble_messages(state, data, prompt)

    body: Dict[str, Any] = {
        "model": model_cfg.get("modelId") or "gpt-4o",
        "input": messages,
    }
    body.update(_model_parameters(model_cfg))

    response_format, max_repairs = _structured_output_config(data)
    if response_format:
        body["response_format"] = response_format
        body["json_mode"] = True

    return body, messages, response_format or {}, max_repairs


def _extract_output_text(response: Mapping[str, Any]) -> str:
    text = response.get("output_text")
    if isinstance(text, list):
        return "".join(_coerce_text(item) for item in text)
    if isinstance(text, str):
        return text

    outputs = response.get("output")
    if isinstance(outputs, list):
        for item in outputs:
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if isinstance(block, Mapping):
                    text_value = block.get("text") or block.get("value")
                    if isinstance(text_value, str):
                        return text_value
    return ""


def _extract_usage(response: Mapping[str, Any]) -> Dict[str, Any]:
    usage = response.get("usage")
    if isinstance(usage, Mapping):
        return dict(usage)
    return {}


def _append_assistant_message(
    state: OrchestratorState,
    agent_node: Mapping[str, Any],
    output_text: str,
) -> OrchestratorState:
    return _append_assistant_message_with_metadata(state, agent_node, output_text, {}, {})


def _append_assistant_message_with_metadata(
    state: OrchestratorState,
    agent_node: Mapping[str, Any],
    output_text: str,
    response: Mapping[str, Any],
    usage: Mapping[str, Any],
) -> OrchestratorState:
    new_state = copy.deepcopy(state)
    messages = list(new_state.get("messages") or [])
    if output_text:
        messages.append(
            {
                "role": "assistant",
                "content": output_text,
                "model": (agent_node.get("data") or {}).get("model", {}).get("modelId"),
                "ts": time.time(),
            }
        )
    new_state["messages"] = messages
    scratch = dict(new_state.get("scratch") or {})
    scratch.setdefault("agents", {})
    scratch["agents"][agent_node.get("id", "agent")] = {
        "last_output_text": output_text,
        "last_response": copy.deepcopy(dict(response)),
        "usage": dict(usage),
    }
    new_state["scratch"] = scratch
    return new_state


async def _invoke_with_repairs(
    body: Dict[str, Any],
    *,
    headers: Mapping[str, str],
    max_repairs: int,
    telemetry: Optional[TelemetryStreamer],
) -> Dict[str, Any]:
    attempt = 0
    while True:
        if telemetry:
            await telemetry.publish(
                TelemetryEvent(
                    event="telemetry.llm.request",
                    payload={"model": body.get("model"), "stream": False, "attempt": attempt + 1},
                )
            )
        response = await create_response(body, context_headers=headers)
        output_text = _extract_output_text(response)
        if not body.get("json_mode"):
            break
        try:
            if output_text:
                json.loads(output_text)
            break
        except json.JSONDecodeError:
            attempt += 1
            if attempt > max_repairs:
                break
    if telemetry:
        await telemetry.publish(
            TelemetryEvent(
                event="telemetry.llm.response",
                payload={
                    "model": body.get("model"),
                    "attempts": attempt + 1,
                    "hasOutput": bool(_extract_output_text(response)),
                },
            )
        )
    return response


async def invoke_llm(
    state: OrchestratorState,
    agent_node: Mapping[str, Any],
    prompt: str,
) -> LLMResult:
    """Invoke the provider synchronously and return the updated state."""

    runtime, headers, telemetry = _runtime_context()
    body, _messages, _response_format, max_repairs = _build_request(state, agent_node, prompt)

    response = await _invoke_with_repairs(body, headers=headers, max_repairs=max_repairs, telemetry=telemetry)
    output_text = _extract_output_text(response)
    usage = _extract_usage(response)
    new_state = _append_assistant_message_with_metadata(state, agent_node, output_text, response, usage)
    return LLMResult(state=new_state, response=response, output_text=output_text, usage=usage)


async def stream_codeless(
    state: OrchestratorState,
    agent_node: Mapping[str, Any],
    prompt: str,
) -> AsyncIterator[Dict[str, Any]]:
    """Stream provider events as an async iterator."""

    runtime, headers, telemetry = _runtime_context()
    body, _messages, _response_format, _max_repairs = _build_request(state, agent_node, prompt)
    body["stream"] = True

    if telemetry:
        await telemetry.publish(
            TelemetryEvent(
                event="telemetry.llm.request",
                payload={"model": body.get("model"), "stream": True},
            )
        )

    async for event in stream_response(body, context_headers=headers):
        yield event

    if telemetry:
        await telemetry.publish(
            TelemetryEvent(
                event="telemetry.llm.response",
                payload={"model": body.get("model"), "stream": True},
            )
        )


def build_codeless_runner(agent_node: Mapping[str, Any], prompt: str):
    """Return an async LangGraph node for the codeless agent."""

    async def _run(state: OrchestratorState) -> OrchestratorState:
        result = await invoke_llm(state, agent_node, prompt)
        return result.state

    return _run


def prepare_codeless_invocation(
    state: OrchestratorState,
    agent_node: Mapping[str, Any],
    prompt: str,
) -> Dict[str, Any]:
    """Expose the request payload for tests and telemetry."""

    body, messages, response_format, max_repairs = _build_request(state, agent_node, prompt)
    return {
        "body": body,
        "messages": messages,
        "response_format": response_format,
        "max_repairs": max_repairs,
    }


__all__ = ["LLMResult", "build_codeless_runner", "invoke_llm", "prepare_codeless_invocation", "stream_codeless"]
