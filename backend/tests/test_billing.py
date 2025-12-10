from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.main import app
from app.metrics import metrics
from app.routers import billing
from app.db import SessionLocal
from app.db_models import BusinessDB
from app.deps import DEFAULT_BUSINESS_ID
from app import config
from app.services import subscription as subscription_service

client = TestClient(app)


def test_list_plans_and_checkout_stub(monkeypatch):
    monkeypatch.setenv("STRIPE_USE_STUB", "true")
    config.get_settings.cache_clear()
    resp = client.get("/v1/billing/plans")
    assert resp.status_code == 200
    plans = resp.json()
    assert any(p["id"] == "basic" for p in plans)

    checkout = client.post(
        "/v1/billing/create-checkout-session", params={"plan_id": "basic"}
    )
    assert checkout.status_code == 200
    data = checkout.json()
    assert data["url"]
    assert data["session_id"]
    config.get_settings.cache_clear()


def test_live_checkout_uses_stripe(monkeypatch):
    created: dict = {}

    class FakeStripeSettings:
        def __init__(self) -> None:
            self.api_key = "sk_test"
            self.publishable_key = None
            self.webhook_secret = None
            self.price_basic = "price_basic_live"
            self.price_growth = "price_growth_live"
            self.price_scale = "price_scale_live"
            self.payment_link_url = None
            self.billing_portal_url = None
            self.billing_portal_return_url = None
            self.checkout_success_url = "https://app.example.com/success"
            self.checkout_cancel_url = "https://app.example.com/cancel"
            self.use_stub = False
            self.verify_signatures = False
            self.replay_protection_seconds = 0

    stripe_settings = FakeStripeSettings()

    class FakeSettings:
        stripe = stripe_settings

    class FakeCheckoutSession:
        @staticmethod
        def create(**kwargs):
            created.update(kwargs)
            return type("obj", (), {"url": "https://checkout.example/abc", "id": "cs_123"})()

    class FakeCheckout:
        Session = FakeCheckoutSession

    class FakeStripe:
        checkout = FakeCheckout

    monkeypatch.setattr(billing, "_get_stripe_client", lambda: FakeStripe)
    monkeypatch.setattr(billing, "_get_or_create_customer", lambda _bid, _email: "cus_live")
    monkeypatch.setattr(billing, "get_settings", lambda: FakeSettings())

    resp = client.post("/v1/billing/create-checkout-session", params={"plan_id": "basic"})
    assert resp.status_code == 200
    assert created["customer"] == "cus_live"
    assert created["line_items"][0]["price"] == stripe_settings.price_basic
    assert created["subscription_data"]["metadata"]["plan_id"] == "basic"


def test_webhook_updates_subscription(monkeypatch):
    monkeypatch.setenv("STRIPE_USE_STUB", "true")
    monkeypatch.setenv("STRIPE_VERIFY_SIGNATURES", "false")
    config.get_settings.cache_clear()
    # Prepare a fake event
    now = datetime.now(UTC)
    payload = {
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "customer": "cus_123",
                "subscription": "sub_123",
                "current_period_end": int((now + timedelta(days=30)).timestamp()),
                "metadata": {"business_id": "default_business", "plan_id": "growth"},
            }
        },
    }
    resp = client.post("/v1/billing/webhook", json=payload)
    assert resp.status_code == 200
    session = SessionLocal()
    try:
        row = session.get(BusinessDB, DEFAULT_BUSINESS_ID)
        assert row.subscription_status in {"active", "past_due", "canceled"}
        assert row.subscription_current_period_end is not None
        assert row.service_tier == "growth"
    finally:
        session.close()
    config.get_settings.cache_clear()


