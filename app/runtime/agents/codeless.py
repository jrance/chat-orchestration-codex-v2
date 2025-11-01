"""Codeless agent runtime integration with the OpenAI-like provider."""

from __future__ import annotations

import asyncio
import copy
import json
import time
from dataclasses import dataclass, field
from io import StringIO
from typing import Any, AsyncIterator, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from app.api.models import ToolResultPayload
from app.compiler.types import OrchestratorState
from app.history.window import window as window_messages
from app.mcp import call_mcp_tool, register_server
from app.providers.openai_like import create_response, stream_response
from app.providers.openai_like.payloads import build_chat_messages, sanitize_tool_name
from app.runtime.context import RuntimeContext, get_runtime_context
from app.telemetry.models import TelemetryEvent
from app.telemetry.streamer import TelemetryStreamer
from app.tools import filter_schema_visible, invoke_tool, register_tool
from app.tools.policy import can_use_tools
from app.tools.types import ToolCall, ToolResult, ToolSpec


Messages = List[Dict[str, Any]]


@dataclass(slots=True)
class LLMResult:
    """Outcome of a synchronous LLM invocation."""

    state: OrchestratorState
    response: Dict[str, Any]
    output_text: str
    usage: Dict[str, Any]


@dataclass(slots=True)
class ToolRuntime:
    """Aggregated tool runtime configuration for an agent invocation."""

    specs: Dict[str, ToolSpec]
    payload: List[Dict[str, Any]]
    policy: str
    max_calls: Optional[int]
    timeout_ms: Optional[int]
    parallelism: int
    redact: bool
    enabled: bool
    mcp_servers: Mapping[str, Mapping[str, Any]]
    name_reverse: Dict[str, str] = field(default_factory=dict)
    sanitized_names: Dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class ToolCallPlanItem:
    """Normalized plan item for executing a tool call."""

    index: int
    call_id: str
    sanitized_name: str
    internal_name: str
    arguments: Dict[str, Any]
    arguments_json: str


@dataclass(slots=True)
class PendingFunctionCall:
    """Accumulator for a single streaming tool call."""

    index: int
    call_id: str | None = None
    sanitized_name: str | None = None
    internal_name: str | None = None
    arguments_buffer: StringIO = field(default_factory=StringIO)
    last_payload: Dict[str, Any] | None = None
    completed: bool = False

    def update(self, payload: Mapping[str, Any]) -> None:
        self.last_payload = dict(payload)
        event_type = str(payload.get("type") or "")
        is_done_event = event_type.endswith(".done")
        if is_done_event:
            self.completed = True
        call_id = payload.get("tool_call_id") or payload.get("id")
        if call_id:
            self.call_id = str(call_id)
        sanitized = payload.get("function_name") or payload.get("sanitized_name") or payload.get("name")
        if sanitized:
            self.sanitized_name = str(sanitized)
        internal = payload.get("tool_name") or payload.get("internal_name")
        if internal:
            self.internal_name = str(internal)
        arguments = payload.get("arguments")
        if isinstance(arguments, str) and arguments:
            if is_done_event:
                self.arguments_buffer = StringIO()
            self.arguments_buffer.write(arguments)
        elif isinstance(arguments, Mapping):
            try:
                serialized = json.dumps(arguments)
            except (TypeError, ValueError):
                serialized = ""
            if serialized:
                if is_done_event:
                    self.arguments_buffer = StringIO()
                self.arguments_buffer.write(serialized)

    def arguments_text(self) -> str:
        text = self.arguments_buffer.getvalue()
        if text:
            return text
        if self.last_payload is None:
            return ""
        arguments = self.last_payload.get("arguments")
        if isinstance(arguments, str):
            return arguments
        if isinstance(arguments, Mapping):
            try:
                return json.dumps(arguments)
            except (TypeError, ValueError):
                return ""
        return ""


