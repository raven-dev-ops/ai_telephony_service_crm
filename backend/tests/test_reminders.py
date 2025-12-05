from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from app.main import app
from app.repositories import appointments_repo, customers_repo
from app.services.sms import sms_service


client = TestClient(app)


def test_send_upcoming_reminders_marks_appointments():
    # Clear repositories
    appointments_repo._by_id.clear()
    appointments_repo._by_customer.clear()
    appointments_repo._by_business.clear()
    customers_repo._by_id.clear()
    customers_repo._by_phone.clear()
    customers_repo._by_business.clear()
    sms_service._sent.clear()  # type: ignore[attr-defined]

    # Create customer and upcoming appointment directly in repos.
    customer = customers_repo.upsert(name="Reminder Test", phone="+15550002222")
    start = datetime.now(UTC) + timedelta(hours=2)
    end = start + timedelta(hours=1)
    appt = appointments_repo.create(
        customer_id=customer.id,
        start_time=start,
        end_time=end,
        service_type="Test",
        is_emergency=False,
        description="Reminder test",
    )
    assert appt.reminder_sent is False

    resp = client.post("/v1/reminders/send-upcoming", params={"hours_ahead": 24})
    assert resp.status_code == 200
    body = resp.json()
    assert body["reminders_sent"] == 1
    assert appt.reminder_sent is True

    sent = sms_service.sent_messages
    assert any(msg.to == customer.phone for msg in sent)


def test_send_upcoming_reminders_skips_opted_out_customers():
    # Clear repositories and SMS stub
    appointments_repo._by_id.clear()
    appointments_repo._by_customer.clear()
    appointments_repo._by_business.clear()
    customers_repo._by_id.clear()
    customers_repo._by_phone.clear()
    customers_repo._by_business.clear()
    sms_service._sent.clear()  # type: ignore[attr-defined]

    # Create two customers: one opted out, one opted in.
    customer_opt_out = customers_repo.upsert(
        name="Opted Out", phone="+15550002223"
    )
    customer_opt_in = customers_repo.upsert(
        name="Opted In", phone="+15550002224"
    )

    # Mark the first customer as opted out of SMS.
    customers_repo.set_sms_opt_out(
        customer_opt_out.phone, business_id="default_business", opt_out=True
    )

    start = datetime.now(UTC) + timedelta(hours=2)
    end = start + timedelta(hours=1)

    appt_opt_out = appointments_repo.create(
        customer_id=customer_opt_out.id,
        start_time=start,
        end_time=end,
        service_type="Test",
        is_emergency=False,
        description="Reminder opt-out test (should be skipped)",
    )
    appt_opt_in = appointments_repo.create(
        customer_id=customer_opt_in.id,
        start_time=start,
        end_time=end,
        service_type="Test",
        is_emergency=False,
        description="Reminder opt-in test (should send)",
    )

    resp = client.post("/v1/reminders/send-upcoming", params={"hours_ahead": 24})
    assert resp.status_code == 200
    body = resp.json()
    # Only the opted-in customer should receive a reminder.
    assert body["reminders_sent"] == 1

    assert appt_opt_out.reminder_sent is False
    assert appt_opt_in.reminder_sent is True

    sent = sms_service.sent_messages
    assert any(msg.to == customer_opt_in.phone for msg in sent)
    assert not any(msg.to == customer_opt_out.phone for msg in sent)


def test_send_upcoming_reminders_skips_cancelled_and_past_appointments():
    # Clear repositories and SMS stub
    appointments_repo._by_id.clear()
    appointments_repo._by_customer.clear()
    appointments_repo._by_business.clear()
    customers_repo._by_id.clear()
    customers_repo._by_phone.clear()
    customers_repo._by_business.clear()
    sms_service._sent.clear()  # type: ignore[attr-defined]

    customer = customers_repo.upsert(name="Status Tester", phone="+15550003333")

    now = datetime.now(UTC)

    # Past appointment (should be ignored).
    past_start = now - timedelta(hours=5)
    past_end = past_start + timedelta(hours=1)
    appointments_repo.create(
        customer_id=customer.id,
        start_time=past_start,
        end_time=past_end,
        service_type="Test",
        is_emergency=False,
        description="Past appointment",
    )

    # Future cancelled appointment (should be ignored).
    future_start = now + timedelta(hours=2)
    future_end = future_start + timedelta(hours=1)
    cancelled = appointments_repo.create(
        customer_id=customer.id,
        start_time=future_start,
        end_time=future_end,
        service_type="Test",
        is_emergency=False,
        description="Cancelled appointment",
    )
    cancelled.status = "CANCELLED"

    resp = client.post("/v1/reminders/send-upcoming", params={"hours_ahead": 24})
    assert resp.status_code == 200
    body = resp.json()
    # No reminders should be sent because the only future appointment is cancelled.
    assert body["reminders_sent"] == 0
    sent = sms_service.sent_messages
    assert sent == []
