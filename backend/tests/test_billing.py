from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from app.main import app
client = TestClient(app)


def test_list_plans_and_checkout_stub(monkeypatch):
    resp = client.get("/v1/billing/plans")
    assert resp.status_code == 200
    plans = resp.json()
    assert any(p["id"] == "basic" for p in plans)

    checkout = client.post("/v1/billing/create-checkout-session", params={"plan_id": "basic"})
    assert checkout.status_code == 200
    data = checkout.json()
    assert data["url"]
    assert data["session_id"]


def test_webhook_updates_subscription(monkeypatch):
    # Prepare a fake event
    now = datetime.now(UTC)
    payload = {
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "customer": "cus_123",
                "subscription": "sub_123",
                "current_period_end": int((now + timedelta(days=30)).timestamp()),
                "metadata": {"business_id": "default_business"},
            }
        },
    }
    resp = client.post("/v1/billing/webhook", json=payload)
    assert resp.status_code == 200

    # Verify via owner onboarding profile
    status = client.get("/v1/owner/onboarding/profile").json()
    assert status.get("subscription_status") in {"active", "past_due", "canceled"}
    assert status.get("subscription_current_period_end") is not None
