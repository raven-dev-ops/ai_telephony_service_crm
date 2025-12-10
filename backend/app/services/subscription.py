from __future__ import annotations

from dataclasses import dataclass
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
    usage: UsageSnapshot | None = None


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
            if state.status not in {"active", "trialing"}:
                if state.current_period_end:
                    grace_end = state.current_period_end + timedelta(days=_grace_days())
                    if grace_end > datetime.now(UTC):
                        state.in_grace = True
                        state.grace_remaining_days = max(
                            0, (grace_end - datetime.now(UTC)).days
                        )
                state.blocked = (
                    getattr(settings, "enforce_subscription", False) and not state.in_grace
                )
    finally:
        session.close()

    return state


async def _notify_owner_if_needed(
    business: BusinessDB | None, state: SubscriptionState
) -> None:
    if not business:
        return
    owner_email = getattr(business, "owner_email", None)
    if not owner_email:
        return
    cache_key = f"{business.id}:{state.status}"
    last_sent = _reminder_cache.get(cache_key)
    interval = timedelta(hours=_reminder_interval_hours())
    now = datetime.now(UTC)
    if last_sent and now - last_sent < interval:
        return
    subject = f"Subscription attention needed ({state.status})"
    grace_note = ""
    if state.in_grace and state.grace_remaining_days:
        grace_note = f" You have {state.grace_remaining_days} day(s) remaining in grace."
    body = (
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
            "subscription_reminder_failed", exc_info=True, extra={"business_id": business.id}
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
) -> SubscriptionState:
    """Evaluate subscription state and enforce blocking/limits when enabled."""
    settings = get_settings()
    state = compute_state(business_id)

    # Early exit when enforcement is disabled.
    if not getattr(settings, "enforce_subscription", False):
        return state

    # Pull the business row when available for richer messaging/notifications.
    business = None
    if SQLALCHEMY_AVAILABLE and SessionLocal is not None:
        session = SessionLocal()
        try:
            business = session.get(BusinessDB, business_id)
        finally:
            session.close()

    if state.status not in {"active", "trialing"} and not state.in_grace:
        state.blocked = True
        state.message = "Subscription inactive. Please upgrade or resume billing."
        await _notify_owner_if_needed(business, state)
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=state.message,
            headers={"X-Subscription-Status": state.status},
        )

    # Enforce plan limits when present.
    limits = _plan_limits(state.plan)
    usage = state.usage or UsageSnapshot()
    projected_calls = usage.calls + upcoming_calls
    projected_appts = usage.appointments + upcoming_appointments

    call_limit = limits.get("monthly_calls")
    if call_limit is not None and projected_calls > call_limit:
        state.blocked = True
        state.message = (
            f"Call limit reached for plan {state.plan or 'starter'} "
            f"({projected_calls}/{call_limit})."
        )
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=state.message,
            headers={"X-Subscription-Status": state.status, "X-Plan-Limit": "calls"},
        )

    appt_limit = limits.get("monthly_appointments")
    if appt_limit is not None and projected_appts > appt_limit:
        state.blocked = True
        state.message = (
            f"Appointment limit reached for plan {state.plan or 'starter'} "
            f"({projected_appts}/{appt_limit})."
        )
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=state.message,
            headers={
                "X-Subscription-Status": state.status,
                "X-Plan-Limit": "appointments",
            },
        )

    state.blocked = False
    return state
