from __future__ import annotations

from datetime import UTC, datetime
from typing import List, Optional

import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel

from ..config import get_settings
from ..deps import ensure_business_active, require_owner_dashboard_auth
from ..db import SQLALCHEMY_AVAILABLE, SessionLocal
from ..db_models import BusinessDB
from ..metrics import metrics


router = APIRouter(dependencies=[Depends(require_owner_dashboard_auth)])
logger = logging.getLogger(__name__)


class Plan(BaseModel):
    id: str
    name: str
    interval: str
    price_cents: int
    stripe_price_id: Optional[str] = None
    features: List[str] = []


class CheckoutSessionResponse(BaseModel):
    url: str
    session_id: str
    mode: str = "subscription"


class BillingWebhookEvent(BaseModel):
    type: str
    data: dict


def _plans_from_settings() -> List[Plan]:
    stripe_cfg = get_settings().stripe
    return [
        Plan(
            id="basic",
            name="Starter",
            interval="month",
            price_cents=2000,
            stripe_price_id=stripe_cfg.price_basic,
            features=["Core scheduling", "CRM", "Voice assistant (dev)"],
        ),
        Plan(
            id="growth",
            name="Growth",
            interval="month",
            price_cents=10000,
            stripe_price_id=stripe_cfg.price_growth,
            features=["Everything in Starter", "Analytics", "Owner AI assistant", "Widget"],
        ),
        Plan(
            id="scale",
            name="Scale",
            interval="month",
            price_cents=20000,
            stripe_price_id=stripe_cfg.price_scale,
            features=["Everything in Growth", "Multi-tenant", "Priority support"],
        ),
    ]


@router.get("/plans", response_model=List[Plan])
def list_plans() -> List[Plan]:
    """Return available subscription plans."""
    return _plans_from_settings()


def _require_db():
    if not SQLALCHEMY_AVAILABLE or SessionLocal is None:
        raise HTTPException(status_code=503, detail="Database not available")
    return SessionLocal()


def _update_subscription(
    business_id: str,
    status: str,
    customer_id: Optional[str],
    subscription_id: Optional[str],
    current_period_end: Optional[datetime],
) -> None:
    session = _require_db()
    try:
        row = session.get(BusinessDB, business_id)
        if not row:
            raise HTTPException(status_code=404, detail="Business not found")
        row.subscription_status = status
        if customer_id:
            row.stripe_customer_id = customer_id
        if subscription_id:
            row.stripe_subscription_id = subscription_id
        if current_period_end:
            row.subscription_current_period_end = current_period_end
        session.add(row)
        session.commit()
    finally:
        session.close()


@router.post("/create-checkout-session", response_model=CheckoutSessionResponse)
def create_checkout_session(
    plan_id: str,
    customer_email: Optional[str] = None,
    business_id: str = Depends(ensure_business_active),
) -> CheckoutSessionResponse:
    """Return a Stripe Checkout session URL (stub unless STRIPE_API_KEY set)."""
    stripe_cfg = get_settings().stripe
    plans = {p.id: p for p in _plans_from_settings()}
    plan = plans.get(plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")

    if stripe_cfg.use_stub or not stripe_cfg.api_key or not plan.stripe_price_id:
        url = f"https://example.com/checkout?plan={plan.id}&business_id={business_id}"
        return CheckoutSessionResponse(url=url, session_id=f"stub_{plan.id}")

    # Real Stripe call would go here; returning a placeholder for now.
    url = f"https://checkout.stripe.com/pay/{plan.stripe_price_id}"
    return CheckoutSessionResponse(url=url, session_id=f"session_{plan.stripe_price_id}")


@router.post("/webhook")
async def billing_webhook(
    request: Request,
    stripe_signature: str | None = Header(default=None, convert_underscores=False),
):
    """Handle Stripe webhook events (lightweight, no signature verification in stub)."""
    try:
        payload = await request.json()
    except Exception as exc:  # pragma: no cover - defensive log path
        metrics.billing_webhook_failures += 1
        logger.exception("billing_webhook_invalid_payload", extra={"error": str(exc)})
        raise HTTPException(status_code=400, detail="Invalid payload")

    event_type = payload.get("type", "")
    data_obj = payload.get("data", {}).get("object", {})
    business_id = (
        data_obj.get("metadata", {}).get("business_id") or "default_business"
    )
    customer_id = data_obj.get("customer")
    subscription_id = data_obj.get("subscription") or data_obj.get("id")
    period_end = data_obj.get("current_period_end")
    current_period_end = (
        datetime.fromtimestamp(period_end, UTC) if isinstance(period_end, (int, float)) else None
    )

    try:
        if event_type == "checkout.session.completed":
            _update_subscription(
                business_id,
                status="active",
                customer_id=customer_id,
                subscription_id=subscription_id,
                current_period_end=current_period_end,
            )
            metrics.subscription_activations += 1
            logger.info(
                "subscription_active",
                extra={
                    "business_id": business_id,
                    "customer_id": customer_id,
                    "subscription_id": subscription_id,
                    "period_end": current_period_end.isoformat()
                    if current_period_end
                    else None,
                },
            )
            return {"status": "ok"}
        elif event_type in {"invoice.payment_failed", "customer.subscription.deleted"}:
            _update_subscription(
                business_id,
                status="past_due"
                if event_type == "invoice.payment_failed"
                else "canceled",
                customer_id=customer_id,
                subscription_id=subscription_id,
                current_period_end=current_period_end,
            )
            metrics.subscription_failures += 1
            logger.warning(
                "subscription_updated_with_issue",
                extra={
                    "business_id": business_id,
                    "customer_id": customer_id,
                    "subscription_id": subscription_id,
                    "event_type": event_type,
                },
            )
            return {"status": "ok"}
        else:
            # Ignore other events
            return {"received": True}
    except HTTPException:
        metrics.billing_webhook_failures += 1
        logger.warning(
            "billing_webhook_http_error",
            extra={"business_id": business_id, "event_type": event_type},
        )
        raise
    except Exception as exc:  # pragma: no cover - unexpected failures should be visible
        metrics.billing_webhook_failures += 1
        logger.exception(
            "billing_webhook_failure",
            extra={
                "business_id": business_id,
                "event_type": event_type,
                "error": str(exc),
            },
        )
        raise HTTPException(status_code=500, detail="Webhook processing failed")
