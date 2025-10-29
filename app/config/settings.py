"""Environment-driven application settings."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Core configuration pulled from environment variables."""

    app_name: str = "codeless-orch"
    app_env: str = "dev"
    app_host: str = "127.0.0.1"
    app_port: int = 8000

    default_tenant_id: str = "demo-tenant"
    apigee_client_id: str | None = None
    apigee_token_url: str | None = None
    log_level: str = "INFO"
    log_redaction_enabled: bool = False

    model_config = SettingsConfigDict(env_file=".env", env_prefix="", extra="ignore")


settings = Settings()
