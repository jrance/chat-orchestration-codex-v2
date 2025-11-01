"""DuckDuckGo Search tool implementation."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any, Dict, List, Literal, Mapping, TypedDict
from urllib.parse import urlparse

import anyio
from pydantic import BaseModel, ConfigDict, Field, conint, field_validator

from .registry import register_tool
from .types import ArgMode

try:  # pragma: no cover - exercised via tests through monkeypatching
    from duckduckgo_search import DDGS  # type: ignore
except Exception:  # pragma: no cover - defensive fallback when dependency missing
    DDGS = None


Vertical = Literal["web", "news", "wikipedia"]
SafeLevel = Literal["off", "moderate", "strict"]
TimeLimit = Literal["", "d", "w", "m", "y"]


class DdgsParams(BaseModel):
    """Validated parameter bundle for DDGS queries."""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(..., description="Search query text")
    vertical: Vertical = Field("web", description="Search index to query")
    maxResults: conint(ge=1, le=50) = Field(5, description="Maximum number of results to return")
    safesearch: SafeLevel = Field("moderate", description="Safe search strictness")
    region: str = Field("us-en", description="Region code, e.g. us-en, uk-en, wt-wt")
    timeLimit: TimeLimit = Field("", description="Freshness window: '', d, w, m, y")
    siteFilter: List[str] = Field(default_factory=list, description="Restrict results to these hostnames")
    mustInclude: List[str] = Field(default_factory=list, description="Discard hits missing these terms")

    @field_validator("query")
    @classmethod
    def _query_not_blank(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("query is required")
        return value.strip()

    @field_validator("siteFilter", "mustInclude", mode="before")
    @classmethod
    def _coerce_str_list(cls, value: Any) -> List[str]:
        if value is None:
            return []
        if isinstance(value, str):
            value = [value]
        if isinstance(value, Iterable):
            items: List[str] = []
            for item in value:
                if not isinstance(item, str):
                    item = str(item)
                trimmed = item.strip()
                if trimmed:
                    items.append(trimmed)
            return items
        raise ValueError("Expected a list of strings")


class DdgsResult(TypedDict, total=False):
    """Normalized DDGS result payload."""

    title: str
    url: str
    snippet: str
    source: str
    published_at: str


def _rewrite_query(base: str, vertical: Vertical, sites: Sequence[str]) -> str:
    query = base
    normalized_sites = [site for site in dict.fromkeys(site.strip() for site in sites if site.strip())]
    if vertical == "wikipedia" and "wikipedia.org" not in normalized_sites:
        normalized_sites.insert(0, "wikipedia.org")
    if normalized_sites:
        site_expr = " OR ".join(f"site:{site}" for site in normalized_sites)
        query = f"({query}) {site_expr}"
    return query


def _filter_must_include(items: Sequence[Mapping[str, Any]], terms: Sequence[str]) -> List[Mapping[str, Any]]:
    if not terms:
        return list(items)
    needles = [term.lower() for term in terms if term.strip()]
    if not needles:
        return list(items)

    filtered: List[Mapping[str, Any]] = []
    for item in items:
        haystack = " ".join(
            str(item.get(key) or "")
            for key in ("title", "body", "snippet", "excerpt", "text")
        ).lower()
        if all(needle in haystack for needle in needles):
            filtered.append(item)
    return filtered


def _source_from_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.netloc:
        return parsed.netloc.lower()
    return "ddg"


def _normalize_web(items: Sequence[Mapping[str, Any]]) -> List[DdgsResult]:
    normalized: List[DdgsResult] = []
    for item in items:
        url = str(item.get("href") or item.get("url") or "")
        normalized.append(
            {
                "title": str(item.get("title") or ""),
                "url": url,
                "snippet": str(item.get("body") or item.get("snippet") or ""),
                "source": _source_from_url(url),
            }
        )
    return normalized


def _normalize_news(items: Sequence[Mapping[str, Any]]) -> List[DdgsResult]:
    normalized: List[DdgsResult] = []
    for item in items:
        url = str(item.get("url") or item.get("href") or "")
        published = item.get("date") or item.get("published")
        if hasattr(published, "isoformat"):
            published_str = published.isoformat()  # type: ignore[call-arg]
        else:
            published_str = str(published or "")
        normalized.append(
            {
                "title": str(item.get("title") or ""),
                "url": url,
                "snippet": str(item.get("body") or item.get("excerpt") or ""),
                "source": str(item.get("source") or item.get("publisher") or _source_from_url(url)),
                "published_at": published_str,
            }
        )
    return normalized


def _ddgs_search_sync(params: DdgsParams) -> List[DdgsResult]:
    if DDGS is None:
        raise RuntimeError("duckduckgo_search is not installed")

    rewritten_query = _rewrite_query(params.query, params.vertical, params.siteFilter)
    time_limit = params.timeLimit or None
    max_results = int(params.maxResults)

    with DDGS() as ddgs:  # type: ignore[operator]
        if params.vertical == "news":
            raw_items = list(
                ddgs.news(
                    rewritten_query,
                    region=params.region,
                    safesearch=params.safesearch,
                    timelimit=time_limit,
                    max_results=max_results,
                )
                or []
            )
            filtered = _filter_must_include(raw_items, params.mustInclude)
            return _normalize_news(filtered)

        raw_items = list(
            ddgs.text(
                rewritten_query,
                region=params.region,
                safesearch=params.safesearch,
                timelimit=time_limit,
                max_results=max_results,
            )
            or []
        )
        filtered = _filter_must_include(raw_items, params.mustInclude)
        return _normalize_web(filtered)


async def ddgs_search_execute(args: Dict[str, Any], *, timeout_ms: int | None = None) -> List[DdgsResult]:
    """Run a DDGS query in a worker thread with optional timeout."""

    params = DdgsParams(**args)
    timeout = timeout_ms / 1000.0 if timeout_ms and timeout_ms > 0 else None

    async def _run() -> List[DdgsResult]:
        return await anyio.to_thread.run_sync(_ddgs_search_sync, params)

    if timeout:
        with anyio.move_on_after(timeout) as scope:
            results = await _run()
            if scope.cancel_called:  # pragma: no cover - defensive guard
                raise TimeoutError("ddgs.search timed out")
            return results

    return await _run()


async def _handler(call_args: Dict[str, Any]) -> Dict[str, Any]:
    timeout_override = call_args.pop("timeoutMs", None)
    timeout_value = int(timeout_override) if timeout_override else None
    results = await ddgs_search_execute(call_args, timeout_ms=timeout_value)
    payload: Dict[str, Any] = {
        "query": call_args.get("query"),
        "vertical": call_args.get("vertical"),
        "results": results,
        "count": len(results),
    }
    return payload


def register() -> None:
    """Register the DDGS search tool."""

    register_tool(
        {
            "id": "tool:ddgs.search",
            "name": "DuckDuckGo Search",
            "description": "Perform ad-hoc web, news, or Wikipedia searches via DuckDuckGo (no API key required).",
            "args_schema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query text"},
                    "vertical": {
                        "type": "string",
                        "enum": ["web", "news", "wikipedia"],
                        "description": "DuckDuckGo vertical to search",
                    },
                    "maxResults": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 50,
                        "description": "Maximum number of results to return",
                    },
                    "safesearch": {
                        "type": "string",
                        "enum": ["off", "moderate", "strict"],
                        "description": "Safe search strictness",
                    },
                    "region": {
                        "type": "string",
                        "description": "Region code (e.g. us-en, uk-en, wt-wt)",
                    },
                    "timeLimit": {
                        "type": "string",
                        "enum": ["", "d", "w", "m", "y"],
                        "description": "Freshness window to apply",
                    },
                    "siteFilter": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Restrict results to these hostnames",
                    },
                    "mustInclude": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Require these keywords in the snippet/title",
                    },
                    "timeoutMs": {
                        "type": "integer",
                        "minimum": 100,
                        "maximum": 120000,
                        "description": "Optional override for the tool timeout (milliseconds)",
                    },
                },
                "required": ["query"],
            },
            "arg_behaviors": {
                "timeoutMs": {"mode": ArgMode.LLM_HIDDEN, "default": 6000},
            },
            "handler": _handler,
            "timeout_ms": 12_000,
        }
    )


__all__ = ["DdgsParams", "DdgsResult", "ddgs_search_execute", "register"]
