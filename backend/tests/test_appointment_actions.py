from datetime import UTC, datetime, timedelta

import pytest

from app.deps import DEFAULT_BUSINESS_ID
from app.repositories import appointments_repo, conversations_repo, customers_repo
from app.services import appointment_actions


def _reset_inmemory_repos() -> None:
    for repo, attrs in (
        (
            customers_repo,
            ("_by_id", "_by_phone", "_by_business"),
        ),
        (
            appointments_repo,
            ("_by_id", "_by_customer", "_by_business"),
        ),
        (
            conversations_repo,
            ("_by_id", "_by_session", "_by_business"),
        ),
    ):
        for attr in attrs:
            if hasattr(repo, attr):
                getattr(repo, attr).clear()  # type: ignore[attr-defined]


@pytest.fixture(autouse=True)
def _isolate_repos() -> None:
    _reset_inmemory_repos()
    yield
    _reset_inmemory_repos()


@pytest.mark.anyio
async def test_cancel_appointment_not_found() -> None:
    result = await appointment_actions.cancel_appointment(
        appointment_id="missing",
        business_id=DEFAULT_BUSINESS_ID,
        actor="system",
    )
    assert result.code == "not_found"


@pytest.mark.anyio
async def test_cancel_appointment_already_cancelled_short_circuits(monkeypatch) -> None:
    sent_sms: list[tuple[str, str]] = []
    sent_email: list[tuple[str, str]] = []

    async def fake_sms(*, to: str, body: str, business_id: str, category: str) -> None:
        sent_sms.append((to, body))

    async def fake_email(*, to: str, subject: str, body: str, business_id: str) -> None:
        sent_email.append((to, body))

    monkeypatch.setattr(appointment_actions.sms_service, "send_sms", fake_sms)
    monkeypatch.setattr(appointment_actions.email_service, "send_email", fake_email)

    cust = customers_repo.upsert(
        name="Already Cancelled",
        phone="+15550000001",
        email="cancelled@example.com",
        business_id=DEFAULT_BUSINESS_ID,
    )
    start = datetime(2032, 1, 1, 12, 0, tzinfo=UTC)
    end = start + timedelta(hours=1)
    appt = appointments_repo.create(
        customer_id=cust.id,
        start_time=start,
        end_time=end,
        service_type="Repair",
        is_emergency=False,
        business_id=DEFAULT_BUSINESS_ID,
    )
    appointments_repo.update(appt.id, status="CANCELLED")

    result = await appointment_actions.cancel_appointment(
        appointment_id=appt.id,
        business_id=DEFAULT_BUSINESS_ID,
        actor="owner",
        notify_customer=True,
        conversation_id=None,
    )
    assert result.code == "already_cancelled"
    assert result.appointment_id == appt.id
    assert sent_sms == []
    assert sent_email == []


