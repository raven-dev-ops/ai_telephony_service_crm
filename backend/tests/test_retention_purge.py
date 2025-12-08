from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.db import SQLALCHEMY_AVAILABLE, SessionLocal
from app.db_models import (
    AppointmentDB,
    BusinessDB,
    ConversationDB,
    ConversationMessageDB,
    RetentionPurgeLogDB,
)
from app.main import app
from app.metrics import metrics

client = TestClient(app)

pytestmark = pytest.mark.skipif(
    not SQLALCHEMY_AVAILABLE, reason="Retention purge requires database support"
)


def _cleanup_business(session, business_id: str) -> None:
    session.query(AppointmentDB).filter(
        AppointmentDB.business_id == business_id
    ).delete(synchronize_session=False)
    conv_ids = [
        row.id
        for row in session.query(ConversationDB.id)
        .filter(ConversationDB.business_id == business_id)
        .all()
    ]
    if conv_ids:
        session.query(ConversationMessageDB).filter(
            ConversationMessageDB.conversation_id.in_(conv_ids)
        ).delete(synchronize_session=False)
        session.query(ConversationDB).filter(ConversationDB.id.in_(conv_ids)).delete(
            synchronize_session=False
        )
    session.query(BusinessDB).filter(BusinessDB.id == business_id).delete(
        synchronize_session=False
    )
    session.commit()


def test_retention_prune_creates_log_and_keeps_recent_records() -> None:
    assert SessionLocal is not None
    session = SessionLocal()
    biz_id = "retention_log_biz"
    _cleanup_business(session, biz_id)

    now = datetime.now(UTC)
    biz = BusinessDB(  # type: ignore[call-arg]
        id=biz_id,
        name="Retention Log Biz",
        appointment_retention_days=15,
        conversation_retention_days=15,
        retention_enabled=True,
    )
    session.add(biz)

    old_time = now - timedelta(days=40)
    recent_time = now - timedelta(days=5)

    session.add(
        AppointmentDB(  # type: ignore[call-arg]
            id="appt-old-log",
            customer_id="cust-old",
            business_id=biz_id,
            start_time=old_time,
            end_time=old_time,
            service_type="Old Service",
            is_emergency=False,
        )
    )
    session.add(
        AppointmentDB(  # type: ignore[call-arg]
            id="appt-new-log",
            customer_id="cust-new",
            business_id=biz_id,
            start_time=recent_time,
            end_time=recent_time,
            service_type="Recent Service",
            is_emergency=False,
        )
    )

    session.add(
        ConversationDB(  # type: ignore[call-arg]
            id="conv-old-log",
            business_id=biz_id,
            customer_id="cust-old",
            channel="sms",
            created_at=old_time,
        )
    )
    session.add(
        ConversationMessageDB(  # type: ignore[call-arg]
            id="msg-old-log",
            conversation_id="conv-old-log",
            role="user",
            text="old message",
            timestamp=old_time,
        )
    )
    session.add(
        ConversationDB(  # type: ignore[call-arg]
            id="conv-new-log",
            business_id=biz_id,
            customer_id="cust-new",
            channel="sms",
            created_at=recent_time,
        )
    )
    session.add(
        ConversationMessageDB(  # type: ignore[call-arg]
            id="msg-new-log",
            conversation_id="conv-new-log",
            role="user",
            text="recent message",
            timestamp=recent_time,
        )
    )
    session.commit()
    session.close()

    # Reset metrics relevant to purge accounting.
    metrics.retention_purge_runs = 0
    metrics.retention_appointments_deleted = 0
    metrics.retention_conversations_deleted = 0
    metrics.retention_messages_deleted = 0

    resp = client.post("/v1/admin/retention/prune")
    assert resp.status_code == 200
    body = resp.json()
    assert body["appointments_deleted"] >= 1
    assert body["conversations_deleted"] >= 1
    assert body["conversation_messages_deleted"] >= 1
    assert body["log_id"] is not None

    session = SessionLocal()
    try:
        assert session.get(AppointmentDB, "appt-old-log") is None
        assert session.get(AppointmentDB, "appt-new-log") is not None
        assert session.get(ConversationDB, "conv-old-log") is None
        assert session.get(ConversationDB, "conv-new-log") is not None
        assert session.get(ConversationMessageDB, "msg-old-log") is None
        assert session.get(ConversationMessageDB, "msg-new-log") is not None

        log = (
            session.query(RetentionPurgeLogDB)
            .order_by(RetentionPurgeLogDB.id.desc())
            .first()
        )
        assert log is not None
        assert log.trigger in {"manual", "scheduled"}

        history_resp = client.get("/v1/admin/retention/history", params={"limit": 5})
        assert history_resp.status_code == 200
        history = history_resp.json()
        assert isinstance(history, list) and history
        assert any(entry["id"] == body["log_id"] for entry in history)

        assert metrics.retention_purge_runs >= 1
        assert metrics.retention_appointments_deleted >= 1
        assert metrics.retention_conversations_deleted >= 1
        assert metrics.retention_messages_deleted >= 1
    finally:
        _cleanup_business(session, biz_id)
        session.close()


def test_retention_prune_skips_disabled_tenant() -> None:
    assert SessionLocal is not None
    session = SessionLocal()
    biz_id = "retention_disabled_biz"
    _cleanup_business(session, biz_id)

    old_time = datetime.now(UTC) - timedelta(days=45)
    biz = BusinessDB(  # type: ignore[call-arg]
        id=biz_id,
        name="Retention Disabled",
        appointment_retention_days=10,
        conversation_retention_days=10,
        retention_enabled=False,
    )
    session.add(biz)
    session.add(
        AppointmentDB(  # type: ignore[call-arg]
            id="appt-disabled",
            customer_id="cust-disabled",
            business_id=biz_id,
            start_time=old_time,
            end_time=old_time,
            service_type="Old Service",
            is_emergency=False,
        )
    )
    session.add(
        ConversationDB(  # type: ignore[call-arg]
            id="conv-disabled",
            business_id=biz_id,
            customer_id="cust-disabled",
            channel="sms",
            created_at=old_time,
        )
    )
    session.add(
        ConversationMessageDB(  # type: ignore[call-arg]
            id="msg-disabled",
            conversation_id="conv-disabled",
            role="user",
            text="old message",
            timestamp=old_time,
        )
    )
    session.commit()
    session.close()

    resp = client.post("/v1/admin/retention/prune")
    assert resp.status_code == 200
    body = resp.json()
    # The disabled tenant should not be purged; totals may include other tenants, but
    # the specific rows must still exist.
    session = SessionLocal()
    try:
        assert session.get(AppointmentDB, "appt-disabled") is not None
        assert session.get(ConversationDB, "conv-disabled") is not None
        assert session.get(ConversationMessageDB, "msg-disabled") is not None
        assert body["appointments_deleted"] >= 0
    finally:
        _cleanup_business(session, biz_id)
        session.close()
