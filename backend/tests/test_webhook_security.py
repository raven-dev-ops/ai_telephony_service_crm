import base64
import hashlib
import hmac
import json
import time

from fastapi.testclient import TestClient

from app import config, deps
from app.main import app


client = TestClient(app)


def _setup_twilio_env(monkeypatch) -> str:
    token = "secret123"
    monkeypatch.setenv("ENVIRONMENT", "prod")
    monkeypatch.setenv("SMS_PROVIDER", "twilio")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", token)
    monkeypatch.setenv("VERIFY_TWILIO_SIGNATURES", "true")
    config.get_settings.cache_clear()
    deps.get_settings.cache_clear()
    return token


def _twilio_signature(url: str, params: dict[str, str], token: str) -> str:
    data = url + "".join(f"{k}{params[k]}" for k in sorted(params.keys()))
    digest = hmac.new(
        token.encode("utf-8"), data.encode("utf-8"), hashlib.sha1
    ).digest()
    return base64.b64encode(digest).decode("utf-8")


def _setup_stripe_env(monkeypatch) -> str:
    secret = "whsec_test"
    monkeypatch.setenv("ENVIRONMENT", "prod")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", secret)
    monkeypatch.setenv("STRIPE_USE_STUB", "false")
    config.get_settings.cache_clear()
    deps.get_settings.cache_clear()
    return secret


def _stripe_signature(payload: str, secret: str, ts: int | None = None) -> str:
    ts_val = ts or int(time.time())
    signed = f"{ts_val}.{payload}".encode()
    digest = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return f"t={ts_val},v1={digest}"


def test_twilio_missing_signature_rejected(monkeypatch):
    _setup_twilio_env(monkeypatch)
    resp = client.post(
        "/twilio/sms",
        data={"From": "+15550001111", "Body": "Hello"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert resp.status_code == 401


def test_twilio_invalid_signature_rejected(monkeypatch):
    token = _setup_twilio_env(monkeypatch)
    params = {"From": "+15550001111", "Body": "Hello"}
    url = "http://testserver/twilio/sms"
    sig = _twilio_signature(url, params, token)
    resp = client.post(
        "/twilio/sms",
        data=params,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Twilio-Signature": sig + "tampered",
            "X-Twilio-Request-Timestamp": str(int(time.time())),
        },
    )
    assert resp.status_code == 401


def test_twilio_stale_timestamp_rejected(monkeypatch):
    token = _setup_twilio_env(monkeypatch)
    params = {"From": "+15550002222", "Body": "Ping"}
    url = "http://testserver/twilio/sms"
    sig = _twilio_signature(url, params, token)
    old_ts = int(time.time()) - 1000
    resp = client.post(
        "/twilio/sms",
        data=params,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Twilio-Signature": sig,
            "X-Twilio-Request-Timestamp": str(old_ts),
        },
    )
    assert resp.status_code == 400


def test_twilio_replay_event_blocked(monkeypatch):
    token = _setup_twilio_env(monkeypatch)
    params = {"From": "+15550003333", "Body": "Replay?"}
    url = "http://testserver/twilio/sms"
    sig = _twilio_signature(url, params, token)
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "X-Twilio-Signature": sig,
        "X-Twilio-Request-Timestamp": str(int(time.time())),
        "X-Twilio-EventId": "EV_TEST_1",
    }

    first = client.post("/twilio/sms", data=params, headers=headers)
    assert first.status_code == 200

    second = client.post("/twilio/sms", data=params, headers=headers)
    assert second.status_code == 409


def test_stripe_missing_signature_rejected(monkeypatch):
    _setup_stripe_env(monkeypatch)
    payload = json.dumps(
        {
            "id": "evt_missing",
            "type": "unknown.event",
            "data": {"object": {"metadata": {"business_id": "default_business"}}},
        }
    )
    resp = client.post(
        "/v1/billing/webhook",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 400


def test_stripe_invalid_signature_rejected(monkeypatch):
    _setup_stripe_env(monkeypatch)
    payload = json.dumps(
        {
            "id": "evt_invalid",
            "type": "unknown.event",
            "data": {"object": {"metadata": {"business_id": "default_business"}}},
        }
    )
    ts = int(time.time())
    sig_header = f"t={ts},v1=badsignature"
    resp = client.post(
        "/v1/billing/webhook",
        data=payload,
        headers={"Content-Type": "application/json", "Stripe-Signature": sig_header},
    )
    assert resp.status_code == 400


def test_stripe_replay_rejected(monkeypatch):
    secret = _setup_stripe_env(monkeypatch)
    payload = json.dumps(
        {
            "id": "evt_replay",
            "type": "unknown.event",
            "data": {"object": {"metadata": {"business_id": "default_business"}}},
        }
    )
    sig_header = _stripe_signature(payload, secret)
    headers = {"Content-Type": "application/json", "Stripe-Signature": sig_header}

    first = client.post("/v1/billing/webhook", data=payload, headers=headers)
    assert first.status_code in {200, 202}

    second = client.post("/v1/billing/webhook", data=payload, headers=headers)
    assert second.status_code == 400
