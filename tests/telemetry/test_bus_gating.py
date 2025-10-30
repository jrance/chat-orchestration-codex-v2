from app.api.deps import ExecutionContext
from app.api.models import ExecutionHeaders, TelemetryLevel
from app.telemetry.bus import configure_publisher, publish_telemetry


def teardown_module():
    configure_publisher(None)


def test_no_header_suppresses_events():
    captured = []
    configure_publisher(captured.append)
    ctx = ExecutionContext(headers=ExecutionHeaders(tenant_id="tenant"))
    publish_telemetry(ctx, {"type": "event"}, level=TelemetryLevel.BASIC)
    assert captured == []
    configure_publisher(None)


def test_basic_blocks_verbose_events():
    captured = []
    configure_publisher(captured.append)
    ctx = ExecutionContext(headers=ExecutionHeaders(tenant_id="tenant", telemetry=TelemetryLevel.BASIC))
    publish_telemetry(ctx, {"type": "basic"}, level=TelemetryLevel.BASIC)
    publish_telemetry(ctx, {"type": "verbose"}, level=TelemetryLevel.VERBOSE)
    assert captured == [{"type": "basic"}]
    configure_publisher(None)


def test_verbose_allows_all_events():
    captured = []
    configure_publisher(captured.append)
    ctx = ExecutionContext(headers=ExecutionHeaders(tenant_id="tenant", telemetry=TelemetryLevel.VERBOSE))
    publish_telemetry(ctx, {"type": "basic"}, level=TelemetryLevel.BASIC)
    publish_telemetry(ctx, {"type": "verbose"}, level=TelemetryLevel.VERBOSE)
    assert captured == [{"type": "basic"}, {"type": "verbose"}]
    configure_publisher(None)
