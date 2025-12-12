from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

import httpx

from ..config import get_settings
from ..db import SQLALCHEMY_AVAILABLE, SessionLocal
from ..db_models import BusinessDB
from ..metrics import BusinessSmsMetrics, metrics
from .alerting import record_notification_failure


@dataclass
class SentMessage:
    to: str
    body: str
    business_id: str | None = None
    category: str | None = None  # "owner", "customer", or None


class SmsService:
    """Abstraction for SMS notifications.

    Defaults to stub mode (recording messages in-memory). When configured with
    SMS_PROVIDER=twilio and valid credentials, it will attempt to call Twilio's
    SMS API; failures fall back to stub behaviour.
    """

    def __init__(self) -> None:
        self._settings = get_settings().sms
        self._sent: List[SentMessage] = []

    @property
    def owner_number(self) -> Optional[str]:
        return self._settings.owner_number

    @property
    def sent_messages(self) -> List[SentMessage]:
        # Exposed primarily for tests and debugging.
        return list(self._sent)

    async def send_sms(
        self,
        to: str,
        body: str,
        business_id: str | None = None,
        category: str | None = None,
    ) -> None:
        # Always record locally for observability/tests.
        self._sent.append(
            SentMessage(to=to, body=body, business_id=business_id, category=category)
        )
        metrics.sms_sent_total += 1
        metrics.notification_attempts += 1

        if business_id:
            per_tenant = metrics.sms_by_business.setdefault(
                business_id, BusinessSmsMetrics()
            )
            per_tenant.sms_sent_total += 1

        if self._settings.provider != "twilio":
            return

        sid = self._settings.twilio_account_sid
        token = self._settings.twilio_auth_token
        from_number = self._settings.from_number
        if business_id and SQLALCHEMY_AVAILABLE and SessionLocal is not None:
            session_db = SessionLocal()
            try:
                row = session_db.get(BusinessDB, business_id)
                if row is not None and getattr(row, "twilio_phone_number", None):
                    from_number = row.twilio_phone_number  # type: ignore[assignment]
            finally:
                session_db.close()
        if not sid or not token or not from_number:
            return

        url = f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"
        data = {"From": from_number, "To": to, "Body": body}
        try:
            async with httpx.AsyncClient(timeout=10.0, auth=(sid, token)) as client:
                resp = await client.post(url, data=data)
                resp.raise_for_status()
        except Exception as exc:
            # Swallow errors and rely on stub recording for diagnostics.
            record_notification_failure("sms", detail=exc.__class__.__name__)
            return

    async def notify_owner(self, body: str, business_id: str | None = None) -> None:
        # Resolve per-tenant owner phone override when possible.
        to_number = self.owner_number
        if (
            business_id
            and not to_number
            and SQLALCHEMY_AVAILABLE
            and SessionLocal is not None
        ):
            session_db = SessionLocal()
            try:
                row = session_db.get(BusinessDB, business_id)
            finally:
                session_db.close()
            if row is not None and getattr(row, "owner_phone", None):
                to_number = row.owner_phone  # type: ignore[assignment]
        if not to_number:
            return
        await self.send_sms(to_number, body, business_id=business_id, category="owner")
        metrics.sms_sent_owner += 1
        if business_id:
            per_tenant = metrics.sms_by_business.setdefault(
                business_id, BusinessSmsMetrics()
            )
            per_tenant.sms_sent_owner += 1

    async def notify_customer(
        self,
        to: str | None,
        body: str,
        business_id: str | None = None,
    ) -> None:
        if not to:
            return
        await self.send_sms(to, body, business_id=business_id, category="customer")
        metrics.sms_sent_customer += 1
        if business_id:
            per_tenant = metrics.sms_by_business.setdefault(
                business_id, BusinessSmsMetrics()
            )
            per_tenant.sms_sent_customer += 1


sms_service = SmsService()