def test_webhook_marks_payment_failed(monkeypatch):
    monkeypatch.setenv("STRIPE_USE_STUB", "true")
    monkeypatch.setenv("STRIPE_VERIFY_SIGNATURES", "false")
    config.get_settings.cache_clear()
    now = datetime.now(UTC)
    payload = {
        "type": "invoice.payment_failed",
        "data": {
            "object": {
                "customer": "cus_999",
                "subscription": "sub_999",
                "current_period_end": int((now + timedelta(days=10)).timestamp()),
                "metadata": {"business_id": "default_business", "plan_id": "basic"},
            }
        },
    }
    resp = client.post("/v1/billing/webhook", json=payload)
    assert resp.status_code == 200
    session = SessionLocal()
    try:
        row = session.get(BusinessDB, DEFAULT_BUSINESS_ID)
        assert row.subscription_status == "past_due"
        assert row.service_tier == "basic"
    finally:
        session.close()
    config.get_settings.cache_clear()


def test_webhook_failure_triggers_notification(monkeypatch):
    monkeypatch.setenv("STRIPE_USE_STUB", "true")
    monkeypatch.setenv("STRIPE_VERIFY_SIGNATURES", "false")
    config.get_settings.cache_clear()
    called = {}

    async def fake_notify(business_id, state):
        called["business_id"] = business_id
        called["status"] = state.status

    monkeypatch.setattr(subscription_service, "notify_status_change", fake_notify)
    session = SessionLocal()
    try:
        row = session.get(BusinessDB, DEFAULT_BUSINESS_ID)
        row.owner_email = "owner@example.com"
        session.add(row)
        session.commit()
    finally:
        session.close()

    now = datetime.now(UTC)
    payload = {
        "type": "invoice.payment_failed",
        "data": {
            "object": {
                "customer": "cus_111",
                "subscription": "sub_111",
                "current_period_end": int((now - timedelta(days=2)).timestamp()),
                "metadata": {"business_id": DEFAULT_BUSINESS_ID, "plan_id": "basic"},
            }
        },
    }
    resp = client.post("/v1/billing/webhook", json=payload)
    assert resp.status_code == 200
    assert called["business_id"] == DEFAULT_BUSINESS_ID
    assert called["status"] == "past_due"
    config.get_settings.cache_clear()


def test_create_checkout_session_invalid_plan_returns_404():
    resp = client.post(
        "/v1/billing/create-checkout-session", params={"plan_id": "nonexistent"}
    )
    assert resp.status_code == 404


