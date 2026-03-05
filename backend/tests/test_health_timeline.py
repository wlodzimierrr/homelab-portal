from datetime import datetime, timedelta, timezone

from app.health_timeline import (
    TimelinePoint,
    TimelineThresholds,
    classify_timeline_status,
    compact_timeline_points,
)


def test_classify_timeline_status_healthy() -> None:
    status, reason = classify_timeline_status(
        availability=0.999,
        error_rate_pct=0.2,
        readiness=1.0,
        thresholds=TimelineThresholds(),
    )
    assert status == "healthy"
    assert reason is None


def test_classify_timeline_status_degraded() -> None:
    status, reason = classify_timeline_status(
        availability=0.994,
        error_rate_pct=0.2,
        readiness=1.0,
        thresholds=TimelineThresholds(),
    )
    assert status == "degraded"
    assert reason == "availability_regression"


def test_classify_timeline_status_down() -> None:
    status, reason = classify_timeline_status(
        availability=0.5,
        error_rate_pct=0.2,
        readiness=1.0,
        thresholds=TimelineThresholds(),
    )
    assert status == "down"
    assert reason == "low_availability"


def test_classify_timeline_status_unknown() -> None:
    status, reason = classify_timeline_status(
        availability=None,
        error_rate_pct=0.2,
        readiness=1.0,
        thresholds=TimelineThresholds(),
    )
    assert status == "unknown"
    assert reason is not None
    assert "availability" in reason


def test_compact_timeline_points_merges_adjacent_states() -> None:
    start = datetime(2026, 3, 5, 12, 0, tzinfo=timezone.utc)
    points = [
        TimelinePoint(timestamp=start, status="healthy", reason=None),
        TimelinePoint(timestamp=start + timedelta(minutes=5), status="healthy", reason=None),
        TimelinePoint(timestamp=start + timedelta(minutes=10), status="degraded", reason="error_rate_regression"),
    ]

    segments = compact_timeline_points(
        points,
        window_start=start,
        window_end=start + timedelta(minutes=15),
        step=timedelta(minutes=5),
    )

    assert len(segments) == 2
    assert segments[0].status == "healthy"
    assert segments[1].status == "degraded"
