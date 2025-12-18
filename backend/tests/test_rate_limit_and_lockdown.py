from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app import config, deps, main
from app.db import SQLALCHEMY_AVAILABLE, SessionLocal
from app.db_models import BusinessDB
from app.metrics import metrics


def _fresh_client(monkeypatch: pytest.MonkeyPatch, env: dict[str, str]) -> TestClient:
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    config.get_settings.cache_clear()
    deps.get_settings.cache_clear()
    app = main.create_app()
    return TestClient(app)


def _ensure_business():
    if not (SQLALCHEMY_AVAILABLE and SessionLocal is not None):
        return
    session = SessionLocal()
    try:
        row = session.get(BusinessDB, "default_business")
        if row is None:
            row = BusinessDB(  # type: ignore[call-arg]
                id="default_business", name="Default", status="ACTIVE"
            )
            session.add(row)
        session.commit()
    finally:
        session.close()


def test_rate_limit_blocks_after_burst(monkeypatch):
    metrics.rate_limit_blocks_total = 0
    metrics.rate_limit_blocks_by_route.clear()
    metrics.rate_limit_blocks_by_route_business.clear()
    client = _fresh_client(
        monkeypatch,
        {
            "RATE_LIMIT_PER_MINUTE": "1",
            "RATE_LIMIT_BURST": "1",
            "RATE_LIMIT_DISABLED": "false",
        },
    )
    _ensure_business()
    first = client.post("/v1/widget/start", json={})
    assert first.status_code == 200

    second = client.post("/v1/widget/start", json={})
    assert second.status_code == 429
    assert "Retry-After" in second.headers
    assert metrics.rate_limit_blocks_total >= 1
    assert metrics.rate_limit_blocks_by_route.get("/v1/widget", 0) >= 1


def test_rate_limit_whitelist_ips_allows_requests(monkeypatch):
    client = _fresh_client(
        monkeypatch,
        {
            "RATE_LIMIT_PER_MINUTE": "1",
            "RATE_LIMIT_BURST": "1",
            "RATE_LIMIT_DISABLED": "false",
            "RATE_LIMIT_WHITELIST_IPS": "testclient",
        },
    )
    _ensure_business()
    for _ in range(3):
        resp = client.post("/v1/widget/start", json={})
        assert resp.status_code == 200


@pytest.mark.skipif(
    not (SQLALCHEMY_AVAILABLE and SessionLocal is not None),
    reason="Self-signup requires database support",
)
def test_public_signup_is_rate_limited(monkeypatch):
    client = _fresh_client(
        monkeypatch,
        {
            "ALLOW_SELF_SIGNUP": "true",
            "RATE_LIMIT_PER_MINUTE": "1",
            "RATE_LIMIT_BURST": "1",
            "RATE_LIMIT_DISABLED": "false",
        },
    )
    first = client.post(
        "/v1/public/signup",
        json={"business_name": f"RateLimit Signup {uuid4()}"},
        headers={"X-Forwarded-For": "203.0.113.10"},
    )
    assert first.status_code == 201

    second = client.post(
        "/v1/public/signup",
        json={"business_name": f"RateLimit Signup {uuid4()}"},
        headers={"X-Forwarded-For": "203.0.113.10"},
    )
    assert second.status_code == 429
    assert "Retry-After" in second.headers


@pytest.mark.skipif(
    not (SQLALCHEMY_AVAILABLE and SessionLocal is not None),
    reason="Lockdown flag requires database support",
)
def test_lockdown_blocks_widget_requests(monkeypatch):
    client = _fresh_client(
        monkeypatch,
        {
            "RATE_LIMIT_PER_MINUTE": "120",
            "RATE_LIMIT_BURST": "20",
            "RATE_LIMIT_DISABLED": "false",
        },
    )
    _ensure_business()
    session = SessionLocal()
    try:
        row = session.get(BusinessDB, "default_business")
        if row is None:
            row = BusinessDB(  # type: ignore[call-arg]
                id="default_business", name="LockdownBiz", status="ACTIVE"
            )
            session.add(row)
            session.flush()
        row.lockdown_mode = True  # type: ignore[assignment]
        session.commit()
    finally:
        session.close()

    resp = client.post("/v1/widget/start", json={})
    assert resp.status_code == 423
    assert "lockdown" in resp.text.lower()

    # Reset lockdown to avoid affecting other tests.
    session = SessionLocal()
    try:
        row = session.get(BusinessDB, "default_business")
        if row:
            row.lockdown_mode = False  # type: ignore[assignment]
            session.commit()
    finally:
        session.close()


