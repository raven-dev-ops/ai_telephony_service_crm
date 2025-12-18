from datetime import UTC, datetime, timedelta
import uuid

from fastapi.testclient import TestClient

from app.main import app
from app.db import SessionLocal
from app.db_models import BusinessDB, BusinessUserDB
from app.services import subscription as subscription_service
from app import config, deps
from app.metrics import CallbackItem, metrics


client = TestClient(app)


def _reset_settings_env(monkeypatch):
    monkeypatch.delenv("ENFORCE_SUBSCRIPTION", raising=False)
    monkeypatch.delenv("SUBSCRIPTION_GRACE_DAYS", raising=False)
    config.get_settings.cache_clear()
    deps.get_settings.cache_clear()


def test_invite_acceptance_creates_user(monkeypatch):
    monkeypatch.setenv("TESTING", "true")
    owner_email = f"owner-{uuid.uuid4().hex[:6]}@example.com"
    owner_pass = "OwnerPass!1"
    invitee_email = f"staff-{uuid.uuid4().hex[:6]}@example.com"

    # Register and login as owner to obtain a bearer token for invite creation.
    reg = client.post(
        "/v1/auth/register", json={"email": owner_email, "password": owner_pass}
    )
    assert reg.status_code == 200
    login = client.post(
        "/v1/auth/login", json={"email": owner_email, "password": owner_pass}
    )
    assert login.status_code == 200
    owner_access = login.json()["access_token"]

    invite = client.post(
        "/v1/owner/invites",
        headers={"Authorization": f"Bearer {owner_access}"},
        json={"email": invitee_email, "role": "staff"},
    )
    assert invite.status_code == 201
    token = invite.json().get("invite_token")
    assert token

    accept = client.post(
        "/v1/auth/invite/accept",
        json={"token": token, "password": "StaffPass!2", "name": "Staffer"},
    )
    assert accept.status_code == 200
    payload = accept.json()
    assert payload["user"]["email"] == invitee_email
    assert "staff" in payload["user"]["roles"]

    # Invite cannot be reused.
    reuse = client.post(
        "/v1/auth/invite/accept",
        json={"token": token, "password": "AnotherPass!3"},
    )
    assert reuse.status_code == 400
    monkeypatch.delenv("TESTING", raising=False)


def test_business_switch_via_refresh(monkeypatch):
    _reset_settings_env(monkeypatch)
    email = f"user-{uuid.uuid4().hex[:6]}@example.com"
    password = "SwitchPass!1"
    reg = client.post("/v1/auth/register", json={"email": email, "password": password})
    assert reg.status_code == 200
    login = client.post("/v1/auth/login", json={"email": email, "password": password})
    assert login.status_code == 200
    tokens = login.json()
    refresh = tokens["refresh_token"]
    user_id = tokens["user"]["id"]

    # Create a new business and membership for the user.
    new_business_id = f"biz_{uuid.uuid4().hex[:6]}"
    session = SessionLocal()
    try:
        biz = BusinessDB(
            id=new_business_id,
            name="Switch Co",
            api_key="switch_key",
            calendar_id="primary",
            status="ACTIVE",
        )
        session.add(biz)
        session.add(
            BusinessUserDB(
                id=uuid.uuid4().hex,
                business_id=new_business_id,
                user_id=user_id,
                role="admin",
            )
        )
        session.commit()
    finally:
        session.close()

    switched = client.post(
        "/v1/auth/refresh",
        json={"refresh_token": refresh, "business_id": new_business_id},
    )
    assert switched.status_code == 200
    switched_payload = switched.json()
    assert switched_payload["user"]["active_business_id"] == new_business_id

    me = client.get(
        "/v1/auth/me",
        headers={"Authorization": f"Bearer {switched_payload['access_token']}"},
    )
    assert me.status_code == 200
    assert me.json()["active_business_id"] == new_business_id


