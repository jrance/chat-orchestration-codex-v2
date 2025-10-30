from app.api.models import ExecutionHeaders, TelemetryLevel


class _Request:
    def __init__(self, headers=None):
        self.headers = headers or {}


def test_default_telemetry_is_none():
    headers = ExecutionHeaders.from_request(_Request())
    assert headers.telemetry is TelemetryLevel.NONE


def test_invalid_telemetry_falls_back_to_none():
    headers = ExecutionHeaders.from_request(_Request({"X-Telemetry": "invalid"}))
    assert headers.telemetry is TelemetryLevel.NONE