@dataclass(slots=True)
class PendingToolCalls:
    """Track all streaming tool call deltas for a turn."""

    by_index: Dict[int, PendingFunctionCall] = field(default_factory=dict)
    by_call_id: Dict[str, PendingFunctionCall] = field(default_factory=dict)
    next_index: int = 0

    def _ensure(self, index_hint: int | None, call_id: str | None) -> PendingFunctionCall:
        candidate: PendingFunctionCall | None = None
        if index_hint is not None and index_hint in self.by_index:
            candidate = self.by_index[index_hint]
        if candidate is None and call_id and call_id in self.by_call_id:
            candidate = self.by_call_id[call_id]

        if candidate is None:
            idx = index_hint if index_hint is not None else self.next_index
            candidate = PendingFunctionCall(index=idx)
            if index_hint is None:
                self.next_index += 1
        else:
            if index_hint is not None:
                candidate.index = index_hint
        return candidate

    def upsert(self, payload: Mapping[str, Any]) -> PendingFunctionCall:
        index_val = payload.get("index")
        idx = int(index_val) if isinstance(index_val, int) else None
        call_id_raw = payload.get("tool_call_id") or payload.get("id")
        call_id = str(call_id_raw) if call_id_raw else None
        entry = self._ensure(idx, call_id)
        entry.update(payload)
        self.by_index[entry.index] = entry
        if call_id:
            self.by_call_id[call_id] = entry
        return entry

    def to_plan(self, runtime: ToolRuntime, *, limit: Optional[int] = None, turn_finished: bool = False) -> List[ToolCallPlanItem]:
        ordered_indices = sorted(self.by_index)
        if limit is not None:
            ordered_indices = ordered_indices[: max(0, limit)]
        plan: List[ToolCallPlanItem] = []
        for idx in ordered_indices:
            pending = self.by_index[idx]
            call_id = pending.call_id or f"tool_call_{idx}"
            sanitized = pending.sanitized_name or runtime.sanitized_names.get(pending.internal_name or "", "")
            internal = pending.internal_name or runtime.name_reverse.get(sanitized or "", "")
            if not internal and sanitized:
                internal = runtime.name_reverse.get(sanitized, sanitized)
            if not sanitized and internal:
                sanitized = runtime.sanitized_names.get(internal, sanitize_tool_name(internal))
            if not internal:
                internal = sanitized or f"tool_{idx}"
            if not sanitized:
                sanitized = sanitize_tool_name(internal)

            arguments_text = pending.arguments_text()
            ready_for_parse = turn_finished or pending.completed or _appears_complete_json(arguments_text)
            if arguments_text and not ready_for_parse:
                return []
            arguments = _parse_tool_arguments(arguments_text)
            if not arguments_text and arguments:
                try:
                    arguments_text = json.dumps(arguments)
                except (TypeError, ValueError):
                    arguments_text = "{}"

            plan.append(
                ToolCallPlanItem(
                    index=pending.index,
                    call_id=call_id,
                    sanitized_name=sanitized,
                    internal_name=internal,
                    arguments=arguments,
                    arguments_json=arguments_text,
                )
            )
        return plan

    def clear(self) -> None:
        self.by_index.clear()
        self.by_call_id.clear()
        self.next_index = 0

    def __bool__(self) -> bool:  # pragma: no cover - convenience
        return bool(self.by_index)


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


def _tool_payload_entry(spec: ToolSpec) -> Dict[str, Any]:
    return {
        "type": "function",
        "name": spec.get("id") or "",
        "description": spec.get("description") or spec.get("name") or "",
        "parameters": filter_schema_visible(spec),
    }


