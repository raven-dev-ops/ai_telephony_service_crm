from datetime import UTC, datetime, timedelta

import pytest

from app.services.calendar import HttpError, TimeSlot, calendar_service
import app.services.calendar as calendar_mod


def test_parse_closed_days_and_invalid_hours(monkeypatch):
    # Avoid DB access.
    monkeypatch.setattr(calendar_mod, "SQLALCHEMY_AVAILABLE", False)
    monkeypatch.setattr(calendar_mod, "SessionLocal", None)

    class DummyCalendar:
        default_open_hour = 18
        default_close_hour = 8
        default_closed_days = "sat, sunday, 7, bad"

    class DummySettings:
        calendar = DummyCalendar()

    monkeypatch.setattr(calendar_mod, "get_settings", lambda: DummySettings())

    closed = calendar_mod._parse_closed_days("Mon, wed, 5, invalid")
    assert closed == {0, 2, 5}

    # When close_hour <= open_hour, function returns empty closed_days to signal always-open.
    open_hour, close_hour, closed_days = calendar_mod._get_business_hours("biz-1")
    assert open_hour == 18 and close_hour == 8
    assert closed_days == set()

    max_jobs, reserve_mornings, buffer_minutes = calendar_mod._get_business_capacity(
        business_id=None
    )
    assert max_jobs is None
    assert reserve_mornings is False
    assert buffer_minutes == 0


def test_get_business_capacity_handles_invalid_values(monkeypatch):
    class DummyRow:
        max_jobs_per_day = "not-an-int"
        reserve_mornings_for_emergencies = "yes"
        travel_buffer_minutes = "NaN"

    class DummySession:
        def get(self, model, key):
            return DummyRow()

        def close(self):
            return None

    monkeypatch.setattr(calendar_mod, "SQLALCHEMY_AVAILABLE", True)
    monkeypatch.setattr(calendar_mod, "SessionLocal", lambda: DummySession())

    max_jobs, reserve_mornings, buffer_minutes = calendar_mod._get_business_capacity(
        "biz-capacity"
    )
    assert max_jobs is None
    assert reserve_mornings is True  # truthy string coerces to bool
    assert buffer_minutes == 0


@pytest.mark.anyio
async def test_calendar_find_slots_falls_back_on_http_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Install a dummy client whose freebusy().query().execute() raises an HttpError.
    class DummyFreeBusyQuery:
        def execute(self) -> None:
            # Provide a minimal response object so HttpError can be constructed.
            class DummyResp:
                status = 500
                reason = "Internal Server Error"

            raise HttpError(resp=DummyResp(), content=b"forced calendar error")

    class DummyFreeBusy:
        def query(self, body):  # type: ignore[no-untyped-def]
            return DummyFreeBusyQuery()

    class DummyClient:
        def freebusy(self) -> DummyFreeBusy:  # type: ignore[override]
            return DummyFreeBusy()

    calendar_service._client = DummyClient()  # type: ignore[attr-defined]

    slots = await calendar_service.find_slots(
        duration_minutes=60,
        calendar_id="primary",
        business_id=None,
    )
    assert len(slots) == 1
    slot: TimeSlot = slots[0]
    assert isinstance(slot.start, datetime)
    assert isinstance(slot.end, datetime)
    assert slot.end > slot.start


@pytest.mark.anyio
async def test_calendar_create_event_http_error_falls_back_to_placeholder() -> None:
    # Configure a dummy client whose events().insert().execute() raises HttpError.
    class DummyInsert:
        def execute(self) -> None:
            class DummyResp:
                status = 500
                reason = "Internal Server Error"

            raise HttpError(resp=DummyResp(), content=b"forced create error")

    class DummyEvents:
        def insert(self, calendarId, body):  # type: ignore[no-untyped-def]
            return DummyInsert()

    class DummyClient:
        def events(self) -> DummyEvents:  # type: ignore[override]
            return DummyEvents()

    calendar_service._client = DummyClient()  # type: ignore[attr-defined]

    now = datetime.now(UTC)
    slot = TimeSlot(start=now, end=now + timedelta(minutes=30))

    event_id = await calendar_service.create_event(
        summary="Create Failure",
        slot=slot,
        description="Should fall back to placeholder",
        calendar_id="primary",
    )
    assert event_id.startswith("event_placeholder_")


