from datetime import datetime, timezone

from app.logs_quickview import (
    build_preset_query,
    build_time_window,
    clear_rate_limit_state_for_tests,
    decode_cursor_ns,
    encode_cursor_ns,
    enforce_logs_rate_limit,
    validate_preset,
)


def test_validate_preset_allows_only_approved_values() -> None:
    assert validate_preset("errors") == "errors"
    assert validate_preset("restarts") == "restarts"
    assert validate_preset("warnings") == "warnings"

    try:
        validate_preset("arbitrary")
        assert False, "expected preset validation to fail"
    except ValueError:
        pass


def test_cursor_encode_decode_roundtrip() -> None:
    value = 1_700_000_000_123_456_789
    encoded = encode_cursor_ns(value)
    assert decode_cursor_ns(encoded) == value


def test_build_preset_query_is_scoped_to_service_and_namespace() -> None:
    query = build_preset_query(
        app_label="portal-api",
        namespace="default",
        preset="errors",
    )
    assert 'namespace="default"' in query
    assert 'app="portal-api"' in query
    assert '|~ "' in query
    assert "error" in query


def test_build_preset_query_uses_valid_regex_filters_for_compound_presets() -> None:
    warnings_query = build_preset_query(
        app_label="portal-api",
        namespace="default",
        preset="warnings",
    )
    restarts_query = build_preset_query(
        app_label="portal-api",
        namespace="default",
        preset="restarts",
    )

    assert '|~ "' in warnings_query
    assert "warn" in warnings_query
    assert "timeout" in warnings_query
    assert '|~ "' in restarts_query
    assert "restart" in restarts_query
    assert "CrashLoopBackOff" in restarts_query


def test_build_time_window_uses_range_and_cursor() -> None:
    now = datetime(2026, 3, 5, 12, 0, tzinfo=timezone.utc)
    cursor = encode_cursor_ns(int(now.timestamp() * 1_000_000_000))
    window = build_time_window(now=now, range_value="1h", cursor=cursor)
    assert window.end < now
    assert window.start < window.end


def test_enforce_logs_rate_limit_blocks_when_exceeded(monkeypatch) -> None:
    clear_rate_limit_state_for_tests()
    monkeypatch.setenv("LOGS_QUICKVIEW_RATE_LIMIT_PER_MIN", "1")
    now = datetime(2026, 3, 5, 12, 0, tzinfo=timezone.utc)

    enforce_logs_rate_limit(identity_key="alice", now=now)
    try:
        enforce_logs_rate_limit(identity_key="alice", now=now)
        assert False, "expected rate limit exception"
    except ValueError:
        pass