def test_webhook_invalid_payload_increments_failure(monkeypatch):
    metrics.billing_webhook_failures = 0
    resp = client.post(
        "/v1/billing/webhook",
        data="not-json",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 400
    assert metrics.billing_webhook_failures >= 1


def test_webhook_uses_stripe_construct_event(monkeypatch):
    now = datetime.now(UTC)
    captured = {}

    class FakeWebhook:
        @staticmethod
        def construct_event(payload, sig_header, secret):
            captured["sig"] = (payload, sig_header, secret)
            return {
                "id": "evt_123",
                "type": "customer.subscription.updated",
                "data": {
                    "object": {
                        "id": "sub_123",
                        "status": "active",
                        "customer": "cus_123",
                        "current_period_end": int((now + timedelta(days=3)).timestamp()),
                        "metadata": {"business_id": "default_business"},
                    }
                },
            }

    class FakeStripe:
        Webhook = FakeWebhook

    class FakeStripeSettings:
        verify_signatures = True
        use_stub = False
        webhook_secret = "whsec_test"
        replay_protection_seconds = 0
        api_key = "sk_test"

    class FakeSettings:
        stripe = FakeStripeSettings()

    monkeypatch.setattr(billing, "stripe", FakeStripe)
    monkeypatch.setattr(billing, "check_replay", lambda *_a, **_k: None)
    monkeypatch.setattr(billing, "_update_subscription", lambda *args, **kwargs: captured.update(kwargs))
    monkeypatch.setattr(billing, "get_settings", lambda: FakeSettings())

    resp = client.post(
        "/v1/billing/webhook",
        data="{}",
        headers={"Stripe-Signature": "t=123,v1=fake"},
    )
    assert resp.status_code == 200
    assert captured["status"] == "active"
    assert captured["subscription_id"] == "sub_123"
    assert captured["customer_id"] == "cus_123"
    assert captured["sig"][1] == "t=123,v1=fake"


def test_webhook_bad_signature_rejected(monkeypatch):
    metrics.billing_webhook_failures = 0

    class FakeStripe:
        class Webhook:
            @staticmethod
            def construct_event(payload, sig_header, secret):
                raise Exception("bad sig")

    class FakeStripeSettings:
        verify_signatures = True
        use_stub = False
        webhook_secret = "whsec_test"
        replay_protection_seconds = 0
        api_key = "sk_test"

    class FakeSettings:
        stripe = FakeStripeSettings()

    monkeypatch.setattr(billing, "stripe", FakeStripe)
    monkeypatch.setattr(billing, "get_settings", lambda: FakeSettings())

    resp = client.post(
        "/v1/billing/webhook",
        data="{}",
        headers={"Stripe-Signature": "t=123,v1=fake"},
    )
    assert resp.status_code == 400
    assert metrics.billing_webhook_failures >= 1


def test_billing_portal_live_creates_session(monkeypatch):
    class FakePortalSession:
        @staticmethod
        def create(customer, return_url):
            FakePortalSession.customer = customer
            FakePortalSession.return_url = return_url
            return type("obj", (), {"url": "https://portal.example.com/session"})()

    class FakePortal:
        Session = FakePortalSession

    class FakeClient:
        billing_portal = FakePortal

    class FakeStripeSettings:
        billing_portal_url = None
        use_stub = False
        api_key = "sk_test"
        billing_portal_return_url = "https://app.example.com/billing"
        checkout_success_url = "https://app.example.com/success"

    class FakeSettings:
        stripe = FakeStripeSettings()

    monkeypatch.setattr(billing, "_get_stripe_client", lambda: FakeClient)
    monkeypatch.setattr(billing, "_get_or_create_customer", lambda _bid, _e: "cus_portal")
    monkeypatch.setattr(billing, "get_settings", lambda: FakeSettings())

    resp = client.get("/v1/billing/portal-link")
    assert resp.status_code == 200
    body = resp.json()
    assert body["url"] == "https://portal.example.com/session"
    assert FakePortalSession.customer == "cus_portal"
    assert FakePortalSession.return_url == "https://app.example.com/billing"


def test_require_db_raises_when_unavailable(monkeypatch):
    monkeypatch.setattr(billing, "SQLALCHEMY_AVAILABLE", False)
    monkeypatch.setattr(billing, "SessionLocal", None)
    with pytest.raises(HTTPException) as exc:
        billing._require_db()
    assert exc.value.status_code == 503


def test_update_subscription_missing_business(monkeypatch):
    class DummySession:
        def __init__(self) -> None:
            self.closed = False

        def get(self, model, key):
            return None

        def close(self):
            self.closed = True

    monkeypatch.setattr(billing, "SQLALCHEMY_AVAILABLE", True)
    monkeypatch.setattr(billing, "SessionLocal", lambda: DummySession())
    with pytest.raises(HTTPException) as exc:
        billing._update_subscription(
            business_id="missing",
            status="active",
            customer_id=None,
            subscription_id=None,
            current_period_end=None,
        )
    assert exc.value.status_code == 404


def test_live_checkout_requires_price(monkeypatch):
    class FakeStripeSettings:
        payment_link_url = None
        use_stub = False
        api_key = "sk_test"
        checkout_success_url = "https://example.com/success"
        checkout_cancel_url = "https://example.com/cancel"

    class FakeSettings:
        stripe = FakeStripeSettings()

    # Force plans without price IDs to trigger the config error path when live mode is requested.
    monkeypatch.setattr(billing, "get_settings", lambda: FakeSettings())
    monkeypatch.setattr(
        billing,
        "_plans_from_settings",
        lambda: [
            billing.Plan(
                id="basic",
                name="Basic",
                interval="month",
                price_cents=1000,
                stripe_price_id=None,
                features=[],
            )
        ],
    )
    resp = client.post(
        "/v1/billing/create-checkout-session", params={"plan_id": "basic"}
    )
    assert resp.status_code == 503
