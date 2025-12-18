import base64
import hashlib
import hmac
import time

from fastapi.testclient import TestClient

import pytest

from app import config, deps, main
from app.main import app as default_app
from app.db import SQLALCHEMY_AVAILABLE, SessionLocal
from app.db_models import BusinessDB


def _fresh_client(monkeypatch: pytest.MonkeyPatch, env: dict[str, str]) -> TestClient:
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    config.get_settings.cache_clear()
    deps.get_settings.cache_clear()
    app = main.create_app()
    return TestClient(app)


def _ensure_default_business() -> None:
    if not (SQLALCHEMY_AVAILABLE and SessionLocal is not None):
        return
    session = SessionLocal()
    try:
        row = session.get(BusinessDB, deps.DEFAULT_BUSINESS_ID)
        if row is None:
            row = BusinessDB(  # type: ignore[call-arg]
                id=deps.DEFAULT_BUSINESS_ID, name="Default", status="ACTIVE"
            )
            session.add(row)
            session.commit()
    finally:
        session.close()


def _twilio_signature(url: str, params: dict[str, str], token: str) -> str:
    data = url + "".join(f"{k}{params[k]}" for k in sorted(params.keys()))
    digest = hmac.new(
        token.encode("utf-8"), data.encode("utf-8"), hashlib.sha1
    ).digest()
    return base64.b64encode(digest).decode("utf-8")


def test_security_event_rate_limit_block_is_queryable(monkeypatch) -> None:
    client = _fresh_client(
        monkeypatch,
        {
            "ADMIN_API_KEY": "",
            "RATE_LIMIT_PER_MINUTE": "1",
            "RATE_LIMIT_BURST": "1",
            "RATE_LIMIT_DISABLED": "false",
        },
    )
    _ensure_default_business()

    first = client.post("/v1/widget/start", json={})
    assert first.status_code == 200

    second = client.post("/v1/widget/start", json={})
    assert second.status_code == 429
    rid = second.headers.get("X-Request-ID")
    assert rid

    events = client.get(
        "/v1/admin/security-events", params={"request_id": rid, "limit": 10}
    )
    assert events.status_code == 200
    rows = events.json()
    assert any(
        row.get("event_type") == "rate_limit_blocked" and row.get("status_code") == 429
        for row in rows
    )


def test_security_event_twilio_missing_signature_is_queryable(monkeypatch) -> None:
    token = "secret123"
    monkeypatch.setenv("ENVIRONMENT", "prod")
    monkeypatch.setenv("SMS_PROVIDER", "twilio")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", token)
    monkeypatch.setenv("VERIFY_TWILIO_SIGNATURES", "true")
    config.get_settings.cache_clear()
    deps.get_settings.cache_clear()
    client = TestClient(default_app)
    _ensure_default_business()

    resp = client.post(
        "/twilio/sms",
        data={"From": "+15550001111", "Body": "Hello"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert resp.status_code == 401
    rid = resp.headers.get("X-Request-ID")
    assert rid

    events = client.get(
        "/v1/admin/security-events", params={"request_id": rid, "limit": 10}
    )
    assert events.status_code == 200
    rows = events.json()
    assert any(
        row.get("event_type") == "webhook_signature_missing"
        and row.get("status_code") == 401
        and row.get("path") == "/twilio/sms"
        for row in rows
    )


def test_security_event_twilio_replay_is_queryable(monkeypatch) -> None:
    token = "secret123"
    monkeypatch.setenv("ENVIRONMENT", "prod")
    monkeypatch.setenv("SMS_PROVIDER", "twilio")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", token)
    monkeypatch.setenv("VERIFY_TWILIO_SIGNATURES", "true")
    config.get_settings.cache_clear()
    deps.get_settings.cache_clear()
    client = TestClient(default_app)
    _ensure_default_business()

    params = {"From": "+15550003333", "Body": "Replay?"}
    url = "http://testserver/twilio/sms"
    sig = _twilio_signature(url, params, token)
    event_id = f"EV_SECURITY_{int(time.time() * 1000)}"
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "X-Twilio-Signature": sig,
        "X-Twilio-Request-Timestamp": str(int(time.time())),
        "X-Twilio-EventId": event_id,
    }

    first = client.post("/twilio/sms", data=params, headers=headers)
    assert first.status_code == 200

    second = client.post("/twilio/sms", data=params, headers=headers)
    assert second.status_code == 409
    rid = second.headers.get("X-Request-ID")
    assert rid

    events = client.get(
        "/v1/admin/security-events", params={"request_id": rid, "limit": 10}
    )
    assert events.status_code == 200
    rows = events.json()
    assert any(
        row.get("event_type") == "webhook_replay_blocked"
        and row.get("status_code") == 409
        and row.get("path") == "/twilio/sms"
        for row in rows
    )


def test_security_event_admin_auth_failure_is_queryable(monkeypatch) -> None:
    client = _fresh_client(
        monkeypatch,
        {
            "ADMIN_API_KEY": "admin-secret",
            "ENVIRONMENT": "dev",
        },
    )
    _ensure_default_business()

    blocked = client.get(
        "/v1/admin/audit",
        headers={"X-Admin-API-Key": "bad"},
    )
    assert blocked.status_code == 401
    rid = blocked.headers.get("X-Request-ID")
    assert rid

    events = client.get(
        "/v1/admin/security-events",
        params={"request_id": rid, "limit": 10},
        headers={"X-Admin-API-Key": "admin-secret"},
    )
    assert events.status_code == 200
    rows = events.json()
    assert any(
        row.get("event_type") == "auth_failure"
        and row.get("status_code") == 401
        and row.get("meta")
        and "admin_api_key_invalid" in row.get("meta")
        for row in rows
    )
