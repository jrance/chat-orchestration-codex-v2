"""Async HTTP client for OpenAI-compatible calls via Apigee."""

from __future__ import annotations

import asyncio
import json
import logging
import random
from typing import Any, AsyncIterator, Mapping, Optional, Sequence

import httpx

from app.config.settings import settings
from app.http.client_factory import create_gateway_client
from app.http.headers import build_default_headers
from app.http.token_provider import ApigeeTokenProvider
from app.providers.openai_like.payloads import (
    Turn,
    build_chat_messages,
    build_responses_body,
    normalize_turns,
)

RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

_client: httpx.AsyncClient | None = None

logger = logging.getLogger(__name__)

_RESPONSES_STYLE = "responses"
_CHAT_STYLE = "chat"
_RESPONSES_ENDPOINT = "/v1/responses"
_CHAT_ENDPOINT = "/v1/chat/completions"


def _configured_style() -> str:
    style = str(settings.openai_api_style or _RESPONSES_STYLE).strip().lower()
    if style == _CHAT_STYLE:
        return _CHAT_STYLE
    return _RESPONSES_STYLE


def _endpoint_for_style(style: str) -> str:
    return _CHAT_ENDPOINT if style == _CHAT_STYLE else _RESPONSES_ENDPOINT


def _turns_from_body(body: Mapping[str, Any]) -> Sequence[Turn]:
    if "input" in body:
        turns = normalize_turns(body.get("input"))
        if turns:
            return turns
    if "messages" in body:
        turns = normalize_turns(body.get("messages"))
        if turns:
            return turns
    return []


def _decompose_body(body: Mapping[str, Any]) -> tuple[str, bool, Sequence[Turn], dict[str, Any]]:
    model_raw = body.get("model")
    model = str(model_raw) if model_raw is not None else ""
    stream_value = body.get("stream")
    stream = bool(stream_value) if stream_value is not None else False
    params = {
        key: value
        for key, value in body.items()
        if key not in {"model", "input", "messages", "stream"}
    }
    turns = _turns_from_body(body)
    return model, stream, turns, params


def _build_payload(body: Mapping[str, Any], *, style: str) -> dict[str, Any]:
    model, stream, turns, params = _decompose_body(body)
    params_copy = dict(params)
    if style == _CHAT_STYLE:
        return build_chat_messages(turns, model, stream, **params_copy)
    return build_responses_body(turns, model, stream, **params_copy)


def _error_message_from_body(data: Any, *, default: str = "") -> str:
    if isinstance(data, Mapping):
        error_block = data.get("error")
        if isinstance(error_block, Mapping):
            message = error_block.get("message")
            if isinstance(message, str):
                return message
        message = data.get("message")
        if isinstance(message, str):
            return message
    if isinstance(data, str):
        return data
    return default


def _extract_error_message(response: httpx.Response) -> str:
    try:
        data = response.json()
    except (json.JSONDecodeError, ValueError):
        return response.text.strip()
    return _error_message_from_body(data, default=response.text.strip())


async def _extract_stream_error(response: httpx.Response) -> str:
    try:
        body_bytes = await response.aread()
    except Exception:
        return ""
    text = body_bytes.decode("utf-8", errors="ignore")
    if not text:
        return ""
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return text.strip()
    return _error_message_from_body(data, default=text.strip())


def _error_hint(message: str) -> str | None:
    if not message:
        return None
    lowered = message.lower()
    if "messages" in lowered:
        return "Gateway expects Chat Completions payload; consider OPENAI_API_STYLE=chat."
    if "input" in lowered:
        return "Gateway expects Responses payload; consider OPENAI_API_STYLE=responses."
    if "unrecognized" in lowered and "input" in lowered:
        return "Gateway rejected Responses fields; consider enabling chat fallback."
    return None


def _log_payload_debug(status: int, path: str, payload: Mapping[str, Any], message: str) -> None:
    hint = _error_hint(message)
    log_parts = {
        "status": status,
        "path": path,
        "payload_keys": sorted(payload.keys()),
    }
    if hint:
        log_parts["hint"] = hint
    logger.debug("OpenAI request failed: %s message=%s", log_parts, message or "<no-message>")


def _should_retry_as_chat(style: str, status_code: int, message: str) -> bool:
    if style != _RESPONSES_STYLE:
        return False
    if status_code != 400:
        return False
    if not settings.openai_responses_fallback_to_chat:
        return False
    lowered = message.lower()
    return "messages" in lowered or "input" in lowered or "required parameter" in lowered


