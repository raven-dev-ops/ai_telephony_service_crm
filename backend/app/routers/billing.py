from __future__ import annotations

from datetime import UTC, datetime
from typing import List, Optional

import logging
import os

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field

from ..config import get_settings
from ..deps import ensure_business_active, require_owner_dashboard_auth
from ..db import SQLALCHEMY_AVAILABLE, SessionLocal
from ..db_models import BusinessDB
from ..metrics import metrics
from ..services import subscription as subscription_service
from ..services.stripe_webhook import (
    StripeReplayError,
    StripeSignatureError,
    check_replay,
    verify_stripe_signature,
)
from ..services.subscription import SubscriptionState

try:
    import stripe  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    stripe = None


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


class BillingPortalResponse(BaseModel):
    url: str


class BillingWebhookEvent(BaseModel):
    type: str
    data: dict


class SubscriptionStatusResponse(BaseModel):
    status: str
    plan: str | None = None
    current_period_end: datetime | None = None
    in_grace: bool = False
    grace_remaining_days: int = 0
    blocked: bool = False
    message: str | None = None
    usage_warnings: list[str] = Field(default_factory=list)
    calls_used: int = 0
    calls_limit: int | None = None
    appointments_used: int = 0
    appointments_limit: int | None = None
    enforce_subscription: bool = False


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
            features=[
                "Everything in Starter",
                "Analytics",
                "Owner AI assistant",
                "Widget",
            ],
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
    plan_id: Optional[str] = None,
) -> None:
    session = _require_db()
    try:
        row = session.get(BusinessDB, business_id)
        if not row:
            raise HTTPException(status_code=404, detail="Business not found")
        row.subscription_status = status
        if plan_id:
            row.service_tier = plan_id
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


def _get_stripe_client():
    settings = get_settings().stripe
    if stripe is None:
        raise HTTPException(status_code=503, detail="Stripe SDK not installed")
    if not settings.api_key:
        raise HTTPException(status_code=503, detail="Stripe API key not configured")
    stripe.api_key = settings.api_key
    return stripe


def _get_or_create_customer(business_id: str, email: Optional[str]) -> str:
    session = _require_db()
    try:
        row = session.get(BusinessDB, business_id)
        if not row:
            raise HTTPException(status_code=404, detail="Business not found")
        if row.stripe_customer_id:
            return row.stripe_customer_id
        client = _get_stripe_client()
        customer = client.Customer.create(
            email=email,
            metadata={"business_id": business_id},
        )
        row.stripe_customer_id = customer["id"]
        session.add(row)
        session.commit()
        return row.stripe_customer_id  # type: ignore[return-value]
    finally:
        session.close()


