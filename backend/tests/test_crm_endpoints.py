from datetime import datetime, timedelta, UTC

from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_crm_customer_and_appointment_flow():
    # Create customer
    cust_resp = client.post(
        "/v1/crm/customers",
        json={"name": "Test Customer", "phone": "555-4444", "email": "test@example.com"},
    )
    assert cust_resp.status_code == 200
    customer = cust_resp.json()
    customer_id = customer["id"]
    assert customer["name"] == "Test Customer"

    # List customers and ensure our customer is present
    list_resp = client.get("/v1/crm/customers")
    assert list_resp.status_code == 200
    customers = list_resp.json()
    assert any(c["id"] == customer_id for c in customers)

    # Create appointment
    start = datetime.now(UTC) + timedelta(days=1)
    end = start + timedelta(hours=1)
    appt_resp = client.post(
        "/v1/crm/appointments",
        json={
            "customer_id": customer_id,
            "start_time": start.isoformat(),
            "end_time": end.isoformat(),
            "service_type": "Leak repair",
            "is_emergency": False,
        },
    )
    assert appt_resp.status_code == 200
    appt = appt_resp.json()
    assert appt["customer_id"] == customer_id
    assert appt["service_type"] == "Leak repair"

    # List appointments for customer
    list_appt_resp = client.get(f"/v1/crm/customers/{customer_id}/appointments")
    assert list_appt_resp.status_code == 200
    appts = list_appt_resp.json()
    assert any(a["id"] == appt["id"] for a in appts)

    # List all appointments
    all_appts_resp = client.get("/v1/crm/appointments")
    assert all_appts_resp.status_code == 200
    all_appts = all_appts_resp.json()
    assert any(a["id"] == appt["id"] for a in all_appts)

    # Update appointment status via CRM PATCH and verify.
    patch_resp = client.patch(
        f"/v1/crm/appointments/{appt['id']}",
        json={"status": "CONFIRMED"},
    )
    assert patch_resp.status_code == 200
    patched = patch_resp.json()
    assert patched["status"] == "CONFIRMED"


def test_crm_list_customers_and_appointments_support_pagination():
    # Seed multiple customers and appointments.
    created_ids = []
    for i in range(5):
        resp = client.post(
            "/v1/crm/customers",
            json={"name": f"Paginated Customer {i}", "phone": f"555-77{i}"},
        )
        assert resp.status_code == 200
        created_ids.append(resp.json()["id"])

    # Fetch first page of customers.
    first_page = client.get("/v1/crm/customers", params={"limit": 2, "offset": 0})
    assert first_page.status_code == 200
    first_items = first_page.json()
    assert len(first_items) == 2

    # Second page should also have 2 items.
    second_page = client.get("/v1/crm/customers", params={"limit": 2, "offset": 2})
    assert second_page.status_code == 200
    second_items = second_page.json()
    assert len(second_items) == 2

    # For appointments, create a few appointments for the first customer.
    customer_id = created_ids[0]
    now = datetime.now(UTC)
    for i in range(3):
        start = now + timedelta(days=i + 1)
        end = start + timedelta(hours=1)
        resp = client.post(
            "/v1/crm/appointments",
            json={
                "customer_id": customer_id,
                "start_time": start.isoformat(),
                "end_time": end.isoformat(),
                "service_type": "Paged",
                "is_emergency": False,
            },
        )
        assert resp.status_code == 200

    appt_page = client.get("/v1/crm/appointments", params={"limit": 2, "offset": 0})
    assert appt_page.status_code == 200
    appts = appt_page.json()
    assert len(appts) == 2


def test_crm_customer_search_by_name_and_phone():
    # Create two customers with distinct names and phones.
    resp1 = client.post(
        "/v1/crm/customers",
        json={"name": "Alpha Plumbing", "phone": "555-0001"},
    )
    assert resp1.status_code == 200
    resp2 = client.post(
        "/v1/crm/customers",
        json={"name": "Beta Heating", "phone": "555-0002"},
    )
    assert resp2.status_code == 200

    # Search by partial name (case-insensitive).
    search_resp = client.get("/v1/crm/customers/search", params={"q": "alpha"})
    assert search_resp.status_code == 200
    results = search_resp.json()
    assert any(c["name"] == "Alpha Plumbing" for c in results)
    assert all("alpha" in c["name"].lower() for c in results)

    # Search by exact phone.
    phone_search = client.get("/v1/crm/customers/search", params={"q": "555-0002"})
    assert phone_search.status_code == 200
    phone_results = phone_search.json()
    assert any(c["phone"] == "555-0002" and c["name"] == "Beta Heating" for c in phone_results)


def test_crm_appointments_support_basic_filters():
    resp = client.post(
        "/v1/crm/customers",
        json={"name": "Filter Customer", "phone": "555-8888"},
    )
    assert resp.status_code == 200
    customer_id = resp.json()["id"]

    now = datetime.now(UTC)
    start1 = now + timedelta(days=1)
    end1 = start1 + timedelta(hours=1)
    start2 = now + timedelta(days=2)
    end2 = start2 + timedelta(hours=1)

    resp1 = client.post(
        "/v1/crm/appointments",
        json={
            "customer_id": customer_id,
            "start_time": start1.isoformat(),
            "end_time": end1.isoformat(),
            "service_type": "Leak",
            "is_emergency": True,
        },
    )
    assert resp1.status_code == 200
    appt1 = resp1.json()

    resp2 = client.post(
        "/v1/crm/appointments",
        json={
            "customer_id": customer_id,
            "start_time": start2.isoformat(),
            "end_time": end2.isoformat(),
            "service_type": "Inspection",
            "is_emergency": False,
        },
    )
    assert resp2.status_code == 200
    appt2 = resp2.json()

    emergency_only = client.get("/v1/crm/appointments", params={"is_emergency": "true"})
    assert emergency_only.status_code == 200
    emergency_items = emergency_only.json()
    assert any(a["id"] == appt1["id"] for a in emergency_items)
    assert all(a["is_emergency"] for a in emergency_items)

    leak_only = client.get(
        "/v1/crm/appointments",
        params={"service_type": "Leak"},
    )
    assert leak_only.status_code == 200
    leak_items = leak_only.json()
    assert any(a["id"] == appt1["id"] for a in leak_items)
    assert all(a["service_type"] == "Leak" for a in leak_items)