def _prepare_tool_runtime(
    agent_node: Mapping[str, Any],
    attached_tools: Sequence[ToolSpec] | None,
    mcp_servers: Mapping[str, Mapping[str, Any]] | None,
) -> ToolRuntime:
    data = agent_node.get("data") or {}
    tool_cfg = data.get("tools") or {}
    policy = str(tool_cfg.get("policy") or "Disabled")

    specs: Dict[str, ToolSpec] = {}
    payload: List[Dict[str, Any]] = []

    for server_id, config in (mcp_servers or {}).items():
        register_server(server_id, config)

    for spec in attached_tools or []:
        tool_id = spec.get("id")
        if not tool_id:
            continue
        register_tool(spec)
        specs[tool_id] = spec
        payload.append(_tool_payload_entry(spec))

    max_calls_raw = tool_cfg.get("maxCallsPerTurn")
    max_calls = int(max_calls_raw) if isinstance(max_calls_raw, int) and max_calls_raw > 0 else None

    timeout_raw = tool_cfg.get("timeoutMs")
    timeout_ms = int(timeout_raw) if isinstance(timeout_raw, int) and timeout_raw > 0 else None

    parallelism_raw = tool_cfg.get("parallelism")
    parallelism = int(parallelism_raw) if isinstance(parallelism_raw, int) and parallelism_raw > 0 else 1

    name_reverse: Dict[str, str] = {}
    sanitized_names: Dict[str, str] = {}
    if payload:
        preview = build_chat_messages([], model="", stream=False, tools=[dict(tool) for tool in payload])
        reverse_raw = preview.pop("_tool_name_reverse_map", {}) or {}
        name_reverse = {str(k): str(v) for k, v in reverse_raw.items()}
        sanitized_names = {str(v): str(k) for k, v in name_reverse.items()}

    runtime = ToolRuntime(
        specs=specs,
        payload=payload,
        policy=policy,
        max_calls=max_calls,
        timeout_ms=timeout_ms,
        parallelism=parallelism,
        redact=bool(tool_cfg.get("redactPII")),
        enabled=bool(payload) and can_use_tools(policy),
        mcp_servers=mcp_servers or {},
        name_reverse=name_reverse,
        sanitized_names=sanitized_names,
    )
    return runtime


def _appears_complete_json(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    if not (stripped.startswith("{") and stripped.endswith("}")):
        return False
    depth = 0
    escaped = False
    in_string = False
    for ch in stripped:
        if escaped:
            escaped = False
            continue
        if ch == "\\":
            escaped = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0 and not in_string


def _parse_tool_arguments(raw: Any) -> Dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, Mapping):
        return dict(raw)
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return {}
        if isinstance(parsed, Mapping):
            return dict(parsed)
        return {}
    return {}


def _extract_tool_calls(response: Mapping[str, Any]) -> List[ToolCall]:
    calls: List[ToolCall] = []
    # Responses API emits tool calls within output[*].content[*]
    for item in response.get("output", []):
        content_items = item.get("content") or []
        for content in content_items:
            if not isinstance(content, Mapping):
                continue
            if str(content.get("type")) != "tool_call":
                continue
            name = str(content.get("name") or "")
            call_id = str(content.get("id") or name)
            arguments = _parse_tool_arguments(content.get("arguments"))
            calls.append({"id": call_id, "name": name, "arguments": arguments})
    # Some providers may emit a top-level collection
    for extra in response.get("tool_calls", []) or []:
        if not isinstance(extra, Mapping):
            continue
        name = str(extra.get("name") or "")
        call_id = str(extra.get("id") or name)
        arguments = _parse_tool_arguments(extra.get("arguments"))
        calls.append({"id": call_id, "name": name, "arguments": arguments})
    return calls


def _tool_timeout_ms(call: ToolCall, runtime: ToolRuntime) -> Optional[int]:
    spec = runtime.specs.get(call.get("name") or "")
    if runtime.timeout_ms is not None:
        return runtime.timeout_ms
    if spec is None:
        return None
    timeout = spec.get("timeout_ms")
    if isinstance(timeout, int) and timeout > 0:
        return timeout
    return None


def _tool_result_payload(call: ToolCall, output: Dict[str, Any] | str | Any) -> ToolResult:
    normalized_output: Dict[str, Any] | str
    if isinstance(output, (dict, str)):
        normalized_output = output
    else:
        normalized_output = {"value": output}
    payload = ToolResultPayload(
        id=str(call.get("id") or ""),
        name=str(call.get("name") or ""),
        output=normalized_output,
    )
    return payload.model_dump()