@router.post("/create-checkout-session", response_model=CheckoutSessionResponse)
def create_checkout_session(
    plan_id: str,
    customer_email: Optional[str] = None,
    business_id: str = Depends(ensure_business_active),
) -> CheckoutSessionResponse:
    """Return a Stripe Checkout session URL (prefers configured payment link)."""
    stripe_cfg = get_settings().stripe
    plans = {p.id: p for p in _plans_from_settings()}
    plan = plans.get(plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")

    if stripe_cfg.use_stub:
        if stripe_cfg.payment_link_url:
            return CheckoutSessionResponse(
                url=stripe_cfg.payment_link_url, session_id="stripe_payment_link"
            )
        url = f"https://example.com/checkout?plan={plan.id}&business_id={business_id}"
        return CheckoutSessionResponse(url=url, session_id=f"stub_{plan.id}")

    if not stripe_cfg.api_key:
        raise HTTPException(
            status_code=503, detail="Stripe API key not configured for live checkout"
        )

    if not plan.stripe_price_id:
        raise HTTPException(
            status_code=503, detail="Stripe price not configured for this plan"
        )

    client = _get_stripe_client()

    try:
        customer_id = _get_or_create_customer(business_id, customer_email)
        session = client.checkout.Session.create(
            mode="subscription",
            customer=customer_id,
            line_items=[{"price": plan.stripe_price_id, "quantity": 1}],
            success_url=stripe_cfg.checkout_success_url,
            cancel_url=stripe_cfg.checkout_cancel_url,
            metadata={"business_id": business_id, "plan_id": plan.id},
            subscription_data={
                "metadata": {"business_id": business_id, "plan_id": plan.id}
            },
        )
        return CheckoutSessionResponse(url=session.url, session_id=session.id)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("stripe_checkout_failed", extra={"business_id": business_id})
        raise HTTPException(status_code=502, detail="Stripe checkout failed") from exc


@router.get("/portal-link", response_model=BillingPortalResponse)
def get_billing_portal_link(
    business_id: str = Depends(ensure_business_active),
) -> BillingPortalResponse:
    """Return a pre-configured Stripe billing portal URL if available."""
    stripe_cfg = get_settings().stripe
    if stripe_cfg.billing_portal_url:
        return BillingPortalResponse(url=stripe_cfg.billing_portal_url)

    if stripe_cfg.use_stub:
        raise HTTPException(status_code=404, detail="Billing portal not configured")
    if not stripe_cfg.api_key:
        raise HTTPException(status_code=503, detail="Stripe API key not configured")

    client = _get_stripe_client()
    customer_id = _get_or_create_customer(business_id, None)
    return_url = stripe_cfg.billing_portal_return_url or stripe_cfg.checkout_success_url
    try:
        session = client.billing_portal.Session.create(
            customer=customer_id,
            return_url=return_url,
        )
        return BillingPortalResponse(url=session.url)
    except Exception as exc:
        logger.exception("stripe_portal_failed", extra={"business_id": business_id})
        raise HTTPException(status_code=502, detail="Stripe portal failed") from exc


def _status_from_subscription(
    event_type: str, obj: dict[str, object]
) -> SubscriptionState:
    status = str(obj.get("status") or "").lower() if isinstance(obj, dict) else ""
    if event_type == "customer.subscription.deleted":
        status = "canceled"
    elif event_type == "invoice.payment_failed":
        status = "past_due"
    elif event_type in {"invoice.payment_succeeded", "checkout.session.completed"}:
        status = status or "active"
    return SubscriptionState(status=status or "active")


def _plan_from_event_obj(obj: dict[str, object]) -> str | None:
    metadata = obj.get("metadata") if isinstance(obj, dict) else None
    plan_id = None
    if isinstance(metadata, dict):
        plan_id = metadata.get("plan_id") or metadata.get("plan")
    if not plan_id and isinstance(obj, dict):
        items = obj.get("items", {}) if isinstance(obj.get("items"), dict) else {}
        data_items = items.get("data") if isinstance(items, dict) else None
        if isinstance(data_items, list):
            for item in data_items:
                if not isinstance(item, dict):
                    continue
                price = item.get("price") if isinstance(item.get("price"), dict) else {}
                plan_id = (
                    price.get("lookup_key") or price.get("nickname") or price.get("id")
                )
                if plan_id:
                    break
    return plan_id


@router.get("/subscription/status", response_model=SubscriptionStatusResponse)
async def get_subscription_status(
    business_id: str = Depends(ensure_business_active),
) -> SubscriptionStatusResponse:
    """Return the current subscription state plus usage/limits for the tenant."""
    state = subscription_service.compute_state(business_id)
    usage = state.usage or subscription_service.UsageSnapshot()
    settings = get_settings()
    return SubscriptionStatusResponse(
        status=state.status,
        plan=state.plan,
        current_period_end=state.current_period_end,
        in_grace=state.in_grace,
        grace_remaining_days=state.grace_remaining_days,
        blocked=state.blocked,
        message=state.message,
        usage_warnings=state.usage_warnings,
        calls_used=usage.calls,
        calls_limit=usage.call_limit,
        appointments_used=usage.appointments,
        appointments_limit=usage.appointment_limit,
        enforce_subscription=getattr(settings, "enforce_subscription", False),
    )


@router.post("/webhook")
async def billing_webhook(
    request: Request,
    stripe_signature: str | None = Header(
        default=None, alias="Stripe-Signature", convert_underscores=False
    ),
):
    """Handle Stripe webhook events with signature verification and replay protection."""
    raw_body = await request.body()
    settings = get_settings().stripe
    env = os.getenv("ENVIRONMENT", "dev").lower()
    require_sig = bool(settings.verify_signatures or (env == "prod" and not settings.use_stub))
    event_type = ""
    event_id = ""
    business_id = "default_business"
    try:
        event_payload: dict = {}
        if require_sig and not settings.use_stub:
            if not stripe_signature:
                raise HTTPException(
                    status_code=400, detail="Missing Stripe-Signature header"
                )
            if not settings.webhook_secret:
                raise HTTPException(
                    status_code=503, detail="Stripe webhook secret not configured"
                )
            if stripe is not None:
                try:
                    event_payload = stripe.Webhook.construct_event(
                        payload=raw_body,
                        sig_header=stripe_signature,
                        secret=settings.webhook_secret,
                    )
                except Exception as exc:
                    logger.warning(
                        "billing_webhook_signature_invalid",
                        extra={"error": str(exc)},
                    )
                    raise HTTPException(
                        status_code=400, detail="Invalid webhook signature"
                    ) from exc
            else:
                # Fallback to local verifier if Stripe SDK is not installed.
                try:
                    verify_stripe_signature(
                        raw_body, stripe_signature, settings.webhook_secret
                    )
                except (StripeSignatureError, StripeReplayError) as exc:
                    logger.warning(
                        "billing_webhook_signature_invalid",
                        extra={"error": str(exc)},
                    )
                    raise HTTPException(
                        status_code=400, detail="Invalid webhook signature"
                    ) from exc

        if not event_payload:
            try:
                event_payload = await request.json()
            except Exception as exc:  # pragma: no cover - defensive log path
                logger.exception(
                    "billing_webhook_invalid_payload", extra={"error": str(exc)}
                )
                raise HTTPException(status_code=400, detail="Invalid payload") from exc

        event_type = event_payload.get("type", "")
        event_id = event_payload.get("id", "")
        data_obj = event_payload.get("data", {}).get("object", {})
        business_id = (
            data_obj.get("metadata", {}).get("business_id") or "default_business"
        )
        customer_id = data_obj.get("customer")
        subscription_id = data_obj.get("subscription") or data_obj.get("id")
        plan_id = _plan_from_event_obj(data_obj)
        period_end = data_obj.get("current_period_end")
        current_period_end = (
            datetime.fromtimestamp(period_end, UTC)
            if isinstance(period_end, (int, float))
            else None
        )

        if require_sig and not settings.use_stub and event_id and settings.replay_protection_seconds > 0:
            check_replay(event_id, settings.replay_protection_seconds)

        if event_type == "checkout.session.completed":
            _update_subscription(
                business_id,
                status="active",
                customer_id=customer_id,
                subscription_id=subscription_id,
                current_period_end=current_period_end,
                plan_id=plan_id,
            )
            metrics.subscription_activations += 1
            logger.info(
                "subscription_active",
                extra={
                    "business_id": business_id,
                    "customer_id": customer_id,
                    "subscription_id": subscription_id,
                    "period_end": (
                        current_period_end.isoformat() if current_period_end else None
                    ),
                },
            )
            return {"status": "ok"}
        elif event_type in {
            "customer.subscription.updated",
            "invoice.payment_succeeded",
        }:
            state = _status_from_subscription(event_type, data_obj)
            _update_subscription(
                business_id,
                status=state.status,
                customer_id=customer_id,
                subscription_id=subscription_id,
                current_period_end=current_period_end,
                plan_id=plan_id,
            )
            metrics.subscription_activations += 1
            if state.status not in {"active", "trialing"}:
                await subscription_service.notify_status_change(
                    business_id, subscription_service.compute_state(business_id)
                )
            return {"status": "ok"}
        elif event_type in {"invoice.payment_failed", "customer.subscription.deleted"}:
            _update_subscription(
                business_id,
                status=(
                    "past_due" if event_type == "invoice.payment_failed" else "canceled"
                ),
                customer_id=customer_id,
                subscription_id=subscription_id,
                current_period_end=current_period_end,
                plan_id=plan_id,
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
            await subscription_service.notify_status_change(
                business_id, subscription_service.compute_state(business_id)
            )
            return {"status": "ok"}
        else:
            # Ignore other events
            return {"received": True}
    except StripeReplayError as exc:
        metrics.billing_webhook_failures += 1
        logger.warning(
            "billing_webhook_replay_blocked",
            extra={
                "business_id": business_id,
                "event_id": event_id,
                "event_type": event_type,
                "error": str(exc),
            },
        )
        raise HTTPException(status_code=400, detail="Duplicate webhook event")
    except HTTPException:
        metrics.billing_webhook_failures += 1
        logger.warning(
            "billing_webhook_http_error",
            extra={
                "business_id": business_id,
                "event_id": event_id,
                "event_type": event_type,
            },
        )
        raise
    except Exception as exc:  # pragma: no cover - unexpected failures should be visible
        metrics.billing_webhook_failures += 1
        logger.exception(
            "billing_webhook_failure",
            extra={
                "business_id": business_id,
                "event_id": event_id,
                "event_type": event_type,
                "error": str(exc),
            },
        )
        raise HTTPException(status_code=500, detail="Webhook processing failed")
