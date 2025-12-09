from __future__ import annotations

import logging
import os
from dataclasses import dataclass

import httpx

from ..config import get_settings
from ..db import SQLALCHEMY_AVAILABLE, SessionLocal
from ..db_models import BusinessDB

logger = logging.getLogger(__name__)


@dataclass
class ProvisionResult:
    status: str  # "attached", "provisioned", "skipped", or "error"
    phone_number: str | None
    message: str


async def provision_toll_free_number(
    business_id: str,
    phone_number: str | None = None,
    purchase_new: bool = False,
    friendly_name: str | None = None,
    webhook_base_url: str | None = None,
    voice_webhook_url: str | None = None,
    sms_webhook_url: str | None = None,
    status_callback_url: str | None = None,
) -> ProvisionResult:
    """Attach or purchase a Twilio toll-free number for this tenant."""
    if not SQLALCHEMY_AVAILABLE or SessionLocal is None:
        return ProvisionResult(
            status="error",
            phone_number=None,
            message="Database support is not available for provisioning.",
        )

    session = SessionLocal()
    try:
        row = session.get(BusinessDB, business_id)
        if row is None:
            return ProvisionResult(
                status="error",
                phone_number=None,
                message="Business not found.",
            )

        # If a number is provided, just attach it and mark as connected.
        if phone_number:
            row.twilio_phone_number = phone_number  # type: ignore[assignment]
            row.integration_twilio_status = "connected"  # type: ignore[assignment]
            session.add(row)
            session.commit()
            return ProvisionResult(
                status="attached",
                phone_number=phone_number,
                message="Attached existing number; ensure Twilio webhooks point at /twilio/voice and /twilio/sms.",
            )

        if not purchase_new:
            return ProvisionResult(
                status="skipped",
                phone_number=None,
                message="No phone_number provided and purchase_new is false.",
            )

        settings = get_settings().sms
        if settings.provider != "twilio":
            return ProvisionResult(
                status="skipped",
                phone_number=None,
                message="SMS_PROVIDER is not 'twilio'; set it or provide an existing number.",
            )
        sid = settings.twilio_account_sid
        token = settings.twilio_auth_token
        if not sid or not token:
            return ProvisionResult(
                status="error",
                phone_number=None,
                message="Twilio credentials missing; set TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN.",
            )

        base = webhook_base_url or os.getenv("TWILIO_WEBHOOK_BASE_URL")
        voice_url = voice_webhook_url or (
            f"{base}/twilio/voice?business_id={business_id}" if base else None
        )
        sms_url = sms_webhook_url or (
            f"{base}/twilio/sms?business_id={business_id}" if base else None
        )
        status_cb = status_callback_url or (
            f"{base}/twilio/status-callback" if base else None
        )

        if not voice_url or not sms_url:
            return ProvisionResult(
                status="skipped",
                phone_number=None,
                message="Provide webhook_base_url (or explicit voice_webhook_url/sms_webhook_url) so purchased numbers can be wired to your webhooks.",
            )

        chosen_number = None
        try:
            async with httpx.AsyncClient(auth=(sid, token), timeout=10.0) as client:
                avail_resp = await client.get(
                    f"https://api.twilio.com/2010-04-01/Accounts/{sid}/AvailablePhoneNumbers/US/TollFree.json",
                    params={
                        "SmsEnabled": "true",
                        "VoiceEnabled": "true",
                        "PageSize": 1,
                    },
                )
                avail_resp.raise_for_status()
                numbers = avail_resp.json().get("available_phone_numbers", [])
                if not numbers:
                    return ProvisionResult(
                        status="error",
                        phone_number=None,
                        message="No toll-free numbers available from Twilio.",
                    )
                chosen_number = numbers[0].get("phone_number")
                purchase_resp = await client.post(
                    f"https://api.twilio.com/2010-04-01/Accounts/{sid}/IncomingPhoneNumbers/TollFree.json",
                    data={
                        "PhoneNumber": chosen_number,
                        "FriendlyName": friendly_name or f"{business_id}-owner-line",
                        "VoiceUrl": voice_url,
                        "VoiceMethod": "POST",
                        "SmsUrl": sms_url,
                        "SmsMethod": "POST",
                        "StatusCallback": status_cb,
                        "StatusCallbackMethod": "POST",
                    },
                )
                purchase_resp.raise_for_status()
                purchased = purchase_resp.json().get("phone_number") or chosen_number
        except Exception as exc:  # pragma: no cover - network/credential failures
            logger.warning(
                "twilio_provision_failed",
                extra={"business_id": business_id, "error": str(exc)},
            )
            return ProvisionResult(
                status="error",
                phone_number=None,
                message=f"Twilio provisioning failed: {exc}",
            )

        # Persist the purchased number.
        if purchased:
            row.twilio_phone_number = purchased  # type: ignore[assignment]
            row.integration_twilio_status = "connected"  # type: ignore[assignment]
            session.add(row)
            session.commit()
            return ProvisionResult(
                status="provisioned",
                phone_number=purchased,
                message="Purchased and attached a toll-free number via Twilio.",
            )

        return ProvisionResult(
            status="error",
            phone_number=None,
            message="Provisioning did not return a phone number.",
        )
    finally:
        session.close()
