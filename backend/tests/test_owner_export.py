from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from app.main import app
from app.repositories import appointments_repo, customers_repo, conversations_repo


client = TestClient(app)


def test_owner_export_service_mix_csv_last_30_days():
    appointments_repo._by_id.clear()
    appointments_repo._by_customer.clear()
    appointments_repo._by_business.clear()
    customers_repo._by_id.clear()
    customers_repo._by_phone.clear()
    customers_repo._by_business.clear()

    # Create a customer and one recent appointment plus one old appointment.
    cust_resp = client.post(
        "/v1/crm/customers",
        json={"name": "Export Owner", "phone": "555-2222"},
    )
    customer_id = cust_resp.json()["id"]

    now = datetime.now(UTC)

    # Recent appointment (within 30 days).
    start_recent = now - timedelta(days=3)
    end_recent = start_recent + timedelta(hours=1)
    client.post(
        "/v1/crm/appointments",
        json={
            "customer_id": customer_id,
            "start_time": start_recent.isoformat(),
            "end_time": end_recent.isoformat(),
            "service_type": "tankless_water_heater",
            "is_emergency": True,
            "description": "Recent emergency job",
        },
    )

    # Old appointment (outside 30 days).
    start_old = now - timedelta(days=45)
    end_old = start_old + timedelta(hours=1)
    client.post(
        "/v1/crm/appointments",
        json={
            "customer_id": customer_id,
            "start_time": start_old.isoformat(),
            "end_time": end_old.isoformat(),
            "service_type": "drain_or_sewer",
            "is_emergency": False,
            "description": "Old standard job",
        },
    )

    resp = client.get("/v1/owner/export/service-mix.csv")
    assert resp.status_code == 200
    assert resp.headers.get("content-type", "").startswith("text/csv")

    text = resp.text
    lines = [line for line in text.splitlines() if line.strip()]
    # Header plus exactly one data row for the recent appointment.
    assert len(lines) == 2
    header = lines[0].split(",")
    row = lines[1].split(",")
    assert header == ["service_type", "start_time", "is_emergency"]
    assert row[0] == "tankless_water_heater"
    assert row[2] == "true"


def test_owner_export_conversations_csv_last_30_days():
    appointments_repo._by_id.clear()
    appointments_repo._by_customer.clear()
    appointments_repo._by_business.clear()
    customers_repo._by_id.clear()
    customers_repo._by_phone.clear()
    customers_repo._by_business.clear()
    # Clear conversations when using in-memory repo.
    if hasattr(conversations_repo, "_by_id"):
        conversations_repo._by_id.clear()
        conversations_repo._by_session.clear()
        conversations_repo._by_business.clear()

    # Create a customer and one recent appointment and conversation.
    cust_resp = client.post(
        "/v1/crm/customers",
        json={"name": "Conversation Owner", "phone": "555-3333"},
    )
    customer_id = cust_resp.json()["id"]

    now = datetime.now(UTC)

    start_recent = now - timedelta(days=3)
    end_recent = start_recent + timedelta(hours=1)
    client.post(
        "/v1/crm/appointments",
        json={
            "customer_id": customer_id,
            "start_time": start_recent.isoformat(),
            "end_time": end_recent.isoformat(),
            "service_type": "drain_or_sewer",
            "is_emergency": False,
            "description": "Recent job",
        },
    )

    # Create a conversation linked to this customer and business.
    conv = conversations_repo.create(
        channel="phone",
        customer_id=customer_id,
        business_id="default_business",
    )

    resp = client.get("/v1/owner/export/conversations.csv")
    assert resp.status_code == 200
    assert resp.headers.get("content-type", "").startswith("text/csv")

    text = resp.text
    lines = [line for line in text.splitlines() if line.strip()]
    # Header plus exactly one data row for the recent conversation.
    assert len(lines) == 2
    header = lines[0].split(",")
    row = lines[1].split(",")
    assert header[0] == "id"
    assert "service_type" in header
    # service_type column should reflect the appointment service_type.
    service_type_index = header.index("service_type")
    assert row[service_type_index] == "drain_or_sewer"


def test_owner_export_conversion_funnel_csv():
    appointments_repo._by_id.clear()
    appointments_repo._by_customer.clear()
    appointments_repo._by_business.clear()
    customers_repo._by_id.clear()
    customers_repo._by_phone.clear()
    customers_repo._by_business.clear()
    if hasattr(conversations_repo, "_by_id"):
        conversations_repo._by_id.clear()
        conversations_repo._by_session.clear()
        conversations_repo._by_business.clear()

    phone_resp = client.post(
        "/v1/crm/customers",
        json={"name": "Phone Lead Export", "phone": "555-4444"},
    )
    phone_customer_id = phone_resp.json()["id"]
    web_resp = client.post(
        "/v1/crm/customers",
        json={"name": "Web Lead Export", "phone": "555-5555"},
    )
    web_customer_id = web_resp.json()["id"]

    now = datetime.now(UTC)
    first_contact_phone = now - timedelta(days=7)
    first_contact_web = now - timedelta(days=4)

    conv_phone = conversations_repo.create(
        channel="phone",
        customer_id=phone_customer_id,
        business_id="default_business",
    )
    if hasattr(conv_phone, "created_at"):
        conv_phone.created_at = first_contact_phone

    conv_web = conversations_repo.create(
        channel="web",
        customer_id=web_customer_id,
        business_id="default_business",
    )
    if hasattr(conv_web, "created_at"):
        conv_web.created_at = first_contact_web

    start_phone = first_contact_phone + timedelta(days=1)
    end_phone = start_phone + timedelta(hours=1)
    client.post(
        "/v1/crm/appointments",
        json={
            "customer_id": phone_customer_id,
            "start_time": start_phone.isoformat(),
            "end_time": end_phone.isoformat(),
            "service_type": "Inspection",
            "is_emergency": False,
            "description": "Booked from phone lead (export)",
        },
    )

    resp = client.get("/v1/owner/export/conversion-funnel.csv", params={"days": 90})
    assert resp.status_code == 200
    assert resp.headers.get("content-type", "").startswith("text/csv")

    text = resp.text
    lines = [line for line in text.splitlines() if line.strip()]
    # Header plus one row for phone and one for web.
    assert len(lines) == 3
    header = lines[0].split(",")
    assert header == [
        "channel",
        "leads",
        "booked_appointments",
        "conversion_rate",
        "average_time_to_book_minutes",
    ]

    # Rows are sorted by channel name.
    phone_row = lines[1].split(",")
    web_row = lines[2].split(",")

    assert phone_row[0] == "phone"
    assert phone_row[1] == "1"
    assert phone_row[2] == "1"
    assert float(phone_row[3]) == 1.0
    assert float(phone_row[4]) > 0.0

    assert web_row[0] == "web"
    assert web_row[1] == "1"
    assert web_row[2] == "0"
    assert float(web_row[3]) == 0.0
    assert float(web_row[4]) == 0.0
