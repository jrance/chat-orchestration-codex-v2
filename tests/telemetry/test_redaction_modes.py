from app.api.models import TelemetryRedactionMode
from app.telemetry.redaction import redact_headers, redact_text


def test_safe_redaction_masks_sensitive_tokens():
    text = "Email user@example.com token sk-SECRET Bearer abc123"
    redacted = redact_text(text, TelemetryRedactionMode.SAFE, 200)
    assert "[email]" in redacted
    assert "[token]" in redacted
    assert "sk-SECRET" not in redacted


def test_full_redaction_replaces_body():
    redacted = redact_text("sensitive payload", TelemetryRedactionMode.FULL, 10)
    assert redacted == "[redacted]"


def test_none_redaction_retains_text_but_masks_keys():
    text = "Bearer secret sk-SECRET"
    redacted = redact_text(text, TelemetryRedactionMode.NONE, 200)
    assert "Bearer [token]" in redacted
    assert "sk-SECRET" not in redacted


def test_header_redaction_preserves_allowlist():
    headers = {
        "X-Tenant-Id": "tenant-1",
        "X-Correlation-Id": "corr-1",
        "X-Request-Id": "req-1",
        "X-Timestamp": "2025-11-02T08:00:00Z",
        "Authorization": "Bearer secret",
        "X-Custom": "value",
    }
    sanitized = redact_headers(headers)
    assert sanitized["X-Tenant-Id"] == "tenant-1"
    assert sanitized["Authorization"] == "[redacted]"
    assert sanitized["X-Custom"].startswith("[hash:")
