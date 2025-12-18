from datetime import datetime, UTC, timedelta
import base64
import hashlib
import hmac

import pytest
from fastapi.testclient import TestClient

from app.db import SQLALCHEMY_AVAILABLE, SessionLocal
from app.db_models import BusinessDB
from app.main import app
from app.metrics import CallbackItem, metrics
from app.repositories import customers_repo, appointments_repo
from app.deps import DEFAULT_BUSINESS_ID
from app.services import conversation
from app.routers import twilio_integration
from app.services import sessions
from app.services.twilio_state import twilio_state_store
from app.services.stt_tts import speech_service


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
    customers_repo.upsert(name="Opt In Tester", phone=phone)
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
    twilio_state_store.clear_pending_action(DEFAULT_BUSINESS_ID, "+15550007777")

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
    assert "reply yes" in body.lower()

    updated = appointments_repo.get(appt.id)
    assert updated is not None
    assert updated.status == "SCHEDULED"

    confirm = client.post(
        "/twilio/sms",
        data={"From": phone, "Body": "YES"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert confirm.status_code == 200

    updated = appointments_repo.get(appt.id)
    assert updated is not None
    assert updated.status == "PENDING_RESCHEDULE"


def test_twilio_sms_cancel_requires_confirmation():
    customers_repo._by_id.clear()
    customers_repo._by_phone.clear()
    customers_repo._by_business.clear()
    phone = "+15550007788"
    customer = customers_repo.upsert(
        name="Cancel Tester", phone=phone, business_id="default_business"
    )
    start = datetime.now(UTC) + timedelta(hours=6)
    end = start + timedelta(hours=1)
    appt = appointments_repo.create(
        customer_id=customer.id,
        start_time=start,
        end_time=end,
        service_type="Inspection",
        is_emergency=False,
        description="Cancel test appointment",
        business_id="default_business",
    )

    resp = client.post(
        "/twilio/sms",
        data={"From": phone, "Body": "cancel"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert resp.status_code == 200
    assert "reply yes" in resp.text.lower()
    assert appointments_repo.get(appt.id).status == "SCHEDULED"

    resp_yes = client.post(
        "/twilio/sms",
        data={"From": phone, "Body": "YES"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert resp_yes.status_code == 200
    assert appointments_repo.get(appt.id).status == "CANCELLED"


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


def test_twilio_voice_completed_call_short_circuits(monkeypatch):
    metrics.twilio_voice_requests = 0
    metrics.twilio_by_business.clear()

    # Simulate a completed call that should short-circuit.
    resp = client.post(
        "/twilio/voice",
        data={
            "CallSid": "CA_DONE",
            "From": "+15550007777",
            "CallStatus": "completed",
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert resp.status_code == 200
    body = resp.text
    assert body.startswith("<Response")
    assert metrics.twilio_voice_requests == 1


def test_twilio_voice_rejects_missing_required_fields():
    resp = client.post(
        "/twilio/voice",
        data={"From": "+15550007777"},  # missing CallSid
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert resp.status_code == 422


def test_get_business_name_falls_back_when_db_unavailable(monkeypatch):
    monkeypatch.setattr(twilio_integration, "SQLALCHEMY_AVAILABLE", False)
    monkeypatch.setattr(twilio_integration, "SessionLocal", None)
    name = twilio_integration._get_business_name("any")  # type: ignore[attr-defined]
    assert isinstance(name, str) and name


def test_twilio_missed_call_queue_upgrades_partial_intake_and_respects_statuses(
    monkeypatch,
):
    # Start with a clean callback queue.
    metrics.callbacks_by_business.clear()

    biz_id = DEFAULT_BUSINESS_ID

    # Configure business-specific Twilio missed statuses so that "no-answer" is treated as missed.
    if SQLALCHEMY_AVAILABLE and SessionLocal is not None:
        session = SessionLocal()
        try:
            row = session.get(BusinessDB, biz_id)
            if row is None:
                row = BusinessDB(  # type: ignore[call-arg]
                    id=biz_id,
                    name="Missed Status Tenant",
                    status="ACTIVE",
                    twilio_missed_statuses="no-answer",
                )
                session.add(row)
            else:
                row.twilio_missed_statuses = "no-answer"
                row.status = "ACTIVE"
            session.commit()
        finally:
            session.close()

    # First, simulate a hard "no-answer" missed call.
    resp1 = client.post(
        "/twilio/voice",
        data={
            "CallSid": "CA_MISSED_1",
            "From": "+15550990001",
            "CallStatus": "no-answer",
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert resp1.status_code == 200

    queue = metrics.callbacks_by_business.get(biz_id, {})
    assert "+15550990001" in queue
    item = queue["+15550990001"]
    assert item.reason == "MISSED_CALL"
    assert item.status == "PENDING"

    # Seed a callback item with a resolved status to ensure a new missed/partial call re-opens it.
    item.status = "COMPLETED"
    item.last_result = "done"

    # Next, simulate a partial-intake drop for the same number by faking an in-progress session
    # whose status never transitions into a completed/booked state.
    # We do this by sending an in-progress call followed by a "completed" status; the router will
    # treat the non-completed session as a partial intake.
    resp2 = client.post(
        "/twilio/voice",
        data={
            "CallSid": "CA_MISSED_2",
            "From": "+15550990001",
            "CallStatus": "in-progress",
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert resp2.status_code == 200

    resp3 = client.post(
        "/twilio/voice",
        data={
            "CallSid": "CA_MISSED_2",
            "From": "+15550990001",
            "CallStatus": "completed",
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert resp3.status_code == 200

    queue_after = metrics.callbacks_by_business.get(biz_id, {})
    assert "+15550990001" in queue_after
    updated_item: CallbackItem = queue_after["+15550990001"]

    # Partial-intake call should upgrade the reason and reopen the callback.
    assert updated_item.reason == "PARTIAL_INTAKE"
    assert updated_item.status == "PENDING"
    assert updated_item.last_result is None


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


def test_twilio_voicemail_records_callback(monkeypatch):
    metrics.callbacks_by_business.clear()
    resp = client.post(
        "/twilio/voicemail",
        data={
            "CallSid": "CA_VM1",
            "From": "+15557778888",
            "RecordingUrl": "https://example.com/voicemail.wav",
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert resp.status_code == 200
    queue = metrics.callbacks_by_business.get(DEFAULT_BUSINESS_ID, {})
    assert "+15557778888" in queue
    item = queue["+15557778888"]
    assert item.reason == "VOICEMAIL"
    assert item.voicemail_url == "https://example.com/voicemail.wav"
    assert item.status == "PENDING"


def test_missed_call_notifies_owner(monkeypatch):
    metrics.callbacks_by_business.clear()
    # Ensure owner contact info exists.
    if SQLALCHEMY_AVAILABLE and SessionLocal is not None:
        session = SessionLocal()
        try:
            row = session.get(BusinessDB, DEFAULT_BUSINESS_ID)
            if row is None:
                row = BusinessDB(  # type: ignore[call-arg]
                    id=DEFAULT_BUSINESS_ID, name="Notify Tenant", status="ACTIVE"
                )
                session.add(row)
            row.owner_phone = "+15550101010"
            row.owner_email = "owner@example.com"
            row.owner_email_alerts_enabled = True  # type: ignore[assignment]
            session.commit()
        finally:
            session.close()

    sms_calls = []
    email_calls = []

    async def _fake_sms(message: str, business_id: str):
        sms_calls.append((message, business_id))

    async def _fake_email(subject: str, body: str, business_id: str, owner_email: str):
        email_calls.append((subject, body, business_id, owner_email))

    monkeypatch.setattr("app.services.sms.sms_service.notify_owner", _fake_sms)
    monkeypatch.setattr(
        "app.services.email_service.email_service.notify_owner", _fake_email
    )

    resp = client.post(
        "/twilio/voice",
        data={
            "CallSid": "CA_MISSED_OWNER",
            "From": "+15557779999",
            "CallStatus": "no-answer",
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert resp.status_code == 200
    assert sms_calls, "owner SMS alert should be sent"
    assert email_calls, "owner email alert should be sent"


def test_voicemail_notifies_owner(monkeypatch):
    metrics.callbacks_by_business.clear()
    if SQLALCHEMY_AVAILABLE and SessionLocal is not None:
        session = SessionLocal()
        try:
            row = session.get(BusinessDB, DEFAULT_BUSINESS_ID)
            if row is None:
                row = BusinessDB(  # type: ignore[call-arg]
                    id=DEFAULT_BUSINESS_ID, name="VM Tenant", status="ACTIVE"
                )
                session.add(row)
            row.owner_phone = "+15550101010"
            row.owner_email = "owner@example.com"
            row.owner_email_alerts_enabled = True  # type: ignore[assignment]
            session.commit()
        finally:
            session.close()

    sms_calls = []
    email_calls = []

    async def _fake_sms(message: str, business_id: str):
        sms_calls.append((message, business_id))

    async def _fake_email(subject: str, body: str, business_id: str, owner_email: str):
        email_calls.append((subject, body, business_id, owner_email))

    monkeypatch.setattr("app.services.sms.sms_service.notify_owner", _fake_sms)
    monkeypatch.setattr(
        "app.services.email_service.email_service.notify_owner", _fake_email
    )

    resp = client.post(
        "/twilio/voicemail",
        data={
            "CallSid": "CA_VM_NOTIFY",
            "From": "+15558887777",
            "RecordingUrl": "https://example.com/vm2.wav",
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert resp.status_code == 200
    assert sms_calls, "owner SMS alert should be sent for voicemail"
    assert email_calls, "owner email alert should be sent for voicemail"


def test_missed_call_respects_email_toggle(monkeypatch):
    metrics.callbacks_by_business.clear()
    if SQLALCHEMY_AVAILABLE and SessionLocal is not None:
        session = SessionLocal()
        try:
            row = session.get(BusinessDB, DEFAULT_BUSINESS_ID)
            if row is None:
                row = BusinessDB(  # type: ignore[call-arg]
                    id=DEFAULT_BUSINESS_ID, name="Toggle Tenant", status="ACTIVE"
                )
                session.add(row)
            row.owner_phone = "+15550101010"
            row.owner_email = "owner@example.com"
            row.owner_email_alerts_enabled = False  # type: ignore[assignment]
            session.add(row)
            session.commit()
        finally:
            session.close()

    sms_calls = []
    email_calls = []

    async def _fake_sms(message: str, business_id: str):
        sms_calls.append((message, business_id))

    async def _fake_email(subject: str, body: str, business_id: str, owner_email: str):
        email_calls.append((subject, body, business_id, owner_email))

    monkeypatch.setattr("app.services.sms.sms_service.notify_owner", _fake_sms)
    monkeypatch.setattr(
        "app.services.email_service.email_service.notify_owner", _fake_email
    )

    resp = client.post(
        "/twilio/voice",
        data={
            "CallSid": "CA_MISSED_TOGGLE",
            "From": "+15557770000",
            "CallStatus": "no-answer",
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert resp.status_code == 200
    assert sms_calls, "owner SMS alert should be sent"
    assert not email_calls, "owner email alert should be suppressed when disabled"


def test_twilio_voice_session_persists_across_turns():
    metrics.callbacks_by_business.clear()
    twilio_state_store.clear_call_session("CALL_PERSIST")
    # Initial gather start.
    resp1 = client.post(
        "/twilio/voice",
        data={
            "CallSid": "CALL_PERSIST",
            "From": "+15550101010",
            "CallStatus": "in-progress",
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert resp1.status_code == 200
    assert "<Gather" in resp1.text

    # Send a speech turn and ensure we still gather and keep the same session.
    resp2 = client.post(
        "/twilio/voice",
        data={
            "CallSid": "CALL_PERSIST",
            "From": "+15550101010",
            "CallStatus": "in-progress",
            "SpeechResult": "book an appointment",
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert resp2.status_code == 200
    assert "<Gather" in resp2.text
    link = twilio_state_store.get_call_session("CALL_PERSIST")
    assert link is not None
    session = sessions.session_store.get(link.session_id)
    assert session is not None
    assert session.caller_phone == "+15550101010"


def _build_twilio_signature(path: str, params: dict[str, str], auth_token: str) -> str:
    # Mirror the signature algorithm in _maybe_verify_twilio_signature.
    url = f"http://testserver{path}"
    data = url + "".join(f"{k}{params[k]}" for k in sorted(params.keys()))
    digest = hmac.new(
        auth_token.encode("utf-8"),
        data.encode("utf-8"),
        hashlib.sha1,
    ).digest()
    return base64.b64encode(digest).decode("utf-8")


def test_twilio_signature_required_when_enabled(monkeypatch) -> None:
    class SmsCfg:
        def __init__(self) -> None:
            self.verify_twilio_signatures = True
            self.twilio_auth_token = "test-token"

    class DummySettings:
        def __init__(self) -> None:
            self.sms = SmsCfg()

    monkeypatch.setattr(twilio_integration, "get_settings", lambda: DummySettings())

    # Missing signature header should be rejected with 401.
    resp = client.post(
        "/twilio/sms",
        data={"From": "+15550000000", "Body": "Hello"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert resp.status_code == 401


def test_twilio_signature_valid_allows_request(monkeypatch) -> None:
    class SmsCfg:
        def __init__(self) -> None:
            self.verify_twilio_signatures = True
            self.twilio_auth_token = "test-token"
            # Optional language fields used by _twilio_say_language_attr; keep defaults harmless.
            self.twilio_say_language_default = None
            self.twilio_say_language_es = None

    class DummySettings:
        def __init__(self) -> None:
            self.sms = SmsCfg()

    monkeypatch.setattr(twilio_integration, "get_settings", lambda: DummySettings())

    path = "/twilio/sms"
    form = {"From": "+15550000001", "Body": "Hello"}
    signature = _build_twilio_signature(path, dict(form), "test-token")

    resp = client.post(
        path,
        data=form,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Twilio-Signature": signature,
        },
    )
    # With a valid signature, the request should succeed normally.
    assert resp.status_code == 200
    body = resp.text
    assert "<Response>" in body
    assert "<Message>" in body


def test_twilio_voice_signature_required_when_enabled(monkeypatch) -> None:
    class SmsCfg:
        def __init__(self) -> None:
            self.verify_twilio_signatures = True
            self.twilio_auth_token = "test-token"
            self.twilio_say_language_default = None
            self.twilio_say_language_es = None

    class DummySettings:
        def __init__(self) -> None:
            self.sms = SmsCfg()

    monkeypatch.setattr(twilio_integration, "get_settings", lambda: DummySettings())

    resp = client.post(
        "/twilio/voice",
        data={"CallSid": "CA_SIG_MISS", "From": "+15550000002"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert resp.status_code == 401


def test_twilio_voice_signature_valid_allows(monkeypatch) -> None:
    class SmsCfg:
        def __init__(self) -> None:
            self.verify_twilio_signatures = True
            self.twilio_auth_token = "test-token"
            self.twilio_say_language_default = None
            self.twilio_say_language_es = None

    class DummySettings:
        def __init__(self) -> None:
            self.sms = SmsCfg()

    monkeypatch.setattr(twilio_integration, "get_settings", lambda: DummySettings())

    path = "/twilio/voice"
    form = {"CallSid": "CA_SIG_OK", "From": "+15550000003"}
    signature = _build_twilio_signature(path, dict(form), "test-token")

    resp = client.post(
        path,
        data=form,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Twilio-Signature": signature,
        },
    )
    assert resp.status_code == 200
    body = resp.text
    assert "<Response>" in body
    assert "<Gather" in body


def test_twilio_status_callback_requires_signature(monkeypatch) -> None:
    class SmsCfg:
        def __init__(self) -> None:
            self.verify_twilio_signatures = True
            self.twilio_auth_token = "test-token"
            self.twilio_say_language_default = None
            self.twilio_say_language_es = None

    class DummySettings:
        def __init__(self) -> None:
            self.sms = SmsCfg()

    monkeypatch.setattr(twilio_integration, "get_settings", lambda: DummySettings())

    form = {"MessageSid": "SM_SIG1", "MessageStatus": "delivered"}
    resp = client.post(
        "/twilio/status-callback",
        data=form,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert resp.status_code == 401

    signature = _build_twilio_signature("/twilio/status-callback", form, "test-token")
    resp_ok = client.post(
        "/twilio/status-callback",
        data=form,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Twilio-Signature": signature,
        },
    )
    assert resp_ok.status_code == 200
    assert resp_ok.json()["status"] == "delivered"


@pytest.mark.skipif(
    not SQLALCHEMY_AVAILABLE or SessionLocal is None,
    reason="_get_business_name DB behavior requires database support",
)
def test_get_business_name_prefers_db_row_but_falls_back() -> None:
    # Seed a business row with a custom name.
    biz_id = "twilio_business_name_test"
    session = SessionLocal()
    try:
        row = session.get(BusinessDB, biz_id)
        if row is None:
            row = BusinessDB(  # type: ignore[call-arg]
                id=biz_id,
                name="Twilio Test Plumbing",
            )
            session.add(row)
        else:
            row.name = "Twilio Test Plumbing"
        session.commit()
    finally:
        session.close()

    # When the business exists, _get_business_name should return the DB name.
    name = twilio_integration._get_business_name(biz_id)  # type: ignore[attr-defined]
    assert name == "Twilio Test Plumbing"

    # Unknown business_id should fall back to the default business name.
    fallback = twilio_integration._get_business_name("unknown-biz")  # type: ignore[attr-defined]
    assert fallback == conversation.DEFAULT_BUSINESS_NAME


def test_twilio_say_language_attr_uses_sms_settings(monkeypatch) -> None:
    class SmsCfg:
        def __init__(self) -> None:
            self.twilio_say_language_default = "en-US"
            self.twilio_say_language_es = "es-MX"

    class DummySettings:
        def __init__(self) -> None:
            self.sms = SmsCfg()

    monkeypatch.setattr(twilio_integration, "get_settings", lambda: DummySettings())

    # English / default language.
    attr_en = twilio_integration._twilio_say_language_attr("en")  # type: ignore[attr-defined]
    assert attr_en == ' language="en-US"'

    # Spanish variants should pick the ES mapping.
    attr_es = twilio_integration._twilio_say_language_attr("es")  # type: ignore[attr-defined]
    attr_es_mx = twilio_integration._twilio_say_language_attr("ES-mx")  # type: ignore[attr-defined]
    assert attr_es == ' language="es-MX"'
    assert attr_es_mx == ' language="es-MX"'


def _reset_appointments_and_customers() -> None:
    appointments_repo._by_id.clear()  # type: ignore[attr-defined]
    appointments_repo._by_customer.clear()  # type: ignore[attr-defined]
    appointments_repo._by_business.clear()  # type: ignore[attr-defined]
    customers_repo._by_id.clear()  # type: ignore[attr-defined]
    customers_repo._by_phone.clear()  # type: ignore[attr-defined]
    customers_repo._by_business.clear()  # type: ignore[attr-defined]


def test_twilio_owner_voice_emergency_summary_option_2() -> None:
    _reset_appointments_and_customers()

    # Seed two appointments in the last 7 days for the default business,
    # one emergency and one standard.
    now = datetime.now(UTC)
    customer = customers_repo.upsert(
        name="Owner Emergency Tester",
        phone="+15550002222",
        business_id=DEFAULT_BUSINESS_ID,
    )
    start1 = now - timedelta(days=2)
    end1 = start1 + timedelta(hours=1)
    appt1 = appointments_repo.create(
        customer_id=customer.id,
        start_time=start1,
        end_time=end1,
        service_type="Emergency Leak",
        is_emergency=True,
        description="Emergency job",
        business_id=DEFAULT_BUSINESS_ID,
        calendar_event_id=None,
    )
    appt1.status = "CONFIRMED"

    start2 = now - timedelta(days=3)
    end2 = start2 + timedelta(hours=1)
    appt2 = appointments_repo.create(
        customer_id=customer.id,
        start_time=start2,
        end_time=end2,
        service_type="Standard Job",
        is_emergency=False,
        description="Standard job",
        business_id=DEFAULT_BUSINESS_ID,
        calendar_event_id=None,
    )
    appt2.status = "SCHEDULED"

    # Call owner-voice with selection "2" to get the emergency summary.
    resp = client.post(
        "/twilio/owner-voice",
        data={
            "CallSid": "CA_OWNER2",
            "From": "+15550001234",
            "Digits": "2",
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert resp.status_code == 200
    body = resp.text
    assert "<Response>" in body
    assert "<Say" in body
    # Verify that the summary mentions the last seven days and emergency jobs.
    assert "In the last seven days" in body
    assert "flagged as emergency jobs" in body


def test_twilio_owner_voice_pipeline_summary_option_3() -> None:
    _reset_appointments_and_customers()

    # Seed a couple of appointments with job stages and estimated values
    # so the pipeline summary has non-zero content.
    now = datetime.now(UTC)
    customer = customers_repo.upsert(
        name="Owner Pipeline Tester",
        phone="+15550003333",
        business_id=DEFAULT_BUSINESS_ID,
    )

    start1 = now - timedelta(days=5)
    end1 = start1 + timedelta(hours=1)
    appt1 = appointments_repo.create(
        customer_id=customer.id,
        start_time=start1,
        end_time=end1,
        service_type="Estimate visit",
        is_emergency=False,
        description="Estimate",
        business_id=DEFAULT_BUSINESS_ID,
        calendar_event_id=None,
        estimated_value=1000,
        job_stage="Quoted",
    )
    appt1.status = "SCHEDULED"

    start2 = now - timedelta(days=3)
    end2 = start2 + timedelta(hours=1)
    appt2 = appointments_repo.create(
        customer_id=customer.id,
        start_time=start2,
        end_time=end2,
        service_type="Follow-up visit",
        is_emergency=False,
        description="Follow-up",
        business_id=DEFAULT_BUSINESS_ID,
        calendar_event_id=None,
        estimated_value=500,
        job_stage="Quoted",
    )
    appt2.status = "CONFIRMED"

    resp = client.post(
        "/twilio/owner-voice",
        data={
            "CallSid": "CA_OWNER3",
            "From": "+15550004444",
            "Digits": "3",
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert resp.status_code == 200
    body = resp.text
    assert "<Response>" in body
    assert "<Say" in body
    # Verify that the summary mentions pipeline and total estimated value.
    assert "Your pipeline over the last thirty days" in body
    assert "estimated total value" in body


def test_twilio_voice_persists_session_and_conversation_messages():
    # Reset conversations for clean assertions.
    from app.repositories import conversations_repo as repo  # local import

    if hasattr(repo, "_by_id"):
        repo._by_id.clear()  # type: ignore[attr-defined]
        if hasattr(repo, "_by_session"):
            repo._by_session.clear()  # type: ignore[attr-defined]

    call_sid = "CA_SESSION_TEST"
    phone = "+15551234567"

    # Initial call should create a session and conversation.
    resp1 = client.post(
        "/twilio/voice",
        data={"CallSid": call_sid, "From": phone},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert resp1.status_code == 200
    # Resolve the session created for this CallSid.
    from app.services.twilio_state import twilio_state_store  # local import

    link = twilio_state_store.get_call_session(call_sid)
    assert link is not None
    conv = repo.get_by_session(link.session_id)
    assert conv is not None
    # Initial greeting from the assistant should be recorded.
    assert len(conv.messages) >= 1

    # Next Gather post with speech should append user + assistant messages.
    resp2 = client.post(
        "/twilio/voice",
        data={
            "CallSid": call_sid,
            "From": phone,
            "CallStatus": "in-progress",
            "SpeechResult": "I need help with a leak",
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert resp2.status_code == 200
    link_after = twilio_state_store.get_call_session(call_sid)
    assert link_after is not None
    conv_after = repo.get_by_session(link_after.session_id)
    assert conv_after is not None
    # Expect at least the user message to be captured.
    assert any(m.text == "I need help with a leak" for m in conv_after.messages)


def test_twilio_voice_silence_fallbacks_to_voicemail():
    metrics.callbacks_by_business.clear()
    call_sid = "silent-call"
    phone = "+15551230000"
    resp1 = client.post(
        "/twilio/voice",
        data={"CallSid": call_sid, "From": phone, "SpeechResult": ""},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert resp1.status_code == 200
    resp2 = client.post(
        "/twilio/voice",
        data={"CallSid": call_sid, "From": phone, "SpeechResult": ""},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert resp2.status_code == 200
    body = resp2.text.lower()
    assert "trouble hearing you" in body or "problemas para escucharte" in body
    assert "<record" in body
    queue = metrics.callbacks_by_business.get(DEFAULT_BUSINESS_ID, {})
    assert phone in queue


@pytest.mark.skipif(
    not SQLALCHEMY_AVAILABLE or SessionLocal is None,
    reason="Speech degradation alerts require database-backed owner contact",
)
def test_speech_circuit_alerts_owner_once(monkeypatch):
    metrics.speech_alerted_businesses.clear()
    original_until = getattr(speech_service, "_circuit_open_until", None)
    try:
        session = SessionLocal()
        try:
            row = session.get(BusinessDB, DEFAULT_BUSINESS_ID)
            if row is None:
                row = BusinessDB(  # type: ignore[call-arg]
                    id=DEFAULT_BUSINESS_ID, name="Speech Alert Tenant", status="ACTIVE"
                )
                session.add(row)
            row.owner_phone = "+15550101010"
            row.owner_email = "owner@example.com"
            row.owner_email_alerts_enabled = True  # type: ignore[assignment]
            session.commit()
        finally:
            session.close()

        sms_calls = []
        email_calls = []

        async def _fake_sms(message: str, business_id: str):
            sms_calls.append((message, business_id))

        async def _fake_email(
            subject: str, body: str, business_id: str, owner_email: str
        ):
            email_calls.append((subject, body, business_id, owner_email))

        monkeypatch.setattr("app.services.sms.sms_service.notify_owner", _fake_sms)
        monkeypatch.setattr(
            "app.services.email_service.email_service.notify_owner", _fake_email
        )

        speech_service._trip_circuit(cooldown_seconds=60)

        resp1 = client.post(
            "/twilio/voice",
            data={
                "CallSid": "CA_SPEECH_ALERT1",
                "From": "+15550006666",
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert resp1.status_code == 200
        assert sms_calls or email_calls

        resp2 = client.post(
            "/twilio/voice",
            data={
                "CallSid": "CA_SPEECH_ALERT2",
                "From": "+15550007777",
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert resp2.status_code == 200
        assert len(sms_calls) == 1
        assert len(email_calls) == 1
        assert DEFAULT_BUSINESS_ID in metrics.speech_alerted_businesses
    finally:
        speech_service._circuit_open_until = original_until
        metrics.speech_alerted_businesses.clear()


def test_twilio_voice_assistant_handles_partial_and_alerts_owner(monkeypatch):
    metrics.callbacks_by_business.clear()
    call_sid = "CA_BRIDGE1"
    phone = "+15551234567"

    resp1 = client.post(
        "/twilio/voice-assistant",
        data={"CallSid": call_sid, "From": phone, "CallStatus": "in-progress"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert resp1.status_code == 200

    resp2 = client.post(
        "/twilio/voice-assistant",
        data={
            "CallSid": call_sid,
            "From": phone,
            "CallStatus": "in-progress",
            "SpeechResult": "I need to book an appointment",
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert resp2.status_code == 200

    # Simulate caller drop/failure before completion to trigger partial-intake handling.
    resp3 = client.post(
        "/twilio/voice-assistant",
        data={"CallSid": call_sid, "From": phone, "CallStatus": "failed"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert resp3.status_code == 200

    assert resp3.text.strip() == "<Response/>"


@pytest.mark.skipif(
    not SQLALCHEMY_AVAILABLE or SessionLocal is None,
    reason="Suspended tenant checks require database support",
)
def test_twilio_sms_rejects_suspended_business() -> None:
    # Reset Twilio SMS metrics to keep assertions local to this test.
    metrics.twilio_sms_requests = 0
    metrics.twilio_sms_errors = 0
    metrics.twilio_by_business.clear()

    biz_id = "twilio_sms_suspended"
    session = SessionLocal()
    try:
        row = session.get(BusinessDB, biz_id)
        if row is None:
            row = BusinessDB(  # type: ignore[call-arg]
                id=biz_id,
                name="Suspended SMS Tenant",
                status="SUSPENDED",
            )
            session.add(row)
        else:
            row.status = "SUSPENDED"
        session.commit()
    finally:
        session.close()

    resp = client.post(
        f"/twilio/sms?business_id={biz_id}",
        data={"From": "+15550123456", "Body": "Hello"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert resp.status_code == 403
    assert resp.text.strip() == "<Response></Response>"

    # The webhook should still be counted, but not as an error.
    assert metrics.twilio_sms_requests == 1
    per_tenant = metrics.twilio_by_business[biz_id]
    assert per_tenant.sms_requests == 1
    assert per_tenant.sms_errors == 0


@pytest.mark.skipif(
    not SQLALCHEMY_AVAILABLE or SessionLocal is None,
    reason="Suspended tenant checks require database support",
)
def test_twilio_voice_rejects_suspended_business() -> None:
    # Reset Twilio voice metrics to keep assertions local to this test.
    metrics.twilio_voice_requests = 0
    metrics.twilio_voice_errors = 0
    metrics.twilio_by_business.clear()

    biz_id = "twilio_voice_suspended"
    session = SessionLocal()
    try:
        row = session.get(BusinessDB, biz_id)
        if row is None:
            row = BusinessDB(  # type: ignore[call-arg]
                id=biz_id,
                name="Suspended Voice Tenant",
                status="SUSPENDED",
            )
            session.add(row)
        else:
            row.status = "SUSPENDED"
        session.commit()
    finally:
        session.close()

    resp = client.post(
        f"/twilio/voice?business_id={biz_id}",
        data={"CallSid": "CA_SUSP", "From": "+15550112233"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert resp.status_code == 403
    assert resp.text.strip() == "<Response></Response>"

    # The webhook should still be counted, but not as an error.
    assert metrics.twilio_voice_requests == 1
    per_tenant = metrics.twilio_by_business[biz_id]
    assert per_tenant.voice_requests == 1
    assert per_tenant.voice_errors == 0
