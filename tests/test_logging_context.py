import json

import pytest

from app.logging import (
    bind_request_context,
    clear_request_context,
    configure_logging,
    current_context,
    get_logger,
)


@pytest.mark.parametrize("redact", [False, True])
def test_context_binding_and_redaction(capsys, redact: bool) -> None:
    configure_logging(redact=redact)
    capsys.readouterr()

    bind_request_context("corr-123", "req-456", "tenant-xyz")
    assert current_context()["correlation_id"] == "corr-123"

    try:
        log = get_logger("test")
        log.info("sample-event", email="user@example.com")
    finally:
        clear_request_context()
        configure_logging()  # Reset to default configuration for subsequent tests.

    assert all(value is None for value in current_context().values())

    output = capsys.readouterr().err.strip().splitlines()
    assert output, "Expected log output in stderr stream"
    parsed = json.loads(output[-1])

    assert parsed["correlation_id"] == "corr-123"
    assert parsed["request_id"] == "req-456"
    assert parsed["tenant_id"] == "tenant-xyz"

    if redact:
        assert parsed["email"] == "[REDACTED]"
    else:
        assert parsed["email"] == "user@example.com"