async def _invoke_mcp_tool(tool_id: str, args: Dict[str, Any], runtime: ToolRuntime) -> Dict[str, Any]:
    remainder = tool_id.split(":", 1)[1] if ":" in tool_id else ""
    server_id, _, tool_name = remainder.partition("/")
    if not server_id:
        raise LookupError(f"Invalid MCP tool identifier '{tool_id}'")
    server_cfg = runtime.mcp_servers.get(server_id)
    if server_cfg is None:
        raise LookupError(f"MCP server '{server_id}' not registered")
    target_tool = tool_name or tool_id
    return await call_mcp_tool(server_cfg, target_tool, args)


async def _execute_tool_call(
    call: ToolCall,
    runtime: ToolRuntime,
    telemetry: Optional[TelemetryStreamer],
    *,
    index: int | None = None,
) -> ToolResult:
    tool_name = str(call.get("name") or "")
    raw_arguments = call.get("arguments") or {}
    if isinstance(raw_arguments, Mapping):
        args = dict(raw_arguments)
    else:
        try:
            args = dict(raw_arguments)
        except Exception:
            args = {}
    call_id = str(call.get("id") or "")
    args_shape = sorted(args.keys())

    base_payload: Dict[str, Any] = {
        "tool_call_id": call_id,
        "name": tool_name,
        "args_shape": args_shape,
    }
    if index is not None:
        base_payload["index"] = index

    if telemetry:
        request_payload: Dict[str, Any] = {"tool": tool_name, "callId": call.get("id")}
        if index is not None:
            request_payload["index"] = index
        await telemetry.publish(
            TelemetryEvent(
                event="telemetry.tool.request",
                payload=request_payload,
            )
        )
        await telemetry.publish(
            TelemetryEvent(
                event="response.tool_call.created",
                payload=dict(base_payload),
            )
        )
        running_payload = dict(base_payload)
        running_payload["status"] = "running"
        await telemetry.publish(
            TelemetryEvent(
                event="response.tool_call.delta",
                payload=running_payload,
            )
        )

    status = "ok"
    result_payload: Dict[str, Any] | str | Any
    try:
        if tool_name.startswith("mcp:"):
            result_payload = await _invoke_mcp_tool(tool_name, args, runtime)
        else:
            timeout_ms = _tool_timeout_ms(call, runtime)
            result_payload = await invoke_tool(tool_name, args, per_call_timeout_ms=timeout_ms)
    except Exception as exc:  # pragma: no cover - defensive
        status = "error"
        result_payload = {"error": str(exc)}

    result = _tool_result_payload(call, result_payload)

    if telemetry:
        def _result_count(payload: Any) -> int | None:
            if isinstance(payload, Mapping):
                results = payload.get("results")
                if isinstance(results, list):
                    return len(results)
                return 1 if payload else 0
            if isinstance(payload, list):
                return len(payload)
            if payload in (None, "", {}):
                return 0
            return 1

        result_count = _result_count(result_payload)
        created_payload: Dict[str, Any] = {
            "tool_call_id": call_id,
            "name": tool_name,
        }
        if index is not None:
            created_payload["index"] = index
        if result_count is not None:
            created_payload["result_count"] = result_count
        if runtime.redact:
            created_payload["redacted"] = True
        if status != "ok":
            created_payload["error"] = True
        await telemetry.publish(
            TelemetryEvent(
                event="response.tool_result.created",
                payload=created_payload,
            )
        )

        done_payload: Dict[str, Any] = {
            "tool_call_id": call_id,
            "name": tool_name,
        }
        if index is not None:
            done_payload["index"] = index
        if not runtime.redact:
            done_payload["output"] = result.get("output")
        if status != "ok":
            done_payload["error"] = True
        await telemetry.publish(
            TelemetryEvent(
                event="response.tool_result.done",
                payload=done_payload,
            )
        )

        completion_delta = dict(base_payload)
        completion_delta["status"] = "completed" if status == "ok" else "error"
        await telemetry.publish(
            TelemetryEvent(
                event="response.tool_call.delta",
                payload=completion_delta,
            )
        )
        done_call_payload = dict(base_payload)
        done_call_payload["status"] = completion_delta["status"]
        await telemetry.publish(
            TelemetryEvent(
                event="response.tool_call.done",
                payload=done_call_payload,
            )
        )
        await telemetry.publish(
            TelemetryEvent(
                event="telemetry.tool.response",
                payload={
                    "tool": tool_name,
                    "status": status,
                    **({"index": index} if index is not None else {}),
                },
            )
        )

    return result