@pytest.mark.anyio
async def test_cancel_appointment_sets_cancelled_even_if_calendar_delete_fails(
    monkeypatch,
):
    monkeypatch.setattr(
        appointment_actions, "get_language_for_business", lambda _: "es"
    )
    monkeypatch.setattr(
        appointment_actions, "get_calendar_id_for_business", lambda _: "cal_123"
    )

    async def failing_delete_event(*args, **kwargs) -> None:
        raise RuntimeError("calendar down")

    monkeypatch.setattr(
        appointment_actions.calendar_service, "delete_event", failing_delete_event
    )

    sent_sms: list[tuple[str, str]] = []
    sent_email: list[tuple[str, str]] = []

    async def fake_sms(*, to: str, body: str, business_id: str, category: str) -> None:
        sent_sms.append((to, body))

    async def fake_email(*, to: str, subject: str, body: str, business_id: str) -> None:
        sent_email.append((to, body))

    monkeypatch.setattr(appointment_actions.sms_service, "send_sms", fake_sms)
    monkeypatch.setattr(appointment_actions.email_service, "send_email", fake_email)

    cust = customers_repo.upsert(
        name="Cancel Me",
        phone="+15550000002",
        email="cancelme@example.com",
        business_id=DEFAULT_BUSINESS_ID,
    )
    start = datetime(2032, 1, 2, 9, 0, tzinfo=UTC)
    end = start + timedelta(hours=2)
    appt = appointments_repo.create(
        customer_id=cust.id,
        start_time=start,
        end_time=end,
        service_type="Install",
        is_emergency=False,
        business_id=DEFAULT_BUSINESS_ID,
        calendar_event_id="evt_cancel_1",
    )
    conv = conversations_repo.create(
        channel="phone",
        customer_id=cust.id,
        session_id="sess_cancel_1",
        business_id=DEFAULT_BUSINESS_ID,
    )

    result = await appointment_actions.cancel_appointment(
        appointment_id=appt.id,
        business_id=DEFAULT_BUSINESS_ID,
        actor="owner",
        conversation_id=conv.id,
        reason="too late",
        notify_customer=True,
    )
    assert result.code == "cancelled"

    updated = appointments_repo.get(appt.id)
    assert updated is not None
    assert updated.status == "CANCELLED"
    assert updated.job_stage == "Cancelled"

    assert sent_sms
    assert "Tu cita programada" in sent_sms[0][1]
    assert "Reason: too late" in sent_sms[0][1]
    assert sent_email

    conv_after = conversations_repo.get(conv.id)
    assert conv_after is not None
    assert any(m.role == "action" and "cancel" in m.text for m in conv_after.messages)


@pytest.mark.anyio
async def test_cancel_appointment_customer_opt_out_skips_sms(monkeypatch) -> None:
    sent_sms: list[tuple[str, str]] = []
    sent_email: list[tuple[str, str]] = []

    async def fake_sms(*, to: str, body: str, business_id: str, category: str) -> None:
        sent_sms.append((to, body))

    async def fake_email(*, to: str, subject: str, body: str, business_id: str) -> None:
        sent_email.append((to, body))

    monkeypatch.setattr(appointment_actions.sms_service, "send_sms", fake_sms)
    monkeypatch.setattr(appointment_actions.email_service, "send_email", fake_email)

    cust = customers_repo.upsert(
        name="No SMS",
        phone="+15550000003",
        email="nosms@example.com",
        business_id=DEFAULT_BUSINESS_ID,
    )
    cust.sms_opt_out = True
    start = datetime(2032, 1, 3, 9, 0, tzinfo=UTC)
    end = start + timedelta(hours=1)
    appt = appointments_repo.create(
        customer_id=cust.id,
        start_time=start,
        end_time=end,
        service_type="Repair",
        is_emergency=False,
        business_id=DEFAULT_BUSINESS_ID,
    )

    result = await appointment_actions.cancel_appointment(
        appointment_id=appt.id,
        business_id=DEFAULT_BUSINESS_ID,
        actor="owner",
        notify_customer=True,
    )
    assert result.code == "cancelled"
    assert sent_sms == []
    assert sent_email


@pytest.mark.anyio
async def test_reschedule_appointment_invalid_range() -> None:
    cust = customers_repo.upsert(
        name="Range",
        phone="+15550000004",
        business_id=DEFAULT_BUSINESS_ID,
    )
    start = datetime(2032, 1, 4, 9, 0, tzinfo=UTC)
    end = start + timedelta(hours=1)
    appt = appointments_repo.create(
        customer_id=cust.id,
        start_time=start,
        end_time=end,
        service_type="Repair",
        is_emergency=False,
        business_id=DEFAULT_BUSINESS_ID,
    )
    result = await appointment_actions.reschedule_appointment(
        appointment_id=appt.id,
        business_id=DEFAULT_BUSINESS_ID,
        new_start=end,
        new_end=start,
        actor="owner",
    )
    assert result.code == "invalid_range"


