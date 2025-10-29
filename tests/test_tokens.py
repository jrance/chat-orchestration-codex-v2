from app.context.tokens import expand, get_registry


def test_unknown_token_left_intact() -> None:
    assert expand("Hello {unknown_token}") == "Hello {unknown_token}"


def test_default_tokens_present() -> None:
    registry = get_registry()
    for key in [
        "tenant_id",
        "correlation_id",
        "request_id",
        "now_iso",
        "date_yyyy_mm_dd",
        "user_details",
    ]:
        assert key in registry


def test_user_details_injection() -> None:
    payload = {"user_details": {"name": "Jamie", "title": "DE"}}
    rendered = expand("User={user_details}", payload)
    assert "Jamie" in rendered
    assert "DE" in rendered
