from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from ..business_config import get_calendar_id_for_business, get_language_for_business
from ..repositories import appointments_repo, customers_repo, conversations_repo
from ..services.calendar import TimeSlot, calendar_service
from ..services.email_service import email_service
from ..services.sms import sms_service

logger = logging.getLogger(__name__)


@dataclass
class ActionResult:
    code: str
    message: str
    appointment_id: str | None = None


def _append_action_message(
    conversation_id: str | None,
    action: str,
    appointment_id: str,
    metadata: dict[str, Any],
) -> None:
    """Record a structured action entry in the conversation transcript."""

    if not conversation_id:
        return
    payload = {"action": action, "appointment_id": appointment_id, **metadata}
    try:
        body = json.dumps(payload, default=str)
    except Exception:
        body = str(payload)
    conversations_repo.append_message(conversation_id, role="action", text=body)


async def _notify_customer(
    *,
    appointment_id: str,
    business_id: str,
    customer_id: str | None,
    body: str,
    subject: str | None = None,
) -> None:
    """Best-effort customer notification via SMS/email."""

    if not customer_id:
        return
    customer = customers_repo.get(customer_id)
    if not customer:
        return
    phone = getattr(customer, "phone", None)
    email = getattr(customer, "email", None)
    sms_opt_out = bool(getattr(customer, "sms_opt_out", False))

    if phone and not sms_opt_out:
        await sms_service.send_sms(
            to=phone, body=body, business_id=business_id, category="customer"
        )
    if email and subject:
        await email_service.send_email(
            to=email,
            subject=subject,
            body=body,
            business_id=business_id,
        )


async def cancel_appointment(
    *,
    appointment_id: str,
    business_id: str,
    actor: str,
    conversation_id: str | None = None,
    reason: str | None = None,
    notify_customer: bool = True,
) -> ActionResult:
    """Cancel an appointment atomically and idempotently."""

    appt = appointments_repo.get(appointment_id)
    if not appt or getattr(appt, "business_id", business_id) != business_id:
        return ActionResult(code="not_found", message="Appointment not found")

    status = (getattr(appt, "status", "SCHEDULED") or "").upper()
    if status == "CANCELLED":
        return ActionResult(
            code="already_cancelled",
            message="Appointment already cancelled",
            appointment_id=appointment_id,
        )

    # Attempt to remove the calendar event first; failures are tolerated.
    if getattr(appt, "calendar_event_id", None):
        calendar_id = get_calendar_id_for_business(business_id)
        try:
            await calendar_service.delete_event(
                event_id=appt.calendar_event_id,
                calendar_id=calendar_id,
                business_id=business_id,
            )
        except Exception:
            logger.warning(
                "calendar_delete_event_failed",
                exc_info=True,
                extra={"appointment_id": appointment_id, "business_id": business_id},
            )

    job_stage = getattr(appt, "job_stage", None) or "Cancelled"
    appointments_repo.update(
        appointment_id,
        status="CANCELLED",
        job_stage=job_stage,
    )

    when = getattr(appt, "start_time", None)
    when_str = (
        when.astimezone(UTC).strftime("%Y-%m-%d %H:%M %Z") if when else "upcoming time"
    )
    language = get_language_for_business(business_id)
    if language == "es":
        body = f"Tu cita programada ({when_str}) ha sido cancelada."
    else:
        body = f"Your appointment scheduled for {when_str} has been cancelled."
    if reason:
        body = f"{body} Reason: {reason}"

    if notify_customer:
        await _notify_customer(
            appointment_id=appointment_id,
            business_id=business_id,
            customer_id=getattr(appt, "customer_id", None),
            body=body,
            subject="Appointment cancelled",
        )

    _append_action_message(
        conversation_id,
        "cancel",
        appointment_id,
        {
            "actor": actor,
            "reason": reason,
            "timestamp": datetime.now(UTC).isoformat(),
        },
    )
    return ActionResult(
        code="cancelled",
        message="Appointment cancelled",
        appointment_id=appointment_id,
    )