@pytest.mark.anyio
async def test_reschedule_appointment_no_change_is_idempotent(monkeypatch) -> None:
    async def fake_update_event(*args, **kwargs) -> bool:
        raise AssertionError("calendar update should not occur for no_change")

    monkeypatch.setattr(
        appointment_actions.calendar_service, "update_event", fake_update_event
    )

    cust = customers_repo.upsert(
        name="No Change",
        phone="+15550000005",
        business_id=DEFAULT_BUSINESS_ID,
    )
    start = datetime(2032, 1, 5, 9, 0, tzinfo=UTC)
    end = start + timedelta(hours=1)
    appt = appointments_repo.create(
        customer_id=cust.id,
        start_time=start,
        end_time=end,
        service_type="Repair",
        is_emergency=False,
        business_id=DEFAULT_BUSINESS_ID,
        calendar_event_id="evt_no_change",
    )
    result = await appointment_actions.reschedule_appointment(
        appointment_id=appt.id,
        business_id=DEFAULT_BUSINESS_ID,
        new_start=start,
        new_end=end,
        actor="owner",
    )
    assert result.code == "no_change"


@pytest.mark.anyio
async def test_reschedule_appointment_conflict_does_not_touch_calendar(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        appointment_actions.calendar_service, "has_conflict", lambda **_: True
    )

    async def fake_update_event(*args, **kwargs) -> bool:
        raise AssertionError("calendar update should not occur for conflict")

    monkeypatch.setattr(
        appointment_actions.calendar_service, "update_event", fake_update_event
    )

    cust = customers_repo.upsert(
        name="Conflict",
        phone="+15550000006",
        business_id=DEFAULT_BUSINESS_ID,
    )
    start = datetime(2032, 1, 6, 9, 0, tzinfo=UTC)
    end = start + timedelta(hours=1)
    appt = appointments_repo.create(
        customer_id=cust.id,
        start_time=start,
        end_time=end,
        service_type="Repair",
        is_emergency=False,
        business_id=DEFAULT_BUSINESS_ID,
        calendar_event_id="evt_conflict",
    )

    new_start = start + timedelta(days=1)
    new_end = new_start + timedelta(hours=2)
    result = await appointment_actions.reschedule_appointment(
        appointment_id=appt.id,
        business_id=DEFAULT_BUSINESS_ID,
        new_start=new_start,
        new_end=new_end,
        actor="owner",
    )
    assert result.code == "conflict"


@pytest.mark.anyio
async def test_reschedule_appointment_calendar_failure_prevents_update(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        appointment_actions.calendar_service, "has_conflict", lambda **_: False
    )
    monkeypatch.setattr(
        appointment_actions, "get_calendar_id_for_business", lambda _: "cal_456"
    )

    async def fake_update_event(*args, **kwargs) -> bool:
        return False

    monkeypatch.setattr(
        appointment_actions.calendar_service, "update_event", fake_update_event
    )

    cust = customers_repo.upsert(
        name="Calendar Fail",
        phone="+15550000007",
        business_id=DEFAULT_BUSINESS_ID,
    )
    start = datetime(2032, 1, 7, 9, 0, tzinfo=UTC)
    end = start + timedelta(hours=1)
    appt = appointments_repo.create(
        customer_id=cust.id,
        start_time=start,
        end_time=end,
        service_type="Repair",
        is_emergency=False,
        business_id=DEFAULT_BUSINESS_ID,
        calendar_event_id="evt_cal_fail",
    )

    new_start = start + timedelta(days=1)
    new_end = new_start + timedelta(hours=2)
    result = await appointment_actions.reschedule_appointment(
        appointment_id=appt.id,
        business_id=DEFAULT_BUSINESS_ID,
        new_start=new_start,
        new_end=new_end,
        actor="owner",
    )
    assert result.code == "calendar_error"

    updated = appointments_repo.get(appt.id)
    assert updated is not None
    assert updated.start_time == start
    assert updated.end_time == end


