import pytest

from app.runtime.engine import resume_run


@pytest.mark.anyio
async def test_resume_run_missing_run():
    with pytest.raises(ValueError) as exc:
        await resume_run(
            run_id="missing",
            resume_kind="continue",
            payload={},
            tenant_id="tenant-1",
            request_id=None,
            correlation_id=None,
        )
    assert str(exc.value) == "RUN_NOT_FOUND"
