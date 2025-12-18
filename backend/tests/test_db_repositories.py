from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.db import SQLALCHEMY_AVAILABLE, SessionLocal
from app.db_models import ConversationDB
from app.repositories import (
    DbAppointmentRepository,
    DbConversationRepository,
    DbCustomerRepository,
)


pytestmark = pytest.mark.skipif(
    not SQLALCHEMY_AVAILABLE or SessionLocal is None,
    reason="DB-backed repositories require database support",
)


def test_db_customer_repository_upsert_and_sms_opt_out() -> None:
    repo = DbCustomerRepository()
    business_id = "db_repo_test_business"
    phone = "+19995551234"

    customer = repo.upsert(
        name="DB Repo Customer",
        phone=phone,
        email="db@example.com",
        address="123 DB St",
        business_id=business_id,
        tags=["vip", "repeat"],
    )
    assert customer.business_id == business_id
    assert customer.phone == phone
    assert sorted(customer.tags) == ["repeat", "vip"]

    fetched = repo.get(customer.id)
    assert fetched is not None
    assert fetched.id == customer.id

    by_phone = repo.get_by_phone(phone, business_id=business_id)
    assert by_phone is not None
    assert by_phone.id == customer.id

    repo.set_sms_opt_out(phone=phone, business_id=business_id, opt_out=True)
    updated = repo.get_by_phone(phone, business_id=business_id)
    assert updated is not None
    assert updated.sms_opt_out is True

    # Listing APIs and missing lookups.
    all_customers = repo.list_all()
    assert any(c.id == customer.id for c in all_customers)
    business_customers = repo.list_for_business(business_id)
    assert business_customers and business_customers[0].business_id == business_id
    assert repo.get_by_phone("missing", business_id=business_id) is None


def test_db_appointment_repository_create_and_update() -> None:
    repo = DbAppointmentRepository()
    business_id = "db_repo_test_business"
    customer_id = "cust-db-1"
    now = datetime.now(UTC)
    end = now + timedelta(hours=1)
    event_id = f"evt_db_{uuid4()}"

    appt = repo.create(
        customer_id=customer_id,
        start_time=now,
        end_time=end,
        service_type="Inspection",
        is_emergency=False,
        description="Initial",
        lead_source="phone",
        estimated_value=100,
        job_stage="New",
        business_id=business_id,
        calendar_event_id=event_id,
        tags=["tag1", "tag2"],
        technician_id="tech-1",
        quoted_value=200,
        quote_status="PROPOSED",
    )
    assert appt.business_id == business_id
    assert appt.customer_id == customer_id
    assert appt.status == "SCHEDULED"
    assert sorted(appt.tags) == ["tag1", "tag2"]

    fetched = repo.get(appt.id)
    assert fetched is not None
    assert fetched.id == appt.id
    by_event = repo.find_by_calendar_event(event_id, business_id=business_id)
    assert by_event is not None
    assert by_event.id == appt.id

    updated = repo.update(
        appt.id,
        description="Updated description",
        is_emergency=True,
        status="CONFIRMED",
        tags=["tag3"],
        quoted_value=250,
        quote_status="APPROVED",
    )
    assert updated is not None
    assert updated.description == "Updated description"
    assert updated.is_emergency is True
    assert updated.status == "CONFIRMED"
    assert updated.quoted_value == 250
    assert updated.quote_status == "APPROVED"
    assert updated.tags == ["tag3"]

    assert any(a.id == appt.id for a in repo.list_all())
    assert any(a.id == appt.id for a in repo.list_for_business(business_id))
    assert repo.update("missing-id", status="CANCELLED") is None


def test_db_conversation_repository_create_append_and_get() -> None:
    repo = DbConversationRepository()
    business_id = "db_repo_test_business"
    session_id = "sess-db-1"

    # Ensure the test session_id is unique for this test run by removing
    # any existing rows that might use it from prior runs.
    session = SessionLocal()
    try:
        session.query(ConversationDB).filter(
            ConversationDB.session_id == session_id
        ).delete(synchronize_session=False)
        session.commit()
    finally:
        session.close()

    conv = repo.create(
        channel="sms",
        customer_id="cust-db-1",
        session_id=session_id,
        business_id=business_id,
    )
    assert conv.business_id == business_id
    assert conv.session_id == session_id
    assert conv.messages == []

    repo.append_message(conv.id, role="user", text="Hello")
    repo.append_message(conv.id, role="assistant", text="Hi there")
    repo.append_message("missing", role="user", text="ignore")

    by_session = repo.get_by_session(session_id)
    assert by_session is not None
    assert by_session.id == conv.id
    assert len(by_session.messages) == 2
    assert by_session.messages[0].role == "user"
    assert by_session.messages[1].role == "assistant"

    by_business = repo.list_for_business(business_id)
    assert any(c.id == conv.id for c in by_business)

    all_convs = repo.list_all()
    assert any(c.id == conv.id for c in all_convs)