async def reschedule_appointment(
    *,
    appointment_id: str,
    business_id: str,
    new_start: datetime,
    new_end: datetime,
    actor: str,
    conversation_id: str | None = None,
    technician_id: str | None = None,
    address: str | None = None,
    is_emergency: bool = False,
    notify_customer: bool = True,
    service_type: str | None = None,
    description: str | None = None,
) -> ActionResult:
    """Safely reschedule an appointment, updating calendar + CRM with conflict checks."""

    appt = appointments_repo.get(appointment_id)
    if not appt or getattr(appt, "business_id", business_id) != business_id:
        return ActionResult(code="not_found", message="Appointment not found")

    if new_start >= new_end:
        return ActionResult(code="invalid_range", message="Start must be before end")

    # Idempotent short-circuit when times are unchanged.
    if (
        getattr(appt, "start_time", None) == new_start
        and getattr(appt, "end_time", None) == new_end
    ):
        return ActionResult(
            code="no_change",
            message="Appointment already at requested time",
            appointment_id=appointment_id,
        )

    # Conflict check before touching calendar or CRM state.
    if calendar_service.has_conflict(
        business_id=business_id,
        start=new_start,
        end=new_end,
        technician_id=technician_id or getattr(appt, "technician_id", None),
        address=address,
        is_emergency=is_emergency,
    ):
        return ActionResult(
            code="conflict", message="Requested time conflicts with another booking"
        )

    # Update calendar first to avoid duplicate bookings; fall back to stub/false gracefully.
    updated_event = True
    if getattr(appt, "calendar_event_id", None):
        calendar_id = get_calendar_id_for_business(business_id)
        slot = TimeSlot(start=new_start, end=new_end)
        try:
            updated_event = await calendar_service.update_event(
                event_id=appt.calendar_event_id,
                slot=slot,
                summary=service_type or getattr(appt, "service_type", None),
                description=(
                    description
                    if description is not None
                    else getattr(appt, "description", None)
                ),
                calendar_id=calendar_id,
                business_id=business_id,
            )
        except Exception:
            updated_event = False
    if not updated_event:
        return ActionResult(
            code="calendar_error", message="Calendar update failed; no changes applied"
        )

    updated = appointments_repo.update(
        appointment_id,
        start_time=new_start,
        end_time=new_end,
        status="SCHEDULED",
        job_stage="Rescheduled",
    )
    if not updated:
        return ActionResult(
            code="not_found", message="Appointment not found after update"
        )

    when_str = new_start.astimezone(UTC).strftime("%Y-%m-%d %H:%M %Z")
    language = get_language_for_business(business_id)
    if language == "es":
        body = f"Tu cita ha sido reprogramada para {when_str}."
    else:
        body = f"Your appointment has been rescheduled to {when_str}."

    if notify_customer:
        await _notify_customer(
            appointment_id=appointment_id,
            business_id=business_id,
            customer_id=getattr(appt, "customer_id", None),
            body=body,
            subject="Appointment rescheduled",
        )

    _append_action_message(
        conversation_id,
        "reschedule",
        appointment_id,
        {
            "actor": actor,
            "previous_start": getattr(appt, "start_time", None),
            "previous_end": getattr(appt, "end_time", None),
            "new_start": new_start,
            "new_end": new_end,
            "timestamp": datetime.now(UTC).isoformat(),
        },
    )
    return ActionResult(
        code="rescheduled",
        message="Appointment rescheduled",
        appointment_id=appointment_id,
    )


async def mark_pending_reschedule(
    *,
    appointment_id: str,
    business_id: str,
    actor: str,
    conversation_id: str | None = None,
) -> ActionResult:
    """Mark appointment for reschedule with audit logging."""

    appt = appointments_repo.get(appointment_id)
    if not appt or getattr(appt, "business_id", business_id) != business_id:
        return ActionResult(code="not_found", message="Appointment not found")

    status = (getattr(appt, "status", "SCHEDULED") or "").upper()
    if status == "PENDING_RESCHEDULE":
        return ActionResult(
            code="already_pending",
            message="Appointment already pending reschedule",
            appointment_id=appointment_id,
        )

    current_stage = getattr(appt, "job_stage", None) or "Pending Reschedule"
    appointments_repo.update(
        appointment_id, status="PENDING_RESCHEDULE", job_stage=current_stage
    )

    _append_action_message(
        conversation_id,
        "reschedule_requested",
        appointment_id,
        {"actor": actor, "timestamp": datetime.now(UTC).isoformat()},
    )
    return ActionResult(
        code="pending_reschedule",
        message="Appointment marked for reschedule",
        appointment_id=appointment_id,
    )
