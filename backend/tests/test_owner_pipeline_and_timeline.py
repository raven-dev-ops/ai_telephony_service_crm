from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from app.main import app
from app.repositories import appointments_repo, customers_repo, conversations_repo


client = TestClient(app)


def test_owner_pipeline_returns_stages_and_totals():
    # Seed a customer and a couple of appointments with job stages.
    customer = customers_repo.upsert(
        name="Pipeline Customer",
        phone="+15551230000",
        email=None,
        address="100 Pipeline St",
        business_id="default_business",
    )
    now = datetime.now(UTC)
    start1 = now - timedelta(days=1)
    end1 = start1 + timedelta(hours=1)
    start2 = now - timedelta(days=2)
    end2 = start2 + timedelta(hours=2)
    appointments_repo.create(
        customer_id=customer.id,
        start_time=start1,
        end_time=end1,
        service_type="Inspection",
        is_emergency=False,
        description="Lead 1",
        lead_source="web",
        estimated_value=150,
        job_stage="Lead",
        business_id="default_business",
        calendar_event_id=None,
    )
    appointments_repo.create(
        customer_id=customer.id,
        start_time=start2,
        end_time=end2,
        service_type="Install",
        is_emergency=False,
        description="Booked job",
        lead_source="phone",
        estimated_value=500,
        job_stage="Booked",
        business_id="default_business",
        calendar_event_id=None,
    )

    resp = client.get(
        "/v1/owner/pipeline?days=30",
        headers={"X-Business-ID": "default_business"},
    )
    assert resp.status_code == 200
    data = resp.json()
    stages = {s["stage"]: s for s in data.get("stages", [])}
    assert "Lead" in stages or "Booked" in stages
    assert data.get("total_estimated_value", 0) >= 650 - 1  # allow float rounding


def test_customer_timeline_includes_appointments_and_conversations():
    # Seed a customer, appointment, and conversation.
    customer = customers_repo.upsert(
        name="Timeline Customer",
        phone="+15551239999",
        email=None,
        address="200 Timeline Ave",
        business_id="default_business",
    )
    now = datetime.now(UTC)
    start = now - timedelta(days=1)
    end = start + timedelta(hours=1)
    appointments_repo.create(
        customer_id=customer.id,
        start_time=start,
        end_time=end,
        service_type="Inspection",
        is_emergency=False,
        description="Timeline appt",
        business_id="default_business",
        calendar_event_id=None,
    )
    conv = conversations_repo.create(
        channel="phone",
        customer_id=customer.id,
        business_id="default_business",
    )

    resp = client.get(
        f"/v1/crm/customers/{customer.id}/timeline",
        headers={"X-Business-ID": "default_business"},
    )
    assert resp.status_code == 200
    items = resp.json()
    assert isinstance(items, list)
    types = {item["type"] for item in items}
    assert "appointment" in types
    assert "conversation" in types

