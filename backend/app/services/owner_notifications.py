from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Dict, Optional

from ..metrics import metrics
from ..services.sms import sms_service
from ..services.email_service import email_service
from ..db import SQLALCHEMY_AVAILABLE, SessionLocal
from ..db_models import BusinessDB


@dataclass
class OwnerNotificationResult:
    delivered: bool
    channel: str | None
    detail: str | None
    timestamp: datetime


# Simple dedupe cache to avoid spamming owners.
_last_notification: Dict[tuple[str, str], datetime] = {}
_last_body_hash: Dict[tuple[str, str], str] = {}
_DEDUP_WINDOW = timedelta(seconds=90)


def _owner_contacts(business_id: str) -> tuple[Optional[str], Optional[str]]:
    phone = sms_service.owner_number
    email = None
    if business_id and SQLALCHEMY_AVAILABLE and SessionLocal is not None:
        session_db = SessionLocal()
        try:
            row = session_db.get(BusinessDB, business_id)
        finally:
            session_db.close()
        if row is not None:
            phone = getattr(row, "owner_phone", phone)
            email = getattr(row, "owner_email", None)
            if getattr(row, "owner_email_alerts_enabled", True) is False:
                email = None
    return phone, email


def _record_event(
    business_id: str, channel: str | None, status: str, detail: str | None
) -> None:
    now = datetime.now(UTC)
    metrics.owner_notification_status_by_business[business_id] = {
        "channel": channel,
        "status": status,
        "detail": detail,
        "timestamp": now.isoformat(),
    }
    events = metrics.owner_notification_events.setdefault(business_id, [])
    events.append(
        {
            "channel": channel,
            "status": status,
            "detail": detail,
            "timestamp": now.isoformat(),
        }
    )
    # Keep a short trail to bound memory.
    if len(events) > 20:
        del events[:-20]


async def notify_owner_with_fallback(
    *,
    business_id: str,
    message: str,
    subject: str | None = None,
    dedupe_key: str | None = None,
    send_email_copy: bool = False,
) -> OwnerNotificationResult:
    """Notify the owner with SMS first, falling back to email if needed."""

    now = datetime.now(UTC)
    key = dedupe_key or message[:64]
    cache_key = (business_id, key)
    body_hash = str(hash(message))
    last_time = _last_notification.get(cache_key)
    last_hash = _last_body_hash.get(cache_key)
    if last_time and now - last_time < _DEDUP_WINDOW and last_hash == body_hash:
        _record_event(business_id, None, "deduped", "Duplicate notification suppressed")
        return OwnerNotificationResult(
            delivered=False,
            channel=None,
            detail="deduped",
            timestamp=now,
        )

    phone, email = _owner_contacts(business_id)
    channel_used = None
    detail = None

    sms_ok = False
    if phone:
        sms_raw = await sms_service.notify_owner(message, business_id=business_id)
        sms_ok = sms_raw is not False
        channel_used = "sms"
        detail = "sms_sent" if sms_ok else "sms_failed"

    delivered = sms_ok
    if delivered and email and send_email_copy:
        await email_service.notify_owner(
            subject=subject or "Owner notification",
            body=message,
            business_id=business_id,
            owner_email=email,
        )
        channel_used = channel_used or "sms"
        detail = "sms_and_email"
        delivered = bool(channel_used)
    elif not delivered and email:
        result = await email_service.notify_owner(
            subject=subject or "Owner notification",
            body=message,
            business_id=business_id,
            owner_email=email,
        )
        delivered = bool(getattr(result, "sent", False))
        channel_used = "email"
        detail = getattr(result, "detail", None) or (
            "email_sent" if delivered else "email_failed"
        )

    status = "delivered" if delivered else "failed"
    _last_notification[cache_key] = now
    _last_body_hash[cache_key] = body_hash
    _record_event(business_id, channel_used, status, detail)

    return OwnerNotificationResult(
        delivered=delivered,
        channel=channel_used,
        detail=detail,
        timestamp=now,
    )