@pytest.mark.anyio
async def test_reschedule_appointment_success_updates_and_notifies(monkeypatch) -> None:
    monkeypatch.setattr(
        appointment_actions.calendar_service, "has_conflict", lambda **_: False
    )
    monkeypatch.setattr(
        appointment_actions, "get_calendar_id_for_business", lambda _: "cal_789"
    )

    async def fake_update_event(*args, **kwargs) -> bool:
        return True

    monkeypatch.setattr(
        appointment_actions.calendar_service, "update_event", fake_update_event
    )

    sent_sms: list[tuple[str, str]] = []
    sent_email: list[tuple[str, str]] = []

    async def fake_sms(*, to: str, body: str, business_id: str, category: str) -> None:
        sent_sms.append((to, body))

    async def fake_email(*, to: str, subject: str, body: str, business_id: str) -> None:
        sent_email.append((to, body))

    monkeypatch.setattr(appointment_actions.sms_service, "send_sms", fake_sms)
    monkeypatch.setattr(appointment_actions.email_service, "send_email", fake_email)

    cust = customers_repo.upsert(
        name="Reschedule Me",
        phone="+15550000008",
        email="resched@example.com",
        business_id=DEFAULT_BUSINESS_ID,
    )
    start = datetime(2032, 1, 8, 9, 0, tzinfo=UTC)
    end = start + timedelta(hours=1)
    appt = appointments_repo.create(
        customer_id=cust.id,
        start_time=start,
        end_time=end,
        service_type="Repair",
        is_emergency=False,
        business_id=DEFAULT_BUSINESS_ID,
        calendar_event_id="evt_resched",
    )
    conv = conversations_repo.create(
        channel="phone",
        customer_id=cust.id,
        session_id="sess_resched_1",
        business_id=DEFAULT_BUSINESS_ID,
    )

    new_start = start + timedelta(days=2)
    new_end = new_start + timedelta(hours=2)
    result = await appointment_actions.reschedule_appointment(
        appointment_id=appt.id,
        business_id=DEFAULT_BUSINESS_ID,
        new_start=new_start,
        new_end=new_end,
        actor="owner",
        conversation_id=conv.id,
        notify_customer=True,
    )
    assert result.code == "rescheduled"

    updated = appointments_repo.get(appt.id)
    assert updated is not None
    assert updated.start_time == new_start
    assert updated.end_time == new_end
    assert updated.status == "SCHEDULED"
    assert updated.job_stage == "Rescheduled"

    assert sent_sms
    assert sent_email

    conv_after = conversations_repo.get(conv.id)
    assert conv_after is not None
    assert any(
        m.role == "action" and "reschedule" in m.text for m in conv_after.messages
    )


@pytest.mark.anyio
async def test_mark_pending_reschedule_is_idempotent_and_audited() -> None:
    cust = customers_repo.upsert(
        name="Pending",
        phone="+15550000009",
        business_id=DEFAULT_BUSINESS_ID,
    )
    start = datetime(2032, 1, 9, 9, 0, tzinfo=UTC)
    end = start + timedelta(hours=1)
    appt = appointments_repo.create(
        customer_id=cust.id,
        start_time=start,
        end_time=end,
        service_type="Repair",
        is_emergency=False,
        business_id=DEFAULT_BUSINESS_ID,
    )
    conv = conversations_repo.create(
        channel="phone",
        customer_id=cust.id,
        session_id="sess_pending_1",
        business_id=DEFAULT_BUSINESS_ID,
    )

    first = await appointment_actions.mark_pending_reschedule(
        appointment_id=appt.id,
        business_id=DEFAULT_BUSINESS_ID,
        actor="owner",
        conversation_id=conv.id,
    )
    assert first.code == "pending_reschedule"
    updated = appointments_repo.get(appt.id)
    assert updated is not None
    assert updated.status == "PENDING_RESCHEDULE"

    second = await appointment_actions.mark_pending_reschedule(
        appointment_id=appt.id,
        business_id=DEFAULT_BUSINESS_ID,
        actor="owner",
        conversation_id=conv.id,
    )
    assert second.code == "already_pending"

    conv_after = conversations_repo.get(conv.id)
    assert conv_after is not None
    assert any("reschedule_requested" in m.text for m in conv_after.messages)
