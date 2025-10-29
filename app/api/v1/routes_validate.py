"""Validation endpoints."""

from fastapi import APIRouter

from .models import OrchestrationPackage, ValidateResponse

router = APIRouter(prefix="/v1", tags=["v1"])


@router.post("/validate", response_model=ValidateResponse, status_code=501)
async def validate_package(pkg: OrchestrationPackage) -> ValidateResponse:
    """Stub validation endpoint; detailed validation arrives in later PRs."""
    return ValidateResponse(ok=False, message="Validation not yet implemented")
