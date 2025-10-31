"""FastAPI application factory and entrypoint."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.routes_compile import router as compile_router
from app.api.v1.routes_execute import router as execute_router
from app.api.v1.routes_validate import router as validate_router
from app.config.settings import settings
from app.logging import configure_logging, get_logger
from app.middleware.request_ids import RequestIdMiddleware
from docs.openapi_overrides import apply_openapi_overrides

log = get_logger(__name__)


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""

    configure_logging(level=settings.log_level, redact=settings.log_redaction_enabled)

    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        docs_url="/docs",
        redoc_url=None,
    )

    # Add CORS middleware (PR-015)
    if settings.cors_enabled:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_allow_origins,
            allow_credentials=settings.cors_allow_credentials,
            allow_methods=settings.cors_allow_methods,
            allow_headers=settings.cors_allow_headers,
            expose_headers=settings.cors_expose_headers,
            max_age=86400,  # cache preflight for 24h
        )

    app.add_middleware(RequestIdMiddleware)

    @app.get("/health", tags=["meta"])
    async def health() -> dict[str, str]:
        log.info("healthcheck", env=settings.app_env, status="ok")
        return {"status": "ok", "env": settings.app_env}

    app.include_router(validate_router)
    app.include_router(compile_router)
    app.include_router(execute_router)
    apply_openapi_overrides(app)

    return app


app = create_app()
