from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from app.services import calendar as calendar_svc


def test_parse_closed_days_accepts_names_and_numbers() -> None:
    closed = calendar_svc._parse_closed_days("Sun,1,Tuesday,9")
    # Sunday=6, Monday=0, Tuesday=1 in the helper mapping.
    assert 6 in closed
    assert 1 in closed
    assert 9 not in closed


def test_business_hours_and_alignment_guard_against_bad_config(monkeypatch) -> None:
    # Force a configuration with close_hour <= open_hour to hit the guard path.
    dummy_settings = SimpleNamespace(
        calendar=SimpleNamespace(
            default_open_hour=10,
            default_close_hour=10,
            default_closed_days="Sat,Sun",
        )
    )
    monkeypatch.setattr(calendar_svc, "get_settings", lambda: dummy_settings)

    open_hour, close_hour, closed = calendar_svc._get_business_hours(business_id=None)
    assert open_hour == 10
    assert close_hour == 10
    # Guard returns an empty closed set when hours are misconfigured.
    assert closed == set()

    start = datetime(2025, 12, 6, 9, tzinfo=UTC)  # Saturday
    aligned = calendar_svc._align_to_business_hours(
        start=start,
        duration=timedelta(hours=1),
        open_hour=open_hour,
        close_hour=close_hour,
        closed_days={5, 6},
    )
    # With bad hours and weekend closure, fallback returns the original start.
    assert aligned == start