@pytest.mark.skipif(
    not (SQLALCHEMY_AVAILABLE and SessionLocal is not None),
    reason="Lockdown enforcement tests require database support",
)
def test_lockdown_blocks_widget_message_without_headers(monkeypatch):
    client = _fresh_client(
        monkeypatch,
        {
            "RATE_LIMIT_PER_MINUTE": "120",
            "RATE_LIMIT_BURST": "20",
            "RATE_LIMIT_DISABLED": "false",
        },
    )
    _ensure_business()
    locked_business_id = "biz_locked"
    widget_token = "wtok_locked"
    session = SessionLocal()
    try:
        row = session.get(BusinessDB, locked_business_id)
        if row is None:
            row = BusinessDB(  # type: ignore[call-arg]
                id=locked_business_id,
                name="LockedBiz",
                status="ACTIVE",
            )
            session.add(row)
            session.flush()
        row.widget_token = widget_token  # type: ignore[assignment]
        row.lockdown_mode = False  # type: ignore[assignment]
        session.commit()
    finally:
        session.close()

    started = client.post(
        "/v1/widget/start",
        json={},
        headers={"X-Widget-Token": widget_token, "X-Forwarded-For": "203.0.113.11"},
    )
    assert started.status_code == 200
    conversation_id = started.json()["conversation_id"]

    session = SessionLocal()
    try:
        row = session.get(BusinessDB, locked_business_id)
        assert row is not None
        row.lockdown_mode = True  # type: ignore[assignment]
        session.commit()
    finally:
        session.close()

    # No widget token header: middleware resolves DEFAULT business, but the
    # message handler should still block based on the conversation's tenant.
    msg = client.post(
        f"/v1/widget/{conversation_id}/message",
        json={"text": "hello"},
        headers={"X-Forwarded-For": "203.0.113.12"},
    )
    assert msg.status_code == 423


@pytest.mark.skipif(
    not (SQLALCHEMY_AVAILABLE and SessionLocal is not None),
    reason="Lockdown toggle endpoints require database support",
)
def test_owner_can_toggle_lockdown_via_endpoint(monkeypatch):
    client = _fresh_client(
        monkeypatch,
        {
            "RATE_LIMIT_PER_MINUTE": "120",
            "RATE_LIMIT_BURST": "20",
            "RATE_LIMIT_DISABLED": "false",
        },
    )
    _ensure_business()
    session = SessionLocal()
    try:
        row = session.get(BusinessDB, "default_business")
        assert row is not None
        row.lockdown_mode = False  # type: ignore[assignment]
        session.commit()
    finally:
        session.close()

    initial = client.get("/v1/owner/business")
    assert initial.status_code == 200

    enabled = client.post("/v1/owner/lockdown", json={"enabled": True})
    assert enabled.status_code == 200
    assert enabled.json()["lockdown_mode"] is True

    blocked = client.post(
        "/v1/widget/start",
        json={},
        headers={"X-Forwarded-For": "203.0.113.13"},
    )
    assert blocked.status_code == 423

    still_accessible = client.get("/v1/owner/business")
    assert still_accessible.status_code == 200
    assert still_accessible.json().get("lockdown_mode") is True

    disabled = client.post("/v1/owner/lockdown", json={"enabled": False})
    assert disabled.status_code == 200
    assert disabled.json()["lockdown_mode"] is False


@pytest.mark.skipif(
    not (SQLALCHEMY_AVAILABLE and SessionLocal is not None),
    reason="Admin lockdown toggle requires database support",
)
def test_admin_can_toggle_lockdown_via_business_patch(monkeypatch):
    client = _fresh_client(
        monkeypatch,
        {
            "RATE_LIMIT_PER_MINUTE": "120",
            "RATE_LIMIT_BURST": "20",
            "RATE_LIMIT_DISABLED": "false",
        },
    )
    _ensure_business()

    enabled = client.patch(
        "/v1/admin/businesses/default_business", json={"lockdown_mode": True}
    )
    assert enabled.status_code == 200
    assert enabled.json().get("lockdown_mode") is True

    # Admin access remains available while locked.
    listed = client.get("/v1/admin/businesses")
    assert listed.status_code == 200

    disabled = client.patch(
        "/v1/admin/businesses/default_business", json={"lockdown_mode": False}
    )
    assert disabled.status_code == 200
    assert disabled.json().get("lockdown_mode") in {False, None}
