"""FastAPI application factory and entrypoint."""

from fastapi import FastAPI

from app.api.v1.routes_compile import router as compile_router
from app.api.v1.routes_execute import router as execute_router
from app.api.v1.routes_validate import router as validate_router
from app.config.settings import settings
from app.middleware.request_ids import RequestIdMiddleware


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""

    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        docs_url="/docs",
        redoc_url=None,
    )

    app.add_middleware(RequestIdMiddleware)

    @app.get("/health", tags=["meta"])
    async def health() -> dict[str, str]:
        return {"status": "ok", "env": settings.app_env}

    app.include_router(validate_router)
    app.include_router(compile_router)
    app.include_router(execute_router)

    return app


app = create_app()
