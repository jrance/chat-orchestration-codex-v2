from __future__ import annotations

import time
from typing import Any, Dict, List, Sequence

import pytest

from app.tools import ddgs_search


def _install_fake_ddgs(
    monkeypatch: pytest.MonkeyPatch,
    *,
    text_items: Sequence[Dict[str, Any]] | None = None,
    news_items: Sequence[Dict[str, Any]] | None = None,
) -> List[tuple[str, str, Dict[str, Any]]]:
    calls: List[tuple[str, str, Dict[str, Any]]] = []
    text_items = list(text_items or [])
    news_items = list(news_items or [])

    class _FakeDDGS:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def text(self, query: str, **kwargs: Any):
            calls.append(("text", query, kwargs))
            return list(text_items)

        def news(self, query: str, **kwargs: Any):
            calls.append(("news", query, kwargs))
            return list(news_items)

    monkeypatch.setattr(ddgs_search, "DDGS", _FakeDDGS)
    return calls


@pytest.mark.asyncio
async def test_web_results_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _install_fake_ddgs(
        monkeypatch,
        text_items=[
            {"title": "Example Title", "href": "https://example.com/page", "body": "Snippet content"}
        ],
    )

    results = await ddgs_search.ddgs_search_execute({"query": "example"})

    assert len(results) == 1
    entry = results[0]
    assert entry["title"] == "Example Title"
    assert entry["url"] == "https://example.com/page"
    assert entry["source"] == "example.com"
    assert calls[0][0] == "text"
    assert calls[0][1] == "example"
    assert calls[0][2]["max_results"] == 5


@pytest.mark.asyncio
async def test_news_results_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _install_fake_ddgs(
        monkeypatch,
        news_items=[
            {
                "title": "News Item",
                "url": "https://news.example.com/story",
                "body": "Breaking news body",
                "source": "Example News",
                "date": "2024-06-01T00:00:00Z",
            }
        ],
    )

    results = await ddgs_search.ddgs_search_execute({"query": "news", "vertical": "news"})

    assert len(results) == 1
    entry = results[0]
    assert entry["source"] == "Example News"
    assert entry["published_at"] == "2024-06-01T00:00:00Z"
    assert calls[0][0] == "news"


@pytest.mark.asyncio
async def test_wikipedia_rewrites_query(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _install_fake_ddgs(monkeypatch)

    await ddgs_search.ddgs_search_execute(
        {"query": "mars rover", "vertical": "wikipedia", "siteFilter": ["nasa.gov"]}
    )

    assert calls[0][0] == "text"
    query_text = calls[0][1]
    assert "site:wikipedia.org" in query_text
    assert "site:nasa.gov" in query_text


@pytest.mark.asyncio
async def test_must_include_filters_results(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_ddgs(
        monkeypatch,
        text_items=[
            {"title": "Python release", "href": "https://python.org", "body": "Python 3.12 released"},
            {"title": "Other story", "href": "https://example.com", "body": "Completely unrelated content"},
        ],
    )

    results = await ddgs_search.ddgs_search_execute(
        {"query": "python", "mustInclude": ["python"]}
    )

    assert len(results) == 1
    assert results[0]["url"] == "https://python.org"


@pytest.mark.asyncio
async def test_timeout_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def slow_sync(_: ddgs_search.DdgsParams) -> list[dict[str, Any]]:
        time.sleep(0.05)
        return []

    monkeypatch.setattr(ddgs_search, "_ddgs_search_sync", slow_sync)

    with pytest.raises(TimeoutError):
        await ddgs_search.ddgs_search_execute({"query": "slow"}, timeout_ms=10)
