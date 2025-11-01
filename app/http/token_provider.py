"""OAuth token acquisition for the Apigee gateway."""

from __future__ import annotations

import time
import asyncio
from typing import Optional, cast

import httpx
from pydantic import SecretStr

from app.config.settings import settings

TOKEN_SKEW_SECONDS = 60
TOKEN_DEFAULT_EXPIRY = 300


class TokenCache:
    """In-memory cache for bearer tokens with proactive refresh skew."""

    __slots__ = ("access_token", "expires_at")

    def __init__(self) -> None:
        self.access_token: Optional[str] = None
        self.expires_at: float = 0.0

    def is_valid(self) -> bool:
        """Return True when a cached token exists and is not near expiry."""
        if not self.access_token:
            return False
        return (time.time() + TOKEN_SKEW_SECONDS) < self.expires_at

    def set(self, token: str, expires_in: int) -> None:
        """Cache the token with best-effort expiry metadata."""
        ttl = max(TOKEN_DEFAULT_EXPIRY, int(expires_in))
        self.access_token = token
        self.expires_at = time.time() + ttl

    def clear(self) -> None:
        self.access_token = None
        self.expires_at = 0.0


class ApigeeTokenProvider:
    """Client-credentials token provider for the Apigee OAuth endpoint."""

    def __init__(self, transport: httpx.BaseTransport | None = None) -> None:
        self._cache = TokenCache()
        self._transport = transport
        self._lock = asyncio.Lock()

    async def get_token(self, force_refresh: bool = False) -> str:
        """Return a cached bearer token, refreshing when necessary."""
        if not force_refresh and self._cache.is_valid():
            return cast(str, self._cache.access_token)

        async with self._lock:
            if not force_refresh and self._cache.is_valid():
                return cast(str, self._cache.access_token)

            if settings.openai_api_key:
                return settings.openai_api_key
            
            token_url = settings.apigee_token_url
            client_id = settings.apigee_client_id
            client_secret_setting = settings.apigee_client_secret
            if isinstance(client_secret_setting, SecretStr):
                client_secret = client_secret_setting.get_secret_value()
            else:
                client_secret = client_secret_setting

            settings.debug_summary()
            if not token_url or not client_id or not client_secret:
                raise RuntimeError(
                    "Apigee token configuration missing (APIGEE_TOKEN_URL/CLIENT_ID/CLIENT_SECRET).",
                )

            form_data: dict[str, str] = {
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
            }
            scopes = (settings.apigee_scopes or "").strip()
            if scopes:
                form_data["scope"] = scopes
            audience = (settings.apigee_audience or "").strip()
            if audience:
                form_data["audience"] = audience

            timeout = httpx.Timeout(15.0)
            async with httpx.AsyncClient(timeout=timeout, transport=self._transport) as client:
                response = await client.post(
                    token_url,
                    data=form_data,
                    headers={"Accept": "application/json"},
                )
            response.raise_for_status()
            payload = response.json()

            token = payload.get("access_token")
            if not isinstance(token, str) or not token:
                raise RuntimeError("Apigee token endpoint returned an invalid payload (missing access_token).")

            expires_in_raw = payload.get("expires_in", TOKEN_DEFAULT_EXPIRY)
            try:
                expires_in = int(expires_in_raw)
            except (TypeError, ValueError):
                expires_in = TOKEN_DEFAULT_EXPIRY

            self._cache.set(token, expires_in)
            return token

    def clear_cache(self) -> None:
        """Clear the cached token (primarily for testing)."""
        self._cache.clear()
