from datetime import UTC, datetime, timedelta

import pytest

from app.db import SQLALCHEMY_AVAILABLE, SessionLocal
from app.db_models import BusinessDB
from app.services.calendar import (
    TimeSlot,
    _align_to_business_hours,
    _get_business_capacity,
    _get_business_hours,
    _parse_closed_days,
    calendar_service,
)


def test_parse_closed_days_handles_names_and_numbers() -> None:
    closed = _parse_closed_days("Sun, Monday, 2, 6")
    # Sunday (6), Monday (0), and explicit indices 2 and 6.
    assert closed == {0, 2, 6}

    assert _parse_closed_days("") == set()
    assert _parse_closed_days(None) == set()


def test_get_business_hours_respects_defaults_when_no_business() -> None:
    open_hour, close_hour, closed_days = _get_business_hours(None)
    assert isinstance(open_hour, int)
    assert isinstance(close_hour, int)
    assert isinstance(closed_days, set)


@pytest.mark.skipif(
    not SQLALCHEMY_AVAILABLE or SessionLocal is None,
    reason="Business-specific hours require database support",
)
def test_get_business_hours_uses_business_overrides_and_handles_misconfig() -> None:
    session = SessionLocal()
    try:
        biz_id = "calendar_hours_test"
        row = session.get(BusinessDB, biz_id)
        if row is None:
            row = BusinessDB(  # type: ignore[call-arg]
                id=biz_id, name="Calendar Hours Test", open_hour=9, close_hour=17
            )
            row.closed_days = "Sun,6"
            session.add(row)
        else:
            row.open_hour = 9
            row.close_hour = 17
            row.closed_days = "Sun,6"
        session.commit()
    finally:
        session.close()

    open_hour, close_hour, closed_days = _get_business_hours("calendar_hours_test")
    assert open_hour == 9
    assert close_hour == 17
    # "Sun" and "6" both map to Sunday; set should deduplicate.
    assert closed_days == {6}

    # Misconfigured hours where close <= open should yield no closed days
    # (treated as always open).
    session = SessionLocal()
    try:
        bad_id = "calendar_hours_misconfig"
        row = session.get(BusinessDB, bad_id)
        if row is None:
            row = BusinessDB(  # type: ignore[call-arg]
                id=bad_id, name="Calendar Misconfig", open_hour=18, close_hour=17
            )
            session.add(row)
        else:
            row.open_hour = 18
            row.close_hour = 17
        session.commit()
    finally:
        session.close()

    _, _, closed_days_bad = _get_business_hours("calendar_hours_misconfig")
    assert closed_days_bad == set()


@pytest.mark.skipif(
    not SQLALCHEMY_AVAILABLE or SessionLocal is None,
    reason="Business capacity lookups require database support",
)
def test_get_business_capacity_reads_business_fields() -> None:
    session = SessionLocal()
    try:
        biz_id = "calendar_capacity_test"
        row = session.get(BusinessDB, biz_id)
        if row is None:
            row = BusinessDB(  # type: ignore[call-arg]
                id=biz_id,
                name="Calendar Capacity Test",
                max_jobs_per_day=3,
                reserve_mornings_for_emergencies=True,
                travel_buffer_minutes=15,
            )
            session.add(row)
        else:
            row.max_jobs_per_day = 3
            row.reserve_mornings_for_emergencies = True
            row.travel_buffer_minutes = 15
        session.commit()
    finally:
        session.close()

    max_jobs, reserve_mornings, travel_buffer, service_durations = (
        _get_business_capacity("calendar_capacity_test")
    )
    assert max_jobs == 3
    assert reserve_mornings is True
    assert travel_buffer == 15
    assert service_durations == {}


def test_align_to_business_hours_skips_closed_days_and_respects_duration() -> None:
    # Sunday start with Sunday closed should move to Monday at open_hour.
    start = datetime(2025, 1, 5, 7, 0, tzinfo=UTC)  # Sunday
    duration = timedelta(hours=1)
    aligned = _align_to_business_hours(
        start=start,
        duration=duration,
        open_hour=9,
        close_hour=17,
        closed_days={6},  # Sunday
    )
    assert aligned.weekday() == 0  # Monday
    assert aligned.hour == 9

    # If the slot does not fit before close, alignment moves to the next day.
    start_late = datetime(2025, 1, 6, 16, 30, tzinfo=UTC)  # Monday
    duration_long = timedelta(hours=2)
    aligned_late = _align_to_business_hours(
        start=start_late,
        duration=duration_long,
        open_hour=9,
        close_hour=17,
        closed_days=set(),
    )
    assert aligned_late.date() > start_late.date()
    assert aligned_late.hour == 9


@pytest.mark.anyio
async def test_calendar_stub_create_update_delete_events_without_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Force stub mode by clearing any real client.
    calendar_service._client = None  # type: ignore[attr-defined]

    now = datetime.now(UTC)
    slot = TimeSlot(start=now, end=now + timedelta(minutes=30))

    event_id = await calendar_service.create_event(
        summary="Test",
        slot=slot,
        description="Test event",
        calendar_id=None,
    )
    assert event_id.startswith("event_placeholder_")

    updated = await calendar_service.update_event(
        event_id="event-1",
        slot=slot,
        summary="Updated",
        description="Updated",
        calendar_id=None,
    )
    assert updated is False

    deleted = await calendar_service.delete_event(
        event_id="event-1",
        calendar_id=None,
    )
    assert deleted is False
