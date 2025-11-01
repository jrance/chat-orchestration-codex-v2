"""Helpers for configuring proxy support on HTTPX clients."""

from __future__ import annotations

import base64
import ssl
import textwrap
from typing import Optional

from app.config.settings import settings


def build_httpx_proxies() -> Optional[dict[str, str]]:
    """Return an HTTPX proxies mapping when proxying is enabled."""
    if not getattr(settings, "proxy_enabled", False):
        return None
    url = getattr(settings, "proxy_url", None)
    if not url:
        return None
    return {"all": url}


def _normalize_pem_from_bundle(bundle: str) -> str:
    """Return PEM text regardless of whether bundle is PEM or base64 DER."""
    normalized = bundle.strip()
    if normalized.startswith("-----BEGIN"):
        return normalized
    decoded = base64.b64decode(normalized, validate=False)
    try:
        decoded_text = decoded.decode("ascii")
    except UnicodeDecodeError:
        decoded_text = ""
    if decoded_text.startswith("-----BEGIN"):
        return decoded_text
    encoded = base64.b64encode(decoded).decode("ascii")
    wrapped = "\n".join(textwrap.wrap(encoded, 64))
    return "-----BEGIN CERTIFICATE-----\n" + wrapped + "\n-----END CERTIFICATE-----\n"


def build_ssl_verify() -> ssl.SSLContext | bool:
    """Return an SSL verify value for HTTPX clients respecting proxy bundle."""
    if not getattr(settings, "proxy_enabled", False):
        return True
    bundle = getattr(settings, "proxy_ca_bundle", None)
    if not bundle:
        return True
    pem = _normalize_pem_from_bundle(bundle)
    context = ssl.create_default_context()
    context.load_verify_locations(cadata=pem)
    return context


__all__ = ["build_httpx_proxies", "build_ssl_verify"]
