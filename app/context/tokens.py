"""Token registry for prompt assembly."""

from __future__ import annotations

import datetime as dt
import json
import re
from typing import Any, Callable, Dict, Mapping, Optional

from app.logging.context import current_context

TokenProvider = Callable[[Mapping[str, Any]], Any]
_registry: Dict[str, TokenProvider] = {}
_TOKEN_PATTERN = re.compile(r"\{([a-zA-Z0-9_.:-]+)\}")


def register_token(name: str, provider: TokenProvider) -> None:
    """Register or replace a token provider."""
    _registry[name] = provider


def _ensure_defaults() -> None:
    if _registry:
        return

    # Core identifiers from the logging context.
    register_token(
        "tenant_id",
        lambda ctx: ctx.get("tenant_id") or current_context().get("tenant_id"),
    )
    register_token(
        "correlation_id",
        lambda ctx: ctx.get("correlation_id") or current_context().get("correlation_id"),
    )
    register_token(
        "request_id",
        lambda ctx: ctx.get("request_id") or current_context().get("request_id"),
    )

    # Time helpers.
    register_token(
        "now_iso",
        lambda ctx: dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
    )
    register_token(
        "date_yyyy_mm_dd",
        lambda ctx: dt.datetime.utcnow().strftime("%Y-%m-%d"),
    )

    # User metadata (caller-provided).
    def _user_details(ctx: Mapping[str, Any]) -> Any:
        return ctx.get("user_details") or {}

    register_token("user_details", _user_details)


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def expand(text: str, runtime_vars: Optional[Mapping[str, Any]] = None) -> str:
    """Expand `{token}` placeholders using the registered providers."""
    if not text:
        return text

    _ensure_defaults()
    runtime_vars = runtime_vars or {}

    def _replace(match: re.Match[str]) -> str:
        name = match.group(1)
        provider = _registry.get(name)
        if not provider:
            return match.group(0)
        try:
            value = provider(runtime_vars)
        except Exception:
            return match.group(0)
        return _stringify(value)

    return _TOKEN_PATTERN.sub(_replace, text)


def get_registry() -> Dict[str, TokenProvider]:
    """Return a shallow copy of the registered token providers."""
    _ensure_defaults()
    return dict(_registry)


_ensure_defaults()

__all__ = ["TokenProvider", "expand", "get_registry", "register_token"]
