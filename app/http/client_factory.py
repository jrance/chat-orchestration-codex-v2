"""Factory helpers for shared HTTPX AsyncClient instances."""

from __future__ import annotations

import asyncio
import inspect
from contextlib import suppress
from functools import lru_cache
from typing import Any, Mapping

import httpx

from app.config.settings import settings

from .proxy import build_httpx_proxies, build_ssl_verify

_gateway_client: httpx.AsyncClient | None = None
_token_client: httpx.AsyncClient | None = None


@lru_cache(maxsize=1)
def _async_client_parameters() -> Mapping[str, inspect.Parameter]:
    try:
        return inspect.signature(httpx.AsyncClient.__init__).parameters
    except Exception:
        return {}


def _preferred_proxy_argument() -> str:
    params = _async_client_parameters()
    if "proxies" in params:
        return "proxies"
    if "proxy" in params:
        return "proxy"
    return "transport"


def _proxy_url_from_mapping(proxies: Mapping[str, str] | None) -> str | None:
    if not proxies:
        return None
    for key in ("all", "https", "http"):
        value = proxies.get(key)
        if value:
            return value
    return None


def _apply_proxy_kwargs(kwargs: dict[str, Any]) -> None:
    if kwargs.get("transport") is None:
        proxies = build_httpx_proxies()
        if proxies:
            url = _proxy_url_from_mapping(proxies)
            mode = _preferred_proxy_argument()
            if mode == "proxies":
                kwargs["proxies"] = proxies
            elif mode == "proxy" and url:
                kwargs["proxy"] = url
            elif mode == "transport" and url:
                try:
                    kwargs["transport"] = httpx.AsyncHTTPTransport(proxy=url)
                except Exception:
                    pass
    verify = build_ssl_verify()
    kwargs["verify"] = verify
    kwargs["trust_env"] = False


def _sync_close_async_client(client: httpx.AsyncClient | None) -> None:
    """Best-effort close for AsyncClient instances from sync contexts."""

    if client is None:
        return

    aclose = getattr(client, "aclose", None)
    if not callable(aclose):
        close = getattr(client, "close", None)
        if callable(close):
            with suppress(Exception):
                close()
        return

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        try:
            task = loop.create_task(aclose())
        except RuntimeError:
            task = None
        if task is None:
            with suppress(Exception):
                asyncio.run(aclose())
            return

        def _drain(fut: asyncio.Future[Any]) -> None:
            with suppress(Exception):
                fut.result()

        task.add_done_callback(_drain)
        return

    with suppress(Exception):
        asyncio.run(aclose())


def _gateway_client_kwargs(*, transport: httpx.BaseTransport | None = None) -> dict[str, Any]:
    timeout = httpx.Timeout(
        connect=settings.http_connect_timeout,
        read=settings.http_read_timeout,
        write=settings.http_write_timeout,
        pool=settings.http_read_timeout,
    )
    limits = httpx.Limits(
        max_connections=settings.http_pool_max_connections,
        max_keepalive_connections=settings.http_pool_max_keepalive,
    )
    kwargs: dict[str, Any] = {
        "base_url": settings.openai_base_url or "",
        "timeout": timeout,
        "limits": limits,
        "follow_redirects": True,
        "headers": {"Accept": "application/json"},
    }
    if transport is not None:
        kwargs["transport"] = transport
    _apply_proxy_kwargs(kwargs)
    return kwargs


def _token_client_kwargs(*, transport: httpx.BaseTransport | None = None) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "timeout": httpx.Timeout(15.0),
        "headers": {"Accept": "application/json"},
    }
    if transport is not None:
        kwargs["transport"] = transport
    _apply_proxy_kwargs(kwargs)
    return kwargs


def create_gateway_client(*, transport: httpx.BaseTransport | None = None) -> httpx.AsyncClient:
    """Return a newly constructed AsyncClient configured for gateway calls."""
    return httpx.AsyncClient(**_gateway_client_kwargs(transport=transport))


def create_token_client(*, transport: httpx.BaseTransport | None = None) -> httpx.AsyncClient:
    """Return a newly constructed AsyncClient configured for token calls."""
    return httpx.AsyncClient(**_token_client_kwargs(transport=transport))


def get_gateway_client() -> httpx.AsyncClient:
    """Return the shared AsyncClient for gateway calls, creating it on demand."""
    global _gateway_client
    if _gateway_client is None:
        _gateway_client = create_gateway_client()
    return _gateway_client


def get_token_client() -> httpx.AsyncClient:
    """Return the shared AsyncClient for token endpoint calls."""
    global _token_client
    if _token_client is None:
        _token_client = create_token_client()
    return _token_client


def reset_clients() -> None:
    """Reset cached clients (primarily for testing)."""

    global _gateway_client, _token_client

    for client in (_gateway_client, _token_client):
        _sync_close_async_client(client)

    _gateway_client = None
    _token_client = None

    try:
        from app.http import openai_client as _openai_client
    except Exception:
        return

    if getattr(_openai_client, "_client", None) is not None:
        _sync_close_async_client(_openai_client._client)
        _openai_client._client = None


__all__ = [
    "create_gateway_client",
    "create_token_client",
    "get_gateway_client",
    "get_token_client",
    "_sync_close_async_client",
    "reset_clients",
]