async def _execute_tool_calls(
    calls: Sequence[ToolCall],
    runtime: ToolRuntime,
    telemetry: Optional[TelemetryStreamer],
) -> List[ToolResult]:
    if not calls:
        return []

    semaphore = asyncio.Semaphore(max(1, runtime.parallelism))
    results: List[ToolResult | None] = [None] * len(calls)

    async def _run(position: int, call: ToolCall) -> tuple[int, ToolResult]:
        call_index = call.get("__index")
        try:
            index_value = int(call_index)
        except (TypeError, ValueError):
            index_value = position
        call_payload = dict(call)
        call_payload.pop("__index", None)
        async with semaphore:
            result = await _execute_tool_call(call_payload, runtime, telemetry, index=index_value)
        return position, result

    tasks = [asyncio.create_task(_run(idx, call)) for idx, call in enumerate(calls)]
    for task in asyncio.as_completed(tasks):
        position, result = await task
        results[position] = result
    return [item for item in results if item is not None]


def _append_tool_messages(target: List[Dict[str, Any]], results: Iterable[ToolResult]) -> None:
    for result in results:
        serialized = result.get("output")
        if not isinstance(serialized, str):
            serialized = json.dumps(serialized, ensure_ascii=False)
        target.append(
            {
                "role": "tool",
                "tool_call_id": result.get("id"),
                "name": result.get("name"),
                "content": serialized,
            }
        )


def _build_request(
    state: OrchestratorState,
    agent_node: Mapping[str, Any],
    prompt: str,
    *,
    messages_override: Messages | None = None,
    tools_payload: Sequence[Mapping[str, Any]] | None = None,
    tool_policy: str | None = None,
) -> Tuple[Dict[str, Any], Messages, Dict[str, Any], int]:
    data = agent_node.get("data") or {}
    model_cfg = data.get("model") or {}
    messages = list(messages_override) if messages_override is not None else _assemble_messages(state, data, prompt)

    body: Dict[str, Any] = {
        "model": model_cfg.get("modelId") or "gpt-4o",
        "input": messages,
    }
    body.update(_model_parameters(model_cfg))

    response_format, max_repairs = _structured_output_config(data)
    if response_format:
        body["response_format"] = response_format
        body["json_mode"] = True
    if tools_payload:
        body["tools"] = [dict(tool) for tool in tools_payload]
        if tool_policy and str(tool_policy).strip().lower() == "auto":
            body["tool_choice"] = "auto"

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
    *,
    attached_tools: Sequence[ToolSpec] | None = None,
    mcp_servers: Mapping[str, Mapping[str, Any]] | None = None,
) -> LLMResult:
    """Invoke the provider synchronously and return the updated state."""

    runtime_ctx, headers, telemetry = _runtime_context()
    tool_runtime = _prepare_tool_runtime(agent_node, attached_tools, mcp_servers)

    data = agent_node.get("data") or {}
    conversation = _assemble_messages(state, data, prompt)

    body, _messages, _response_format, max_repairs = _build_request(
        state,
        agent_node,
        prompt,
        messages_override=conversation,
        tools_payload=tool_runtime.payload,
        tool_policy=tool_runtime.policy,
    )

    response = await _invoke_with_repairs(body, headers=headers, max_repairs=max_repairs, telemetry=telemetry)

    tool_results: List[ToolResult] = []
    calls_used = 0

    while tool_runtime.enabled:
        tool_calls = _extract_tool_calls(response)
        if not tool_calls:
            break

        if tool_runtime.max_calls is not None:
            remaining = tool_runtime.max_calls - calls_used
            if remaining <= 0:
                break
            tool_calls = list(tool_calls[:remaining])

        if not tool_calls:
            break

        executed = await _execute_tool_calls(tool_calls, tool_runtime, telemetry if runtime_ctx else None)
        if not executed:
            break

        tool_results.extend(executed)
        _append_tool_messages(conversation, executed)
        calls_used += len(executed)

        body, _messages, _response_format, _ = _build_request(
            state,
            agent_node,
            prompt,
            messages_override=conversation,
            tools_payload=tool_runtime.payload,
            tool_policy=tool_runtime.policy,
        )
        response = await _invoke_with_repairs(body, headers=headers, max_repairs=max_repairs, telemetry=telemetry)

    if tool_results:
        response["tool_results"] = tool_results

    output_text = _extract_output_text(response)
    usage = _extract_usage(response)
    state_for_update = state
    if tool_results:
        state_for_update = copy.deepcopy(state)
        state_messages = list(state_for_update.get("messages") or [])
        _append_tool_messages(state_messages, tool_results)
        state_for_update["messages"] = state_messages

    new_state = _append_assistant_message_with_metadata(state_for_update, agent_node, output_text, response, usage)
    return LLMResult(state=new_state, response=response, output_text=output_text, usage=usage)


