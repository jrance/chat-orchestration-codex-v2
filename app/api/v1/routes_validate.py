"""Validation endpoints."""

from fastapi import APIRouter

from app.validation.ir_validator import validate_and_normalize

from .models import OrchestrationPackage, ValidateResponse

router = APIRouter(prefix="/v1", tags=["v1"])


@router.post("/validate", response_model=ValidateResponse)
async def validate_package(pkg: OrchestrationPackage) -> ValidateResponse:
    """Validate an orchestration package and return normalization details."""

    ok, normalized, errors, warnings = validate_and_normalize(pkg.model_dump(exclude_none=True))
    message = "Valid IR" if ok else "Invalid IR"

    return ValidateResponse(
        ok=ok,
        message=message,
        normalized=normalized if ok else None,
        errors=errors,
        warnings=warnings,
    )
