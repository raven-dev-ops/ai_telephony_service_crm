from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
import logging
from typing import Dict, Optional

from fastapi import HTTPException, status

from ..config import get_settings
from ..db import SQLALCHEMY_AVAILABLE, SessionLocal
from ..db_models import BusinessDB
from ..metrics import BusinessVoiceSessionMetrics, metrics
from ..repositories import appointments_repo
from .email_service import email_service

logger = logging.getLogger(__name__)

# Simple, opinionated plan limits. These can be made configurable via environment
# later; for now they give us deterministic guardrails and UI messaging.
PLAN_LIMITS: Dict[str, Dict[str, Optional[int]]] = {
    "starter": {"monthly_calls": 200, "monthly_appointments": 50},
    "basic": {"monthly_calls": 200, "monthly_appointments": 50},
    "growth": {"monthly_calls": 1000, "monthly_appointments": 200},
    "scale": {"monthly_calls": 5000, "monthly_appointments": 1000},
}

# Cache to avoid spamming reminder notifications.
_reminder_cache: Dict[str, datetime] = {}


@dataclass
class UsageSnapshot:
    calls: int = 0
    call_limit: Optional[int] = None
    appointments: int = 0
    appointment_limit: Optional[int] = None


@dataclass
class SubscriptionState:
    status: str = "active"
    plan: str | None = None
    current_period_end: datetime | None = None
    in_grace: bool = False
    grace_remaining_days: int = 0
    blocked: bool = False
    message: str | None = None
    block_reason: str | None = None
    degraded_mode: str | None = None
    feature: str | None = None
    usage: UsageSnapshot | None = None
    usage_warnings: list[str] = field(default_factory=list)


def _grace_days() -> int:
    settings = get_settings()
    return int(getattr(settings, "subscription_grace_days", 5))


def _reminder_interval_hours() -> int:
    settings = get_settings()
    return int(getattr(settings, "subscription_reminder_hours", 12))


def _plan_limits(plan: str | None) -> Dict[str, Optional[int]]:
    if not plan:
        return PLAN_LIMITS.get("starter", {})
    return PLAN_LIMITS.get(plan.lower(), PLAN_LIMITS.get("starter", {}))


def _usage_snapshot(business_id: str) -> UsageSnapshot:
    per = metrics.voice_sessions_by_business.get(
        business_id, BusinessVoiceSessionMetrics()
    )
    calls = per.requests

    # Appointment counts are derived from currently stored appointments. In
    # in-memory mode this reflects the running process; when backed by a DB it
    # includes persisted data.
    appointments = 0
    try:
        appointments = len(appointments_repo.list_for_business(business_id))
    except Exception:
        appointments = 0

    return UsageSnapshot(calls=calls, appointments=appointments)


def _collect_usage_warnings(
    usage: UsageSnapshot, limits: Dict[str, Optional[int]]
) -> list[str]:
    """Return soft warnings when usage is approaching limits."""
    warnings: list[str] = []
    call_limit = limits.get("monthly_calls")
    if call_limit:
        ratio = usage.calls / call_limit if call_limit else 0
        if ratio >= 0.9:
            warnings.append(
                f"Calls at {usage.calls}/{call_limit}; upgrade to avoid suspension."
            )
    appt_limit = limits.get("monthly_appointments")
    if appt_limit:
        ratio = usage.appointments / appt_limit if appt_limit else 0
        if ratio >= 0.9:
            warnings.append(
                f"Appointments at {usage.appointments}/{appt_limit}; upgrade to avoid suspension."
            )
    return warnings


def compute_state(business_id: str) -> SubscriptionState:
    settings = get_settings()
    state = SubscriptionState()
    state.usage = _usage_snapshot(business_id)

    # When subscriptions are not enforced we still surface status for UX but do
    # not block.
    state.blocked = False

    if not (SQLALCHEMY_AVAILABLE and SessionLocal is not None):
        return state

    session = SessionLocal()
    try:
        row = session.get(BusinessDB, business_id)
        if row:
            state.plan = getattr(row, "service_tier", None)
            state.status = getattr(row, "subscription_status", None) or "active"
            period_end = getattr(row, "subscription_current_period_end", None)
            if period_end and period_end.tzinfo is None:
                period_end = period_end.replace(tzinfo=UTC)
            state.current_period_end = period_end
            limits = _plan_limits(state.plan)
            if state.usage:
                state.usage.call_limit = limits.get("monthly_calls")
                state.usage.appointment_limit = limits.get("monthly_appointments")
                state.usage_warnings = _collect_usage_warnings(state.usage, limits)
            if state.status not in {"active", "trialing"}:
                if state.current_period_end:
                    grace_end = state.current_period_end + timedelta(days=_grace_days())
                    if grace_end > datetime.now(UTC):
                        state.in_grace = True
                        state.grace_remaining_days = max(
                            0, (grace_end - datetime.now(UTC)).days
                        )
                state.blocked = (
                    getattr(settings, "enforce_subscription", False)
                    and not state.in_grace
                )
    finally:
        session.close()

    return state