async def stream_codeless(
    state: OrchestratorState,
    agent_node: Mapping[str, Any],
    prompt: str,
    *,
    attached_tools: Sequence[ToolSpec] | None = None,
    mcp_servers: Mapping[str, Mapping[str, Any]] | None = None,
) -> AsyncIterator[Dict[str, Any]]:
    """Stream provider events as an async iterator."""

    runtime, headers, telemetry = _runtime_context()
    tool_runtime = _prepare_tool_runtime(agent_node, attached_tools, mcp_servers)
    body, _messages, _response_format, _max_repairs = _build_request(
        state,
        agent_node,
        prompt,
        tools_payload=tool_runtime.payload,
        tool_policy=tool_runtime.policy,
    )
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


def build_codeless_runner(
    agent_node: Mapping[str, Any],
    prompt: str,
    *,
    attached_tools: Sequence[ToolSpec] | None = None,
    mcp_servers: Mapping[str, Mapping[str, Any]] | None = None,
):
    """Return an async LangGraph node for the codeless agent."""

    for server_id, config in (mcp_servers or {}).items():
        register_server(server_id, config)

    async def _run(state: OrchestratorState) -> OrchestratorState:
        result = await invoke_llm(
            state,
            agent_node,
            prompt,
            attached_tools=attached_tools,
            mcp_servers=mcp_servers,
        )
        return result.state

    return _run


def prepare_codeless_invocation(
    state: OrchestratorState,
    agent_node: Mapping[str, Any],
    prompt: str,
    *,
    attached_tools: Sequence[ToolSpec] | None = None,
    mcp_servers: Mapping[str, Mapping[str, Any]] | None = None,
) -> Dict[str, Any]:
    """Expose the request payload for tests and telemetry."""

    tool_runtime = _prepare_tool_runtime(agent_node, attached_tools, mcp_servers)
    body, messages, response_format, max_repairs = _build_request(
        state,
        agent_node,
        prompt,
        tools_payload=tool_runtime.payload,
        tool_policy=tool_runtime.policy,
    )
    return {
        "body": body,
        "messages": messages,
        "response_format": response_format,
        "max_repairs": max_repairs,
    }


__all__ = ["LLMResult", "build_codeless_runner", "invoke_llm", "prepare_codeless_invocation", "stream_codeless"]
