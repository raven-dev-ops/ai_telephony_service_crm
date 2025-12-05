from datetime import datetime, UTC, timedelta

from fastapi.testclient import TestClient

from app.main import app
from app.metrics import metrics
from app.repositories import customers_repo, appointments_repo
from app.deps import DEFAULT_BUSINESS_ID
from app.services import conversation


client = TestClient(app)


def test_twilio_sms_basic():
    resp = client.post(
        "/twilio/sms",
        data={"From": "+15550000000", "Body": "Hello"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert resp.status_code == 200
    body = resp.text
    assert "<Response>" in body
    assert "<Message>" in body


def test_twilio_sms_opt_out_sets_flag_and_confirms():
    # Ensure a clean customer repository (in-memory mode).
    customers_repo._by_id.clear()
    customers_repo._by_phone.clear()
    customers_repo._by_business.clear()

    phone = "+15550003333"
    customer = customers_repo.upsert(name="Opt Out Tester", phone=phone)
    assert getattr(customer, "sms_opt_out", False) is False

    resp = client.post(
        "/twilio/sms",
        data={"From": phone, "Body": "STOP"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert resp.status_code == 200
    body = resp.text
    # Should return a TwiML confirmation message, not a normal assistant reply.
    assert "<Response>" in body
    assert "<Message>" in body
    assert "opted out of SMS notifications" in body

    updated = customers_repo.get_by_phone(phone)
    assert updated is not None
    assert getattr(updated, "sms_opt_out", False) is True


def test_twilio_sms_opt_in_clears_flag_and_confirms():
    # Ensure a clean customer repository (in-memory mode).
    customers_repo._by_id.clear()
    customers_repo._by_phone.clear()
    customers_repo._by_business.clear()

    phone = "+15550004444"
    customer = customers_repo.upsert(name="Opt In Tester", phone=phone)
    # Mark customer as opted out first.
    customers_repo.set_sms_opt_out(phone, business_id="default_business", opt_out=True)
    opted_out = customers_repo.get_by_phone(phone)
    assert opted_out is not None
    assert getattr(opted_out, "sms_opt_out", False) is True

    resp = client.post(
        "/twilio/sms",
        data={"From": phone, "Body": "START"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert resp.status_code == 200
    body = resp.text
    # Should return a TwiML confirmation message, not a normal assistant reply.
    assert "<Response>" in body
    assert "<Message>" in body
    assert "opted back in to SMS notifications" in body

    updated = customers_repo.get_by_phone(phone)
    assert updated is not None
    assert getattr(updated, "sms_opt_out", False) is False


def test_twilio_sms_yes_confirms_next_appointment():
    # Ensure clean state.
    customers_repo._by_id.clear()
    customers_repo._by_phone.clear()
    customers_repo._by_business.clear()
    # Reset per-tenant SMS metrics so this test is isolated from others.
    metrics.sms_by_business.clear()

    phone = "+15550005555"
    customer = customers_repo.upsert(
        name="Confirm Tester", phone=phone, business_id="default_business"
    )
    now = datetime.now(UTC)
    start = now + timedelta(hours=2)
    end = start + timedelta(hours=1)
    appt = appointments_repo.create(
        customer_id=customer.id,
        start_time=start,
        end_time=end,
        service_type="Inspection",
        is_emergency=False,
        description="Test appointment",
        business_id="default_business",
        calendar_event_id=None,
    )

    resp = client.post(
        "/twilio/sms",
        data={"From": phone, "Body": "YES"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert resp.status_code == 200
    body = resp.text
    assert "<Response>" in body
    assert "<Message>" in body
    assert "confirmed" in body.lower()

    updated = appointments_repo.get(appt.id)
    assert updated is not None
    assert updated.status == "CONFIRMED"

    # Per-tenant metrics should track confirmations via SMS.
    per_sms = metrics.sms_by_business.get(DEFAULT_BUSINESS_ID)
    assert per_sms is not None
    assert per_sms.sms_confirmations_via_sms == 1


def test_twilio_sms_yes_with_no_upcoming_appointment_gives_clear_reply():
    # Clean state and no appointments.
    customers_repo._by_id.clear()
    customers_repo._by_phone.clear()
    customers_repo._by_business.clear()

    phone = "+15550006666"
    customers_repo.upsert(
        name="No Appt Tester", phone=phone, business_id="default_business"
    )

    resp = client.post(
        "/twilio/sms",
        data={"From": phone, "Body": "YES"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert resp.status_code == 200
    body = resp.text
    assert "<Response>" in body
    assert "<Message>" in body
    assert "could not find an upcoming appointment" in body.lower()


def test_twilio_sms_reschedule_marks_pending_reschedule():
    # Clean state.
    customers_repo._by_id.clear()
    customers_repo._by_phone.clear()
    customers_repo._by_business.clear()

    phone = "+15550007777"
    customer = customers_repo.upsert(
        name="Reschedule Tester", phone=phone, business_id="default_business"
    )
    now = datetime.now(UTC)
    start = now + timedelta(hours=4)
    end = start + timedelta(hours=1)
    appt = appointments_repo.create(
        customer_id=customer.id,
        start_time=start,
        end_time=end,
        service_type="Inspection",
        is_emergency=False,
        description="Reschedule test appointment",
        business_id="default_business",
        calendar_event_id=None,
    )

    resp = client.post(
        "/twilio/sms",
        data={"From": phone, "Body": "Reschedule"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert resp.status_code == 200
    body = resp.text
    assert "<Response>" in body
    assert "<Message>" in body
    assert "marked for rescheduling" in body.lower()

    updated = appointments_repo.get(appt.id)
    assert updated is not None
    assert updated.status == "PENDING_RESCHEDULE"

    # Per-tenant metrics should track reschedules via SMS.
    per_sms = metrics.sms_by_business.get(DEFAULT_BUSINESS_ID)
    assert per_sms is not None
    assert per_sms.sms_reschedules_via_sms == 1


def test_twilio_voice_greeting():
    resp = client.post(
        "/twilio/voice",
        data={"CallSid": "CA123", "From": "+15550000001"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert resp.status_code == 200
    body = resp.text
    assert "<Response>" in body
    assert "<Say" in body
    assert "<Gather" in body


def test_twilio_sms_unhandled_error_increments_metrics(monkeypatch):
    # Reset metrics and per-tenant stats.
    metrics.twilio_sms_requests = 0
    metrics.twilio_sms_errors = 0
    metrics.twilio_by_business.clear()

    async def failing_handle_input(session, text):
        raise RuntimeError("forced sms error")

    monkeypatch.setattr(
        conversation.conversation_manager, "handle_input", failing_handle_input
    )

    resp = client.post(
        "/twilio/sms",
        data={"From": "+15550009999", "Body": "Hello"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert resp.status_code == 200
    body = resp.text
    assert "something went wrong while handling your message" in body
    assert "call 911" in body.lower()

    assert metrics.twilio_sms_requests == 1
    assert metrics.twilio_sms_errors == 1
    per_tenant = metrics.twilio_by_business[DEFAULT_BUSINESS_ID]
    assert per_tenant.sms_requests == 1
    assert per_tenant.sms_errors == 1


def test_twilio_voice_unhandled_error_increments_metrics(monkeypatch):
    # Reset metrics and per-tenant stats.
    metrics.twilio_voice_requests = 0
    metrics.twilio_voice_errors = 0
    metrics.twilio_by_business.clear()

    async def failing_handle_input(session, text):
        raise RuntimeError("forced voice error")

    monkeypatch.setattr(
        conversation.conversation_manager, "handle_input", failing_handle_input
    )

    resp = client.post(
        "/twilio/voice",
        data={
            "CallSid": "CA_ERROR",
            "From": "+15550008888",
            "CallStatus": "in-progress",
            "SpeechResult": "Hello",
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert resp.status_code == 200
    body = resp.text
    assert "something went wrong while handling your call" in body
    assert "call 911" in body.lower()

    assert metrics.twilio_voice_requests == 1
    assert metrics.twilio_voice_errors == 1


def test_twilio_owner_voice_menu_and_schedule():
    # Reset Twilio voice metrics so this test can assert on them cleanly.
    metrics.twilio_voice_requests = 0
    metrics.twilio_voice_errors = 0
    metrics.twilio_by_business.clear()
    # Owner voice endpoint should present a menu, then read a schedule summary.
    resp = client.post(
        "/twilio/owner-voice",
        data={
            "CallSid": "CA_OWNER1",
            "From": "+15550001234",
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert resp.status_code == 200
    body = resp.text
    assert "<Response>" in body
    assert "<Gather" in body

    # Select option 1 (tomorrow's schedule); with no appointments, this should
    # yield a "no appointments" style message.
    resp2 = client.post(
        "/twilio/owner-voice",
        data={
            "CallSid": "CA_OWNER1",
            "From": "+15550001234",
            "Digits": "1",
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert resp2.status_code == 200
    body2 = resp2.text
    assert "<Response>" in body2
    assert "<Say" in body2
    # Both menu and selection calls are counted, so expect 2.
    per_tenant = metrics.twilio_by_business[DEFAULT_BUSINESS_ID]
    assert per_tenant.voice_requests == 2
    # Owner voice flows should not increment voice_errors in the happy path.
    assert per_tenant.voice_errors == 0
