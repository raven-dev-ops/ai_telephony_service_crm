from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query

from ..deps import ensure_business_active
from ..db import SQLALCHEMY_AVAILABLE, SessionLocal
from ..db_models import Business
from ..metrics import BusinessSmsMetrics, metrics
from ..repositories import appointments_repo, customers_repo
from ..services.sms import sms_service
from ..business_config import get_vertical_for_business, get_language_for_business


router = APIRouter()


@router.post("/send-retention")
async def send_retention_campaign(
    min_days_since_last_visit: int = Query(180, ge=30, le=1095),
    max_messages: int = Query(50, ge=1, le=500),
    campaign_type: str = Query("generic"),
    service_type: str | None = Query(default=None),
    tag: str | None = Query(default=None),
    business_id: str = Depends(ensure_business_active),
) -> dict:
    """Send simple retention SMS messages to past customers.

    This targets customers whose most recent appointment for this tenant is
    at least `min_days_since_last_visit` days in the past and who do not
    currently have a future SCHEDULED/CONFIRMED appointment.

    Intended for use by a scheduler/cron job.
    """
    now = datetime.now(UTC)
    cutoff = now - timedelta(days=min_days_since_last_visit)

    # Gather appointments for this tenant and compute last-visit per customer,
    # along with simple service/tag context so campaigns can target segments.
    appts = [
        a for a in appointments_repo.list_for_business(business_id)
        if getattr(a, "customer_id", None)
    ]

    latest_by_customer: dict[str, datetime] = {}
    service_type_by_customer: dict[str, str | None] = {}
    tags_by_customer: dict[str, list[str]] = {}
    has_future_by_customer: dict[str, bool] = {}

    for appt in appts:
        customer_id = getattr(appt, "customer_id", None)
        if not customer_id:
            continue
        start_time = getattr(appt, "start_time", None)
        if not start_time:
            continue
        status = getattr(appt, "status", "SCHEDULED").upper()
        if status in {"SCHEDULED", "CONFIRMED"} and start_time > now:
            has_future_by_customer[customer_id] = True
        # Track most recent past appointment.
        if start_time <= now:
            prev = latest_by_customer.get(customer_id)
            if prev is None or start_time > prev:
                latest_by_customer[customer_id] = start_time
                service_type_by_customer[customer_id] = getattr(
                    appt, "service_type", None
                )
                tags_by_customer[customer_id] = getattr(appt, "tags", []) or []

    # Business metadata for copy.
    business_name = "your service company"
    language_code = get_language_for_business(business_id)
    vertical = get_vertical_for_business(business_id)
    if SQLALCHEMY_AVAILABLE and SessionLocal is not None:
        session_db = SessionLocal()
        try:
            row = session_db.get(Business, business_id)
        finally:
            session_db.close()
        if row is not None and getattr(row, "name", None):
            business_name = row.name  # type: ignore[assignment]

    sent = 0

    # Iterate over customers whose last visit is before the cutoff and who do
    # not have a future appointment scheduled.
    for customer_id, last_visit in sorted(
        latest_by_customer.items(), key=lambda kv: kv[1]
    ):
        if sent >= max_messages:
            break
        if last_visit > cutoff:
            continue
        if has_future_by_customer.get(customer_id):
            continue

        customer = customers_repo.get(customer_id)
        if not customer or not customer.phone or getattr(customer, "sms_opt_out", False):
            continue

        days_ago = (now - last_visit).days
        ct = (campaign_type or "generic").lower()
        if language_code == "es":
            body = (
                f"Habla {business_name}. Hace aproximadamente {days_ago} dA-as que te ayudamos "
                f"con tu {vertical}. Si necesitas mantenimiento o tienes algAºn problema nuevo, "
                "responde a este mensaje o llA?manos para agendar una visita."
            )
        else:
            body = (
                f"This is {business_name}. It has been about {days_ago} days since we last helped "
                f"you with your {vertical}. If you need maintenance or have any new issues, "
                "reply to this message or call us to book a visit."
            )

        await sms_service.notify_customer(customer.phone, body, business_id=business_id)

        # Track per-campaign counts for owner analytics.
        campaign_stats = metrics.retention_by_business.setdefault(business_id, {})
        campaign_stats[ct] = campaign_stats.get(ct, 0) + 1

        # Track retention messages in per-tenant SMS metrics.
        metrics.lead_followups_sent += 0  # keep existing metric untouched
        per = metrics.sms_by_business.setdefault(business_id, BusinessSmsMetrics())
        per.retention_messages_sent += 1
        sent += 1

    return {"retention_messages_sent": sent}

