from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.db import SQLALCHEMY_AVAILABLE, SessionLocal
from app.db_models import (
    AppointmentDB,
    BusinessDB,
    ConversationDB,
    ConversationMessageDB,
    CustomerDB,
)
from app.repositories import appointments_repo, customers_repo
from app.main import app


client = TestClient(app)

pytestmark = pytest.mark.skipif(
    not SQLALCHEMY_AVAILABLE,
    reason="Owner data management endpoints require database support",
)


def _ensure_business(session) -> BusinessDB:
    biz = session.get(BusinessDB, "default_business")
    if biz is None:
        biz = BusinessDB(  # type: ignore[call-arg]
            id="default_business",
            name="Default Biz",
            status="ACTIVE",
        )
        session.add(biz)
        session.commit()
        session.refresh(biz)
    return biz


def _seed_db_data() -> None:
    session = SessionLocal()
    try:
        biz = _ensure_business(session)
        # Clear existing related rows for a predictable assertion.
        session.query(ConversationMessageDB).filter(
            ConversationMessageDB.conversation_id == "conv-del"
        ).delete(synchronize_session=False)
        session.query(ConversationDB).filter(ConversationDB.id == "conv-del").delete(
            synchronize_session=False
        )
        session.query(AppointmentDB).filter(AppointmentDB.id == "appt-del").delete(
            synchronize_session=False
        )
        session.query(CustomerDB).filter(CustomerDB.id == "cust-del").delete(
            synchronize_session=False
        )
        session.commit()

        cust = CustomerDB(  # type: ignore[call-arg]
            id="cust-del",
            name="Delete Me",
            phone="555-DEL",
            business_id=biz.id,
        )
        session.add(cust)

        start = datetime.now(UTC) - timedelta(days=1)
        appt = AppointmentDB(  # type: ignore[call-arg]
            id="appt-del",
            customer_id=cust.id,
            business_id=biz.id,
            start_time=start,
            end_time=start + timedelta(hours=1),
            service_type="Cleanup",
            is_emergency=False,
        )
        session.add(appt)

        conv = ConversationDB(  # type: ignore[call-arg]
            id="conv-del",
            customer_id=cust.id,
            business_id=biz.id,
            channel="sms",
            created_at=start,
        )
        session.add(conv)

        msg = ConversationMessageDB(  # type: ignore[call-arg]
            id="msg-del",
            conversation_id=conv.id,
            role="user",
            text="Hello",
            timestamp=start,
        )
        session.add(msg)
        session.commit()
    finally:
        session.close()


def test_delete_tenant_data_requires_confirmation_phrase() -> None:
    resp = client.delete("/v1/owner/tenant-data", params={"confirm": "WRONG"})
    assert resp.status_code == 400


def test_delete_tenant_data_removes_rows() -> None:
    _seed_db_data()
    resp = client.delete("/v1/owner/tenant-data", params={"confirm": "DELETE"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["customers_deleted"] >= 1
    assert body["appointments_deleted"] >= 1
    assert body["conversations_deleted"] >= 1
    assert body["conversation_messages_deleted"] >= 1


def test_owner_onboarding_integrations_updates_flags() -> None:
    session = SessionLocal()
    try:
        _ensure_business(session)
    finally:
        session.close()

    payload = {
        "linkedin_connected": True,
        "gmail_connected": False,
        "gcalendar_connected": True,
        "openai_connected": True,
        "twilio_connected": True,
        "quickbooks_connected": False,
    }
    resp = client.patch("/v1/owner/onboarding/integrations", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    integrations = {
        item["provider"]: item["connected"] for item in body["integrations"]
    }
    assert integrations["linkedin"] is True
    assert integrations["gcalendar"] is True
    assert integrations["quickbooks"] is False


def test_owner_qbo_summary_pending_and_notify() -> None:
    assert SessionLocal is not None
    # Clear repo caches to ensure the pending list reflects test data.
    if hasattr(appointments_repo, "_by_business"):
        appointments_repo._by_business.clear()  # type: ignore[attr-defined]
    if hasattr(appointments_repo, "_by_customer"):
        appointments_repo._by_customer.clear()  # type: ignore[attr-defined]
    if hasattr(customers_repo, "_by_business"):
        customers_repo._by_business.clear()  # type: ignore[attr-defined]
    if hasattr(customers_repo, "_by_id"):
        customers_repo._by_id.clear()  # type: ignore[attr-defined]

    session = SessionLocal()
    try:
        biz = _ensure_business(session)
        biz.integration_qbo_status = "connected"  # type: ignore[assignment]
        biz.qbo_realm_id = "realm-123"  # type: ignore[assignment]
        biz.owner_phone = "+15550000000"  # type: ignore[assignment]
        session.add(biz)
        session.commit()
    finally:
        session.close()

    # Seed in-memory repos for appointments/customers used by owner endpoints.
    cust_model = customers_repo.upsert(
        name="QBO Customer", phone="555-QBO", business_id="default_business"
    )
    start = datetime.now(UTC) + timedelta(days=1)
    appt_model = appointments_repo.create(
        customer_id=cust_model.id,
        start_time=start,
        end_time=start + timedelta(hours=1),
        service_type="Install",
        is_emergency=False,
        description=None,
        lead_source=None,
        estimated_value=None,
        job_stage=None,
        business_id="default_business",
        quoted_value=2500,
        quote_status="PENDING_QBO",
    )

    pending_resp = client.get("/v1/owner/qbo/pending")
    assert pending_resp.status_code == 200
    pending = pending_resp.json()
    assert pending["total"] >= 1
    assert any(
        item["quote_status"] in {"PENDING_QBO", "PENDING", None}
        for item in pending["items"]
    )

    summary_resp = client.get("/v1/owner/qbo/summary")
    assert summary_resp.status_code == 200
    summary = summary_resp.json()
    assert summary["connected"] is True
    assert summary["realm_id"] == "realm-123"
    assert summary["pending_count"] >= 1

    from app.services.sms import sms_service

    before = len(sms_service.sent_messages)
    notify_resp = client.post("/v1/owner/qbo/notify", json={"send_sms": True})
    assert notify_resp.status_code == 200
    body = notify_resp.json()
    assert body["sms_sent"] is True
    assert len(sms_service.sent_messages) == before + 1
    assert str(summary["pending_count"]) in body["message"]
