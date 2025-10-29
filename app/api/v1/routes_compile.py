"""Compilation endpoints."""

from __future__ import annotations

from uuid import uuid4

from fastapi import APIRouter

from app.compiler.builder import GraphBuilder
from app.compiler.registry import put as put_graph
from app.ir.loader import build_runtime_plan

from .models import CompileResponse, OrchestrationPackage

router = APIRouter(prefix="/v1", tags=["v1"])


@router.post("/compile", response_model=CompileResponse)
async def compile_package(pkg: OrchestrationPackage) -> CompileResponse:
    """Validate the orchestration package, compile it, and register the graph."""

    ok, plan, errors = build_runtime_plan(pkg.model_dump(), runtime_vars={})
    if not ok:
        message = "; ".join(errors) if errors else "Invalid orchestration package"
        return CompileResponse(ok=False, message=message[:512])

    try:
        builder = GraphBuilder(plan)
        app = builder.build()
    except Exception as exc:  # pragma: no cover - defensive pathway
        return CompileResponse(ok=False, message=f"Compilation error: {exc}".strip()[:512])

    graph_id = f"graph-{uuid4().hex}"
    put_graph(graph_id, app)

    node_count = len(builder.node_by_id)
    entry_id = builder.entry_id
    warnings = plan.get("warnings") or []
    summary = f"Compiled {node_count} node(s); entry='{entry_id}'"
    if warnings:
        summary = f"{summary}; {len(warnings)} warning(s)"

    return CompileResponse(ok=True, graph_id=graph_id, message=summary[:512])
