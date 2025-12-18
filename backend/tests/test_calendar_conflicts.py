from datetime import UTC, datetime, timedelta

import pytest

from app.deps import DEFAULT_BUSINESS_ID
from app.services.calendar import calendar_service
from app.repositories import appointments_repo
from app.db import SQLALCHEMY_AVAILABLE, SessionLocal
from app.db_models import BusinessDB


def _skip_if_no_db() -> None:
    if not (SQLALCHEMY_AVAILABLE and SessionLocal is not None):
        pytest.skip("Database not available for calendar conflict tests")


def _reset_appts():
    if hasattr(appointments_repo, "_by_id"):
        appointments_repo._by_id.clear()  # type: ignore[attr-defined]
    if hasattr(appointments_repo, "_by_business"):
        appointments_repo._by_business.clear()  # type: ignore[attr-defined]


def _configure_business(**kwargs) -> None:
    session = SessionLocal()
    try:
        row = session.get(BusinessDB, DEFAULT_BUSINESS_ID)
        if not row:
            return
        for key, val in kwargs.items():
            setattr(row, key, val)
        session.add(row)
        session.commit()
    finally:
        session.close()


def test_has_conflict_respects_travel_buffer():
    _skip_if_no_db()
    _reset_appts()
    _configure_business(travel_buffer_minutes=15, open_hour=8, close_hour=18)

    start = datetime(2025, 1, 1, 10, 0, tzinfo=UTC)
    appointments_repo.create(
        customer_id="c1",
        start_time=start,
        end_time=start + timedelta(hours=1),
        service_type="install",
        business_id=DEFAULT_BUSINESS_ID,
        is_emergency=False,
    )

    conflict = calendar_service.has_conflict(
        business_id=DEFAULT_BUSINESS_ID,
        start=start + timedelta(minutes=50),
        end=start + timedelta(minutes=80),
    )
    assert conflict is True

    no_conflict = calendar_service.has_conflict(
        business_id=DEFAULT_BUSINESS_ID,
        start=start + timedelta(minutes=80),
        end=start + timedelta(minutes=110),
    )
    assert no_conflict is False


@pytest.mark.anyio
async def test_find_slots_uses_service_duration_config():
    _skip_if_no_db()
    _reset_appts()
    _configure_business(
        service_duration_config='{"install": 90}', open_hour=8, close_hour=18
    )
    slots = await calendar_service.find_slots(
        duration_minutes=30,
        business_id=DEFAULT_BUSINESS_ID,
        service_type="install",
    )
    assert slots
    slot = slots[0]
    assert int((slot.end - slot.start).total_seconds() // 60) == 90


def test_has_conflict_blocks_closed_days_and_hours():
    _skip_if_no_db()
    _reset_appts()
    _configure_business(closed_days="Sun", open_hour=8, close_hour=18)
    sunday = datetime(2025, 1, 5, 12, 0, tzinfo=UTC)  # Sunday

    assert calendar_service.has_conflict(
        business_id=DEFAULT_BUSINESS_ID, start=sunday, end=sunday + timedelta(hours=1)
    )

    weekday = datetime(2025, 1, 6, 7, 0, tzinfo=UTC)  # Before open
    assert calendar_service.has_conflict(
        business_id=DEFAULT_BUSINESS_ID, start=weekday, end=weekday + timedelta(hours=1)
    )

    ok_start = datetime(2025, 1, 6, 9, 0, tzinfo=UTC)
    assert (
        calendar_service.has_conflict(
            business_id=DEFAULT_BUSINESS_ID,
            start=ok_start,
            end=ok_start + timedelta(hours=1),
        )
        is False
    )
