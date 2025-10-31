"""Environment-driven application settings."""

from __future__ import annotations

import json
from typing import Any

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Core configuration pulled from environment variables."""

    app_name: str = "codeless-orch"
    app_env: str = "dev"
    app_host: str = "127.0.0.1"
    app_port: int = 8000

    default_tenant_id: str = "demo-tenant"

    # Logging (from PR-02)
    log_level: str = "INFO"
    log_redaction_enabled: bool = False

    # Apigee / OpenAI-compatible gateway
    apigee_token_url: str | None = None
    apigee_client_id: str | None = None
    apigee_client_secret: str | None = None
    apigee_audience: str | None = None
    apigee_scopes: str = "openid"
    openai_base_url: str = "http://localhost:8000"
    apigee_extra_headers_json: str | None = None

    # HTTP client tuning
    http_timeout_seconds: float = 30.0
    http_max_retries: int = 2
    http_retry_backoff_ms: int = 250
    http_connect_timeout: float = 5.0
    http_read_timeout: float = 60.0
    http_write_timeout: float = 60.0
    http_pool_max_connections: int = 100
    http_pool_max_keepalive: int = 20
    http_retry_max_attempts: int = 3
    http_retry_base_delay: float = 0.2

    # Validation (PR-04)
    orch_schema_path: str = "schemas/orchestration_ir.schema.json"

    # Prompt preamble configuration (PR-05)
    org_preamble_path: str | None = "docs/org_preamble.md"
    org_preamble_text: str | None = None

    # Runtime persistence configuration (PR-07/PR-14)
    checkpointer_kind: str = "memory"  # legacy name kept for compatibility
    checkpointer_backend: str = "memory"
    run_store_kind: str = "memory"
    state_ttl_sec: int | None = 86_400
    state_max_bytes: int | None = 5_242_880
    redis_url: str | None = None
    redis_emulator: bool = False

    # CORS configuration (PR-015)
    cors_enabled: bool = True
    cors_allow_origins: list[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:8080",
    ]
    cors_allow_credentials: bool = True
    cors_allow_methods: list[str] = ["*"]
    cors_allow_headers: list[str] = ["*"]
    cors_expose_headers: list[str] = [
        "X-Run-Id",
        "X-Request-Id",
        "X-Correlation-Id",
        "Content-Type",
        "Cache-Control",
    ]

    model_config = SettingsConfigDict(env_file=".env", env_prefix="", extra="ignore")

    def model_post_init(self, __context: Any) -> None:  # type: ignore[override]
        """Backfill legacy HTTP tuning defaults when new knobs are provided."""
        fields_set = getattr(self, "model_fields_set", set())
        if "http_connect_timeout" not in fields_set:
            self.http_connect_timeout = float(self.http_timeout_seconds)
        if "http_read_timeout" not in fields_set:
            self.http_read_timeout = float(self.http_timeout_seconds)
        if "http_write_timeout" not in fields_set:
            self.http_write_timeout = float(self.http_timeout_seconds)
        if "http_retry_max_attempts" not in fields_set:
            self.http_retry_max_attempts = int(self.http_max_retries)
        if "http_retry_base_delay" not in fields_set:
            self.http_retry_base_delay = float(self.http_retry_backoff_ms) / 1000.0

    def apigee_extra_headers(self) -> dict[str, Any]:
        """Return configured static headers to include with every Apigee call."""
        if not self.apigee_extra_headers_json:
            return {}
        try:
            loaded = json.loads(self.apigee_extra_headers_json)
        except json.JSONDecodeError:
            return {}
        if not isinstance(loaded, dict):
            return {}
        return {str(k): str(v) for k, v in loaded.items()}

    # -- runtime helpers -----------------------------------------------------

    def state_ttl(self) -> int | None:
        """Return configured state TTL seconds or None when disabled."""

        if self.state_ttl_sec is None:
            return None
        try:
            value = int(self.state_ttl_sec)
        except (TypeError, ValueError):
            return None
        return value if value > 0 else None

    def state_max_bytes_limit(self) -> int | None:
        """Return configured approximate max bytes for in-memory persistence."""

        if self.state_max_bytes is None:
            return None
        try:
            value = int(self.state_max_bytes)
        except (TypeError, ValueError):
            return None
        return value if value > 0 else None


settings = Settings()


def reload_settings() -> Settings:
    """Reload settings from the environment in-place (useful during testing)."""
    new_settings = Settings()
    settings.__dict__.update(new_settings.__dict__)
    return settings