def _extract_text_from_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, Sequence):
        parts: list[str] = []
        for entry in content:
            if not isinstance(entry, Mapping):
                continue
            text = entry.get("text") or entry.get("content") or entry.get("value")
            if isinstance(text, str):
                parts.append(text)
        return "".join(parts)
    return ""


def _chat_response_to_responses(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        return {"output_text": "", "raw": payload}

    choices = payload.get("choices")
    text_segments: list[str] = []
    tool_calls: list[Any] = []
    if isinstance(choices, Sequence):
        for choice in choices:
            if not isinstance(choice, Mapping):
                continue
            message = choice.get("message")
            if isinstance(message, Mapping):
                text = _extract_text_from_content(message.get("content"))
                if text:
                    text_segments.append(text)
                tool_data = message.get("tool_calls")
                if isinstance(tool_data, Sequence):
                    tool_calls.extend(tool_data)
            delta = choice.get("delta")
            if isinstance(delta, Mapping):
                text = _extract_text_from_content(delta.get("content"))
                if text:
                    text_segments.append(text)
                tool_data = delta.get("tool_calls")
                if isinstance(tool_data, Sequence):
                    tool_calls.extend(tool_data)

    usage_block = payload.get("usage")
    normalized: dict[str, Any] = {
        "id": payload.get("id"),
        "model": payload.get("model"),
        "output_text": "".join(text_segments),
    }
    if isinstance(usage_block, Mapping):
        normalized["usage"] = dict(usage_block)
    if tool_calls:
        normalized["tool_calls"] = list(tool_calls)
    if isinstance(choices, Sequence):
        normalized["choices"] = list(choices)
    if normalized["output_text"]:
        normalized["output"] = [
            {
                "role": "assistant",
                "content": [{"type": "output_text", "text": normalized["output_text"]}],
            }
        ]
    normalized["raw"] = payload
    return normalized


async def _chat_stream_as_responses(response: httpx.Response) -> AsyncIterator[str]:
    yield "event: response.created"
    yield 'data: {"status":"in_progress"}'
    yield ""

    final_segments: list[str] = []
    usage: dict[str, Any] = {}
    response_id: str | None = None
    model_name: str | None = None
    tool_states: dict[str, dict[str, Any]] = {}

    async for raw_line in response.aiter_lines():
        line = raw_line.strip()
        if not line:
            continue
        if not line.startswith("data:"):
            continue

        payload = line[5:].strip()
        if not payload:
            continue
        if payload == "[DONE]":
            break

        try:
            chunk = json.loads(payload)
        except json.JSONDecodeError:
            continue

        if isinstance(chunk, Mapping):
            response_id = str(chunk.get("id") or response_id or "") or response_id
            model_name = str(chunk.get("model") or model_name or "") or model_name
            usage_block = chunk.get("usage")
            if isinstance(usage_block, Mapping):
                usage = dict(usage_block)

            choices = chunk.get("choices")
            if not isinstance(choices, Sequence):
                continue
            for choice in choices:
                if not isinstance(choice, Mapping):
                    continue
                delta = choice.get("delta")
                if isinstance(delta, Mapping):
                    text_piece = _extract_text_from_content(delta.get("content"))
                    if text_piece:
                        final_segments.append(text_piece)
                        yield "event: response.output_text.delta"
                        yield f'data: {json.dumps({"delta": text_piece})}'
                        yield ""

                    tool_delta = delta.get("tool_calls")
                    if isinstance(tool_delta, Sequence):
                        for entry in tool_delta:
                            if not isinstance(entry, Mapping):
                                continue
                            tool_id = str(entry.get("id") or "")
                            function_block = entry.get("function")
                            if not isinstance(function_block, Mapping):
                                continue
                            name = str(function_block.get("name") or "")
                            arguments = function_block.get("arguments")
                            state = tool_states.setdefault(
                                tool_id or name or f"tool_{len(tool_states)+1}",
                                {"name": name, "chunks": [], "id": tool_id or None},
                            )
                            state["name"] = name or state["name"]
                            if isinstance(arguments, str):
                                state["chunks"].append(arguments)
                                payload_obj = {
                                    "tool_call_id": state["id"],
                                    "name": state["name"],
                                    "arguments": arguments,
                                }
                                yield "event: response.function_call_arguments.delta"
                                yield f"data: {json.dumps(payload_obj)}"
                                yield ""
                            elif isinstance(arguments, Mapping):
                                serialized = json.dumps(arguments)
                                state["chunks"].append(serialized)
                                payload_obj = {
                                    "tool_call_id": state["id"],
                                    "name": state["name"],
                                    "arguments": serialized,
                                }
                                yield "event: response.function_call_arguments.delta"
                                yield f"data: {json.dumps(payload_obj)}"
                                yield ""

                finish_reason = choice.get("finish_reason")
                if finish_reason == "tool_calls":
                    continue

    for entry in tool_states.values():
        payload_obj = {
            "tool_call_id": entry.get("id"),
            "name": entry.get("name"),
        }
        arguments_text = "".join(entry.get("chunks") or [])
        if arguments_text:
            payload_obj["arguments"] = arguments_text
        yield "event: response.function_call_arguments.done"
        yield f"data: {json.dumps(payload_obj)}"
        yield ""

    final_text = "".join(final_segments)
    if final_text:
        yield "event: response.output_text.done"
        yield "data: {}"
        yield ""

    completed_payload: dict[str, Any] = {
        "type": "response.completed",
        "output_text": final_text,
        "usage": usage,
    }
    response_meta: dict[str, Any] = {}
    if response_id:
        response_meta["id"] = response_id
    if model_name:
        response_meta["model"] = model_name
    if response_meta:
        completed_payload["response"] = response_meta
    if tool_states:
        completed_payload["tool_calls"] = [
            {
                "id": entry.get("id"),
                "name": entry.get("name"),
                "arguments": "".join(entry.get("chunks") or []),
            }
            for entry in tool_states.values()
        ]

    yield "event: response.completed"
    yield f"data: {json.dumps(completed_payload)}"
    yield ""


def _make_async_client(transport: httpx.BaseTransport | None = None) -> httpx.AsyncClient:
    return create_gateway_client(transport=transport)


def get_client(transport: httpx.BaseTransport | None = None) -> httpx.AsyncClient:
    """Return the shared AsyncClient or create one with a custom transport."""
    global _client
    if transport is not None:
        return create_gateway_client(transport=transport)
    if _client is None:
        _client = create_gateway_client()
    return _client


async def _retry_delay(attempt: int) -> float:
    base = settings.http_retry_base_delay
    jitter = random.uniform(0, base)
    return base * (2 ** (attempt - 1)) + jitter


class OpenAICompatibleClient:
    """High-level client that injects auth, headers, and retry behavior."""

    def __init__(
        self,
        token_provider: ApigeeTokenProvider | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.token_provider = token_provider or ApigeeTokenProvider(transport=transport)
        self._transport = transport
        self._client: httpx.AsyncClient | None = None

    async def aclose(self) -> None:
        """Close any per-instance client created for custom transports."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _auth_headers(self) -> dict[str, str]:
        token = await self.token_provider.get_token()
        return {"Authorization": f"Bearer {token}"}

    def _get_client(self) -> httpx.AsyncClient:
        if self._transport is not None:
            if self._client is None:
                self._client = _make_async_client(transport=self._transport)
            return self._client
        return get_client()

    async def _prepare_headers(
        self,
        tenant_id: Optional[str],
        correlation_id: Optional[str],
        request_id: Optional[str],
        telemetry: Optional[str],
        extra_headers: dict[str, str] | None,
    ) -> dict[str, str]:
        headers = build_default_headers(tenant_id, correlation_id, request_id, telemetry)
        headers.update(await self._auth_headers())
        if extra_headers:
            headers.update(extra_headers)
        return headers

    async def request(
        self,
        method: str,
        path: str,
        json_body: dict[str, Any] | None = None,
        tenant_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        request_id: Optional[str] = None,
        telemetry: Optional[str] = None,
        extra_headers: dict[str, str] | None = None,
        max_attempts: Optional[int] = None,
    ) -> httpx.Response:
        """Send an HTTP request with retry/backoff for transient failures."""
        if not settings.openai_base_url:
            raise RuntimeError("OPENAI_BASE_URL not configured.")

        attempts = max(1, int(max_attempts or settings.http_retry_max_attempts))
        client = self._get_client()
        last_error: Exception | None = None

        for attempt in range(1, attempts + 1):
            try:
                headers = await self._prepare_headers(
                    tenant_id,
                    correlation_id,
                    request_id,
                    telemetry,
                    extra_headers,
                )
                response = await client.request(method, path, json=json_body, headers=headers)
                if response.status_code in RETRYABLE_STATUS_CODES and attempt < attempts:
                    await asyncio.sleep(await _retry_delay(attempt))
                    continue

                response.raise_for_status()
                return response
            except httpx.HTTPStatusError as exc:
                last_error = exc
                status_code = exc.response.status_code
                if status_code in RETRYABLE_STATUS_CODES and attempt < attempts:
                    await asyncio.sleep(await _retry_delay(attempt))
                    continue
                raise
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt >= attempts:
                    raise
                await asyncio.sleep(await _retry_delay(attempt))

        if last_error is not None:
            raise last_error
        raise RuntimeError("request failed without raising an exception")  # pragma: no cover

    async def post_responses(self, body: dict[str, Any], **headers: Any) -> dict[str, Any]:
        """Convenience helper for POST /responses."""
        style = _configured_style()
        payload = _build_payload(body, style=style)
        path = _endpoint_for_style(style)
        try:
            response = await self.request("POST", path, json_body=payload, **headers)
            data = response.json()
            return data if style == _RESPONSES_STYLE else _chat_response_to_responses(data)
        except httpx.HTTPStatusError as exc:
            message = _extract_error_message(exc.response)
            _log_payload_debug(exc.response.status_code, path, payload, message)
            if _should_retry_as_chat(style, exc.response.status_code, message):
                fallback_style = _CHAT_STYLE
                fallback_payload = _build_payload(body, style=fallback_style)
                fallback_path = _endpoint_for_style(fallback_style)
                logger.debug(
                    "Retrying OpenAI request against %s with chat payload after 400: %s",
                    fallback_path,
                    message,
                )
                response = await self.request("POST", fallback_path, json_body=fallback_payload, **headers)
                data = response.json()
                return _chat_response_to_responses(data)
            raise

    async def post_responses_stream(
        self,
        body: dict[str, Any],
        *,
        tenant_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        request_id: Optional[str] = None,
        telemetry: Optional[str] = None,
        extra_headers: dict[str, str] | None = None,
        max_attempts: Optional[int] = None,
    ) -> AsyncIterator[str]:
        """Stream the Responses endpoint as an async iterator of SSE lines."""
        if not settings.openai_base_url:
            raise RuntimeError("OPENAI_BASE_URL not configured.")

        attempts = max(1, int(max_attempts or settings.http_retry_max_attempts))
        client = self._get_client()
        style = _configured_style()
        fallback_used = False
        attempt = 1
        last_error: Exception | None = None

        while attempt <= attempts:
            headers = await self._prepare_headers(
                tenant_id,
                correlation_id,
                request_id,
                telemetry,
                extra_headers,
            )
            path = _endpoint_for_style(style)
            payload = _build_payload(body, style=style)

            try:
                async with client.stream("POST", path, json=payload, headers=headers) as response:
                    status = response.status_code
                    if status in RETRYABLE_STATUS_CODES and attempt < attempts:
                        await asyncio.sleep(await _retry_delay(attempt))
                        attempt += 1
                        continue

                    if status >= 400:
                        message = await _extract_stream_error(response)
                        _log_payload_debug(status, path, payload, message)
                        if _should_retry_as_chat(style, status, message) and not fallback_used:
                            style = _CHAT_STYLE
                            fallback_used = True
                            logger.debug(
                                "Retrying streaming OpenAI request against %s with chat payload after 400: %s",
                                _endpoint_for_style(style),
                                message,
                            )
                            continue
                        response.raise_for_status()

                    if style == _CHAT_STYLE:
                        async for line in _chat_stream_as_responses(response):
                            yield line
                    else:
                        async for line in response.aiter_lines():
                            yield line
                    return
            except httpx.HTTPStatusError as exc:
                last_error = exc
                status_code = exc.response.status_code
                if status_code in RETRYABLE_STATUS_CODES and attempt < attempts:
                    await asyncio.sleep(await _retry_delay(attempt))
                    attempt += 1
                    continue
                raise
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt >= attempts:
                    raise
                await asyncio.sleep(await _retry_delay(attempt))
                attempt += 1
                continue

            attempt += 1

        if last_error is not None:
            raise last_error
        raise RuntimeError("streaming request failed without raising an exception")  # pragma: no cover