async def _notify_owner_if_needed(
    business: BusinessDB | None,
    state: SubscriptionState,
    *,
    status_override: str | None = None,
    message_override: str | None = None,
) -> None:
    if not business:
        return
    owner_email = getattr(business, "owner_email", None)
    if not owner_email:
        return
    cache_status = status_override or state.status
    cache_key = f"{business.id}:{cache_status}"
    last_sent = _reminder_cache.get(cache_key)
    interval = timedelta(hours=_reminder_interval_hours())
    now = datetime.now(UTC)
    if last_sent and now - last_sent < interval:
        return
    subject = f"Subscription attention needed ({cache_status})"
    grace_note = ""
    if state.in_grace and state.grace_remaining_days:
        grace_note = (
            f" You have {state.grace_remaining_days} day(s) remaining in grace."
        )
    body = message_override or (
        f"Your subscription status is '{state.status}'."
        f"{grace_note} Please update billing to avoid interruptions."
    )
    try:
        await email_service.notify_owner(
            subject,
            body,
            business_id=business.id,
            owner_email=owner_email,
        )
        _reminder_cache[cache_key] = now
    except Exception:
        logger.warning(
            "subscription_reminder_failed",
            exc_info=True,
            extra={"business_id": business.id},
        )


async def notify_status_change(business_id: str, state: SubscriptionState) -> None:
    """Best-effort owner notification for subscription state transitions."""
    if not (SQLALCHEMY_AVAILABLE and SessionLocal is not None):
        return
    session = SessionLocal()
    try:
        business = session.get(BusinessDB, business_id)
    finally:
        session.close()
    await _notify_owner_if_needed(business, state)


async def check_access(
    business_id: str,
    *,
    feature: str = "core",
    upcoming_calls: int = 0,
    upcoming_appointments: int = 0,
    graceful: bool = False,
) -> SubscriptionState:
    """Evaluate subscription state and enforce blocking/limits when enabled."""
    settings = get_settings()
    state = compute_state(business_id)
    state.feature = feature

    # Even when enforcement is disabled, send reminders for non-active states and renewals.
    if not getattr(settings, "enforce_subscription", False):
        if state.status not in {"active", "trialing"}:
            state.message = (
                state.message
                or "Subscription inactive; update billing to avoid suspension."
            )
            if state.in_grace and state.grace_remaining_days:
                state.message = f"Payment past due. Grace ends in {state.grace_remaining_days} day(s)."
            if SQLALCHEMY_AVAILABLE and SessionLocal is not None:
                session = SessionLocal()
                try:
                    business = session.get(BusinessDB, business_id)
                finally:
                    session.close()
                await _notify_owner_if_needed(business, state)
        else:
            expiring_window = timedelta(days=_grace_days())
            if (
                state.current_period_end
                and state.current_period_end <= datetime.now(UTC) + expiring_window
            ):
                if SQLALCHEMY_AVAILABLE and SessionLocal is not None:
                    session = SessionLocal()
                    try:
                        business = session.get(BusinessDB, business_id)
                    finally:
                        session.close()
                    await _notify_owner_if_needed(
                        business,
                        state,
                        status_override="expiring_soon",
                        message_override="Subscription renews soon; confirm payment to avoid interruption.",
                    )
        return state

    # Pull the business row when available for richer messaging/notifications.
    business = None
    if SQLALCHEMY_AVAILABLE and SessionLocal is not None:
        session = SessionLocal()
        try:
            business = session.get(BusinessDB, business_id)
        finally:
            session.close()

    if state.status not in {"active", "trialing"}:
        if state.in_grace:
            state.message = state.message or (
                f"Payment past due. Grace ends in {state.grace_remaining_days} day(s)."
            )
            await _notify_owner_if_needed(
                business, state, message_override=state.message
            )
            return state
        state.blocked = True
        state.block_reason = "inactive"
        state.degraded_mode = "voicemail_only" if feature == "calls" else "read_only"
        state.message = state.message or (
            "Subscription inactive. Calls will be routed to voicemail and automation is paused."
            if feature == "calls"
            else "Subscription inactive. Please upgrade or resume billing."
        )
        await _notify_owner_if_needed(business, state)
        if graceful:
            return state
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=state.message,
            headers={"X-Subscription-Status": state.status},
        )

    # Proactive reminder when the period end is approaching.
    expiring_window = timedelta(days=_grace_days())
    if (
        state.current_period_end
        and state.status in {"active", "trialing"}
        and state.current_period_end <= datetime.now(UTC) + expiring_window
    ):
        state.message = state.message or (
            "Subscription renews soon; confirm payment to avoid interruption."
        )
        await _notify_owner_if_needed(
            business,
            state,
            status_override="expiring_soon",
            message_override=state.message,
        )

    # Enforce plan limits when present.
    limits = _plan_limits(state.plan)
    usage = state.usage or UsageSnapshot()
    projected_calls = usage.calls + upcoming_calls
    projected_appts = usage.appointments + upcoming_appointments

    call_limit = limits.get("monthly_calls")
    if call_limit is not None and projected_calls > call_limit:
        warning = (
            f"Call limit reached for plan {state.plan or 'starter'} "
            f"({projected_calls}/{call_limit})."
        )
        state.usage_warnings.append(warning)
        state.blocked = True
        state.block_reason = "call_limit"
        state.degraded_mode = "voicemail_only" if feature == "calls" else "read_only"
        state.message = warning
        if graceful:
            return state
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=state.message,
            headers={"X-Subscription-Status": state.status, "X-Plan-Limit": "calls"},
        )

    appt_limit = limits.get("monthly_appointments")
    if appt_limit is not None and projected_appts > appt_limit:
        warning = (
            f"Appointment limit reached for plan {state.plan or 'starter'} "
            f"({projected_appts}/{appt_limit})."
        )
        state.usage_warnings.append(warning)
        state.blocked = True
        state.block_reason = "appointment_limit"
        state.degraded_mode = "read_only"
        state.message = warning
        if graceful:
            return state
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=state.message,
            headers={
                "X-Subscription-Status": state.status,
                "X-Plan-Limit": "appointments",
            },
        )

    if state.usage_warnings and not state.message:
        state.message = state.usage_warnings[0]

    state.blocked = False
    return state