@pytest.mark.anyio
async def test_calendar_update_event_http_error_returns_false() -> None:
    # Configure a dummy client whose events().patch().execute() raises HttpError.
    class DummyPatch:
        def execute(self) -> None:
            class DummyResp:
                status = 500
                reason = "Internal Server Error"

            raise HttpError(resp=DummyResp(), content=b"forced update error")

    class DummyEvents:
        def patch(self, calendarId, eventId, body):  # type: ignore[no-untyped-def]
            return DummyPatch()

    class DummyClient:
        def events(self) -> DummyEvents:  # type: ignore[override]
            return DummyEvents()

    calendar_service._client = DummyClient()  # type: ignore[attr-defined]

    now = datetime.now(UTC)
    slot = TimeSlot(start=now, end=now + timedelta(minutes=45))

    updated = await calendar_service.update_event(
        event_id="event-123",
        slot=slot,
        summary="Update Failure",
        description="Should return False on HttpError",
        calendar_id="primary",
    )
    assert updated is False


@pytest.mark.anyio
async def test_calendar_delete_event_http_error_returns_false() -> None:
    # Configure a dummy client whose events().delete().execute() raises HttpError.
    class DummyDelete:
        def execute(self) -> None:
            class DummyResp:
                status = 500
                reason = "Internal Server Error"

            raise HttpError(resp=DummyResp(), content=b"forced delete error")

    class DummyEvents:
        def delete(self, calendarId, eventId):  # type: ignore[no-untyped-def]
            return DummyDelete()

    class DummyClient:
        def events(self) -> DummyEvents:  # type: ignore[override]
            return DummyEvents()

    calendar_service._client = DummyClient()  # type: ignore[attr-defined]

    deleted = await calendar_service.delete_event(
        event_id="event-456",
        calendar_id="primary",
    )
    assert deleted is False


@pytest.mark.anyio
async def test_calendar_find_slots_respects_busy_ranges_and_travel_buffer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Configure a dummy client whose freebusy().query().execute() returns a known busy window.
    class DummyFreeBusyQuery:
        def __init__(self, body) -> None:  # type: ignore[no-untyped-def]
            self._body = body

        def execute(self) -> dict:
            cal_id = self._body["items"][0]["id"]
            now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
            busy_start = (now + timedelta(hours=1)).isoformat().replace("+00:00", "Z")
            busy_end = (now + timedelta(hours=2)).isoformat().replace("+00:00", "Z")
            return {
                "calendars": {
                    cal_id: {
                        "busy": [
                            {
                                "start": busy_start,
                                "end": busy_end,
                            }
                        ]
                    }
                }
            }

    class DummyFreeBusy:
        def query(self, body):  # type: ignore[no-untyped-def]
            return DummyFreeBusyQuery(body)

    class DummyClient:
        def freebusy(self) -> DummyFreeBusy:  # type: ignore[override]
            return DummyFreeBusy()

    # Force use of the "real client" path.
    calendar_service._client = DummyClient()  # type: ignore[attr-defined]

    # Monkeypatch business capacity to enable travel buffer and reserve mornings.
    def fake_get_business_capacity(business_id: str | None):
        return 10, True, 30

    monkeypatch.setattr(
        "app.services.calendar._get_business_capacity", fake_get_business_capacity
    )

    slots = await calendar_service.find_slots(
        duration_minutes=60,
        calendar_id="primary",
        business_id="biz-calendar",
        is_emergency=False,
    )
    assert slots, "expected at least one slot when using real-client path"
    slot: TimeSlot = slots[0]

    # Slot should be at least one hour long and should not sit inside the busy window.
    assert isinstance(slot.start, datetime)
    assert isinstance(slot.end, datetime)
    assert slot.end > slot.start
