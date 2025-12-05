from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from app.main import app
from app.repositories import appointments_repo, customers_repo


client = TestClient(app)


def test_propose_slots_returns_candidate_for_existing_appointment():
    # Seed a customer and appointment in the default business.
    customer = customers_repo.upsert(
        name="Reschedule Candidate",
        phone="+15551234567",
        email=None,
        address="123 Demo St",
        business_id="default_business",
    )
    now = datetime.now(UTC)
    start = now + timedelta(days=1, hours=2)
    end = start + timedelta(hours=1)
    appt = appointments_repo.create(
        customer_id=customer.id,
        start_time=start,
        end_time=end,
        service_type="Inspection",
        is_emergency=False,
        description="Original appointment",
        business_id="default_business",
        calendar_event_id=None,
    )

    resp = client.post(
        f"/v1/crm/appointments/{appt.id}/propose-slots",
        headers={"X-Business-ID": "default_business"},
    )
    assert resp.status_code == 200
    slots = resp.json()
    assert isinstance(slots, list)
    assert slots, "expected at least one proposed slot"

    slot = slots[0]
    # Proposed slot should include start and end times.
    assert "start_time" in slot and "end_time" in slot

