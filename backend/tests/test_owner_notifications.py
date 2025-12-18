import pytest

from app.deps import DEFAULT_BUSINESS_ID
from app.services import owner_notifications
from app.services.owner_notifications import notify_owner_with_fallback
from app.services.sms import sms_service
from app.services.email_service import email_service, EmailResult
from app.metrics import metrics


def _reset_state():
    owner_notifications._last_notification.clear()  # type: ignore[attr-defined]
    owner_notifications._last_body_hash.clear()  # type: ignore[attr-defined]
    metrics.owner_notification_status_by_business.clear()
    metrics.owner_notification_events.clear()
    if hasattr(sms_service, "_sent"):
        sms_service._sent.clear()  # type: ignore[attr-defined]
    if hasattr(email_service, "_sent"):
        email_service._sent.clear()  # type: ignore[attr-defined]


@pytest.mark.anyio
async def test_notify_owner_with_sms_success(monkeypatch):
    _reset_state()
    calls = []

    async def fake_notify(body: str, business_id: str | None = None):
        calls.append((body, business_id))
        return True

    monkeypatch.setattr(sms_service, "notify_owner", fake_notify)
    monkeypatch.setattr(
        owner_notifications, "_owner_contacts", lambda biz: ("+10000000000", None)
    )

    result = await notify_owner_with_fallback(
        business_id=DEFAULT_BUSINESS_ID,
        message="Emergency alert",
        subject="Alert",
        dedupe_key="sms_success",
    )
    assert result.delivered is True
    assert result.channel == "sms"
    assert calls
    status = metrics.owner_notification_status_by_business.get(DEFAULT_BUSINESS_ID, {})
    assert status.get("status") == "delivered"


@pytest.mark.anyio
async def test_notify_owner_falls_back_to_email(monkeypatch):
    _reset_state()

    async def fake_sms(body: str, business_id: str | None = None):
        return False

    async def fake_email(
        subject: str, body: str, *, business_id: str, owner_email=None
    ):
        return EmailResult(sent=True, detail="ok", provider="stub")

    monkeypatch.setattr(sms_service, "notify_owner", fake_sms)
    monkeypatch.setattr(email_service, "notify_owner", fake_email)
    monkeypatch.setattr(
        owner_notifications, "_owner_contacts", lambda biz: (None, "owner@example.com")
    )

    result = await notify_owner_with_fallback(
        business_id=DEFAULT_BUSINESS_ID,
        message="Fallback alert",
        subject="Alert",
        dedupe_key="fallback_email",
    )
    assert result.delivered is True
    assert result.channel == "email"
    status = metrics.owner_notification_status_by_business.get(DEFAULT_BUSINESS_ID, {})
    assert status.get("channel") == "email"


@pytest.mark.anyio
async def test_notify_owner_dedupes_recent_alerts(monkeypatch):
    _reset_state()

    async def fake_sms(body: str, business_id: str | None = None):
        return True

    monkeypatch.setattr(sms_service, "notify_owner", fake_sms)
    monkeypatch.setattr(
        owner_notifications, "_owner_contacts", lambda biz: ("+10000000000", None)
    )

    first = await notify_owner_with_fallback(
        business_id=DEFAULT_BUSINESS_ID,
        message="Deduped alert",
        subject="Alert",
        dedupe_key="dedupe_key",
    )
    assert first.delivered is True

    second = await notify_owner_with_fallback(
        business_id=DEFAULT_BUSINESS_ID,
        message="Deduped alert",
        subject="Alert",
        dedupe_key="dedupe_key",
    )
    assert second.delivered is False
    assert second.detail == "deduped"
    events = metrics.owner_notification_events.get(DEFAULT_BUSINESS_ID, [])
    assert len(events) >= 1