def test_subscription_blocks_when_inactive(monkeypatch):
    monkeypatch.setenv("ENFORCE_SUBSCRIPTION", "true")
    config.get_settings.cache_clear()
    deps.get_settings.cache_clear()
    session = SessionLocal()
    try:
        row = session.get(BusinessDB, "default_business")
        row.subscription_status = "canceled"
        row.subscription_current_period_end = datetime.now(UTC) - timedelta(days=10)
        session.add(row)
        session.commit()
    finally:
        session.close()

    resp = client.post("/v1/telephony/inbound", json={"caller_phone": "+10000000000"})
    assert resp.status_code == 402
    _reset_settings_env(monkeypatch)


def test_subscription_grace_allows_temporarily(monkeypatch):
    monkeypatch.setenv("ENFORCE_SUBSCRIPTION", "true")
    monkeypatch.setenv("SUBSCRIPTION_GRACE_DAYS", "5")
    config.get_settings.cache_clear()
    deps.get_settings.cache_clear()
    session = SessionLocal()
    try:
        row = session.get(BusinessDB, "default_business")
        row.subscription_status = "canceled"
        row.subscription_current_period_end = datetime.now(UTC) - timedelta(days=1)
        session.add(row)
        session.commit()
    finally:
        session.close()

    resp = client.post("/v1/telephony/inbound", json={"caller_phone": "+10000000000"})
    assert resp.status_code == 200
    _reset_settings_env(monkeypatch)


def test_plan_limit_blocks_calls(monkeypatch):
    monkeypatch.setenv("ENFORCE_SUBSCRIPTION", "true")
    config.get_settings.cache_clear()
    deps.get_settings.cache_clear()
    # Force a very low limit for starter to exercise the path.
    monkeypatch.setitem(
        subscription_service.PLAN_LIMITS,
        "starter",
        {"monthly_calls": 0, "monthly_appointments": 10},
    )
    metrics.voice_sessions_by_business.clear()
    session = SessionLocal()
    try:
        row = session.get(BusinessDB, "default_business")
        row.subscription_status = "active"
        row.service_tier = "starter"
        session.add(row)
        session.commit()
    finally:
        session.close()

    resp = client.post("/v1/telephony/inbound", json={"caller_phone": "+18885551212"})
    assert resp.status_code == 402
    _reset_settings_env(monkeypatch)


def test_subscription_reminder_sent_when_enforcement_disabled(monkeypatch):
    monkeypatch.delenv("ENFORCE_SUBSCRIPTION", raising=False)
    config.get_settings.cache_clear()
    deps.get_settings.cache_clear()
    # Clear reminder cache to avoid previous tests blocking notifications.
    from app.services import subscription as subscription_service

    subscription_service._reminder_cache.clear()  # type: ignore[attr-defined]

    sent = []

    class DummyEmail:
        async def notify_owner(self, subject, body, *, business_id, owner_email):
            sent.append((subject, body, business_id, owner_email))

    monkeypatch.setattr(subscription_service, "email_service", DummyEmail())

    session = SessionLocal()
    try:
        row = session.get(BusinessDB, "default_business")
        row.subscription_status = "past_due"
        row.subscription_current_period_end = datetime.now(UTC) - timedelta(days=1)
        row.owner_email = "owner@example.com"
        session.add(row)
        session.commit()
    finally:
        session.close()

    import asyncio

    state = asyncio.run(subscription_service.check_access("default_business"))
    assert state.status == "past_due"
    assert sent, "Owner reminder should be sent even when enforcement disabled"
    _reset_settings_env(monkeypatch)


def test_owner_callbacks_api(monkeypatch):
    metrics.callbacks_by_business.clear()
    # Seed a callback item as if missed call was recorded.
    now = datetime.now(UTC)
    metrics.callbacks_by_business.setdefault("default_business", {})["+15550000001"] = (
        CallbackItem(
            phone="+15550000001",
            first_seen=now,
            last_seen=now,
            count=1,
            reason="MISSED_CALL",
            status="PENDING",
        )
    )
    resp = client.get("/v1/owner/callbacks")
    assert resp.status_code == 200
    data = resp.json()
    assert data["callbacks"][0]["phone"] == "+15550000001"

    # Update status
    patch = client.patch(
        "/v1/owner/callbacks/%2B15550000001",
        json={"status": "RESOLVED", "last_result": "called back"},
    )
    assert patch.status_code == 200
    updated = patch.json()
    assert updated["status"] == "RESOLVED"
    assert updated["last_result"] == "called back"
