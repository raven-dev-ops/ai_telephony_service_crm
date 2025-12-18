from fastapi.testclient import TestClient

from app.main import app
from app.repositories import (
    customers_repo,
    appointments_repo,
    conversations_repo,
)
from app.deps import DEFAULT_BUSINESS_ID


client = TestClient(app)


def reset_in_memory_repos():
    if hasattr(customers_repo, "_by_id"):
        customers_repo._by_id.clear()
        customers_repo._by_phone.clear()
        customers_repo._by_business.clear()
    if hasattr(appointments_repo, "_by_id"):
        appointments_repo._by_id.clear()
        appointments_repo._by_customer.clear()
        appointments_repo._by_business.clear()
    if hasattr(conversations_repo, "_by_id"):
        conversations_repo._by_id.clear()
        conversations_repo._by_session.clear()
        conversations_repo._by_business.clear()


def test_privacy_export_and_delete_flow(monkeypatch):
    # Ensure permissive auth for dashboard (no owner token needed)
    monkeypatch.delenv("OWNER_DASHBOARD_TOKEN", raising=False)
    reset_in_memory_repos()

    phone = "+15551234567"
    email = "user@example.com"
    cust = customers_repo.upsert(
        name="John Doe",
        phone=phone,
        email=email,
        address="123 Main St",
        business_id=DEFAULT_BUSINESS_ID,
    )
    appointments_repo.create(
        customer_id=cust.id,
        start_time=None,
        end_time=None,
        service_type="Leak Fix",
        is_emergency=False,
        description="Card 4111111111111111 and SSN 123-45-6789",
        business_id=DEFAULT_BUSINESS_ID,
    )
    conv = conversations_repo.create(
        channel="web", customer_id=cust.id, business_id=DEFAULT_BUSINESS_ID
    )
    conversations_repo.append_message(
        conv.id, role="user", text=f"My phone is {phone} and email {email}"
    )

    export = client.post(
        "/v1/owner/privacy/export",
        json={"customer_phone": phone},
        headers={"X-Business-ID": DEFAULT_BUSINESS_ID},
    )
    assert export.status_code == 200
    data = export.json()
    # Ensure PII is masked
    assert phone not in data["customer"]["phone"]
    assert email not in data["customer"]["email"]
    exported_msg = data["conversations"][0]["messages"][0]
    assert phone not in exported_msg and email not in exported_msg
    # Description redacted
    assert "411111" not in data["appointments"][0]["description"]
    assert "123-45-6789" not in data["appointments"][0]["description"]

    delete = client.post(
        "/v1/owner/privacy/delete",
        json={"customer_phone": phone},
        headers={"X-Business-ID": DEFAULT_BUSINESS_ID},
    )
    assert delete.status_code == 200
    # Customer should be gone from memory repos
    assert customers_repo.get_by_phone(phone, business_id=DEFAULT_BUSINESS_ID) is None
    assert not conversations_repo.list_for_customer(cust.id)
    assert not appointments_repo.list_for_customer(cust.id)
