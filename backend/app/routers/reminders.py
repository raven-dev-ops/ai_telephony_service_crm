from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends

from ..deps import ensure_business_active
from ..db import SQLALCHEMY_AVAILABLE, SessionLocal
from ..db_models import BusinessDB
from ..metrics import BusinessSmsMetrics, metrics
from ..repositories import appointments_repo, customers_repo
from ..services import conversation
from ..services.sms import sms_service
from ..services.job_queue import job_queue


router = APIRouter()


@router.post("/send-upcoming")
async def send_upcoming_reminders(
    hours_ahead: int = 24,
    background: bool = False,
    business_id: str = Depends(ensure_business_active),
) -> dict:
    """Send SMS reminders for upcoming appointments within the next N hours.

    This endpoint is intended to be called by a scheduler/cron job.
    """

    async def _run() -> int:
        now = datetime.now(UTC)

        effective_hours = hours_ahead
        business_name = conversation.DEFAULT_BUSINESS_NAME
        language_code = "en"
        if SQLALCHEMY_AVAILABLE and SessionLocal is not None:
            session_db = SessionLocal()
            try:
                row = session_db.get(BusinessDB, business_id)
            finally:
                session_db.close()
            if row is not None:
                if getattr(row, "default_reminder_hours", None) is not None:
                    effective_hours = row.default_reminder_hours  # type: ignore[assignment]
                business_name = getattr(row, "name", business_name)
                language_code = getattr(row, "language_code", "en") or "en"

        cutoff = now + timedelta(hours=effective_hours)
        sent = 0

        for appt in appointments_repo.list_for_business(business_id):
            if appt.reminder_sent:
                continue
            # Skip reminders for cancelled or non-active appointments.
            status = getattr(appt, "status", "SCHEDULED").upper()
            if status not in {"SCHEDULED", "CONFIRMED"}:
                continue
            if not (now <= appt.start_time <= cutoff):
                continue
            customer = customers_repo.get(appt.customer_id)
            if (
                not customer
                or not customer.phone
                or getattr(customer, "sms_opt_out", False)
            ):
                continue
            when_str = appt.start_time.strftime("%a %b %d at %I:%M %p UTC")
            if language_code == "es":
                body = (
                    f"Recordatorio: tu cita con {business_name} es el {when_str}.\n"
                    "Si necesitas reprogramarla, por favor llama o envA-a un mensaje de texto."
                )
            else:
                body = (
                    f"Reminder: your appointment with {business_name} is scheduled for {when_str}.\n"
                    f"If you need to reschedule, please call or text."
                )
            await sms_service.notify_customer(
                customer.phone, body, business_id=business_id
            )
            appt.reminder_sent = True
            sent += 1

        return sent

    if background:
        job_queue.enqueue("send_upcoming_reminders", lambda: asyncio.run(_run()))
        return {"reminders_sent": 0, "queued": True}
    sent = await _run()
    return {"reminders_sent": sent}


@router.post("/send-followups")
async def send_unbooked_lead_followups(
    business_id: str = Depends(ensure_business_active),
) -> dict:
    """Send SMS follow-ups to recent leads without a booked appointment.

    This targets customers who have at least one completed conversation in
    the last 7 days but no SCHEDULED/CONFIRMED appointments yet. It is
    intended to be run by a scheduler/cron job.
    """
    now = datetime.now(UTC)
    window = now - timedelta(days=7)

    # Avoid importing inline in tests: conversation module already imported.
    from ..repositories import conversations_repo  # local to avoid import cycles

    # Build a set of customer_ids that already have active appointments.
    active_customers: set[str] = set()
    for appt in appointments_repo.list_for_business(business_id):
        status = getattr(appt, "status", "SCHEDULED").upper()
        if status in {"SCHEDULED", "CONFIRMED"} and getattr(appt, "customer_id", None):
            active_customers.add(appt.customer_id)

    sent = 0
    seen_customers: set[str] = set()

    for conv in conversations_repo.list_for_business(business_id):
        if not conv.customer_id or conv.customer_id in seen_customers:
            continue
        created_at = getattr(conv, "created_at", now)
        if created_at < window or created_at > now:
            continue
        if conv.customer_id in active_customers:
            continue
        customer = customers_repo.get(conv.customer_id)
        if (
            not customer
            or not customer.phone
            or getattr(customer, "sms_opt_out", False)
        ):
            continue

        when_str = created_at.strftime("%a %b %d")
        business_name = conversation.DEFAULT_BUSINESS_NAME
        if SQLALCHEMY_AVAILABLE and SessionLocal is not None:
            session_db = SessionLocal()
            try:
                row = session_db.get(BusinessDB, business_id)
            finally:
                session_db.close()
            if row is not None and getattr(row, "name", None):
                business_name = row.name

        body = (
            f"This is {business_name}. We spoke on {when_str} about work you might need. "
            "If you would like to book an appointment or have any questions, "
            "reply to this message or call us."
        )
        await sms_service.notify_customer(customer.phone, body, business_id=business_id)
        metrics.lead_followups_sent += 1
        per = metrics.sms_by_business.setdefault(business_id, BusinessSmsMetrics())
        per.lead_followups_sent += 1
        seen_customers.add(conv.customer_id)
        sent += 1

    return {"followups_sent": sent}
