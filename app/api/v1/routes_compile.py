"""Compilation endpoints."""

from fastapi import APIRouter

from .models import CompileResponse, OrchestrationPackage

router = APIRouter(prefix="/v1", tags=["v1"])


@router.post("/compile", response_model=CompileResponse, status_code=501)
async def compile_package(pkg: OrchestrationPackage) -> CompileResponse:
    """Stub compilation endpoint until LangGraph integration lands."""
    return CompileResponse(ok=False, message="Compile not yet implemented")
