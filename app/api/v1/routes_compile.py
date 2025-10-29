"""Compilation endpoints."""

from fastapi import APIRouter

from app.ir.loader import build_runtime_plan

from .models import CompileResponse, OrchestrationPackage

router = APIRouter(prefix="/v1", tags=["v1"])


@router.post("/compile", response_model=CompileResponse)
async def compile_package(pkg: OrchestrationPackage) -> CompileResponse:
    """Validate the orchestration package and prepare a runtime plan."""
    ok, _plan, errors = build_runtime_plan(pkg.model_dump(), runtime_vars={})
    if ok:
        return CompileResponse(ok=True, graph_id="planned", message="Plan ready")
    message = "; ".join(errors) if errors else "Invalid IR"
    return CompileResponse(ok=False, message=message[:512])
