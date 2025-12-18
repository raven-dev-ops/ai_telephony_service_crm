from __future__ import annotations

from datetime import UTC, datetime, timedelta
import time
from typing import Optional
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
import logging
import httpx

from ..config import get_settings
from ..deps import ensure_business_active, require_owner_dashboard_auth
from ..db import SQLALCHEMY_AVAILABLE, SessionLocal
from ..db_models import BusinessDB
from ..repositories import customers_repo
from ..metrics import metrics
from ..services.job_queue import job_queue


router = APIRouter(dependencies=[Depends(require_owner_dashboard_auth)])
logger = logging.getLogger(__name__)


class QboAuthorizeResponse(BaseModel):
    authorization_url: str
    state: str


class QboCallbackResponse(BaseModel):
    connected: bool
    business_id: str
    realm_id: Optional[str] = None


class QboStatusResponse(BaseModel):
    connected: bool
    realm_id: Optional[str] = None
    token_expires_at: Optional[datetime] = None


class QboSyncResponse(BaseModel):
    customers_pushed: int = 0
    receipts_pushed: int = 0
    skipped: int = 0
    note: str


def _require_db():
    if not SQLALCHEMY_AVAILABLE or SessionLocal is None:
        raise HTTPException(
            status_code=503, detail="Database support is required for QuickBooks."
        )
    return SessionLocal()


def _mark_connected(
    business_id: str,
    realm_id: str | None,
    access_token: str,
    refresh_token: str,
    expires_in: int | None = None,
) -> None:
    session = _require_db()
    try:
        row = session.get(BusinessDB, business_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Business not found")
        row.integration_qbo_status = "connected"
        row.qbo_realm_id = realm_id
        row.qbo_access_token = access_token
        row.qbo_refresh_token = refresh_token
        expiry_seconds = expires_in if expires_in and expires_in > 0 else 3600
        row.qbo_token_expires_at = datetime.now(UTC) + timedelta(seconds=expiry_seconds)
        session.add(row)
        session.commit()
    finally:
        session.close()


def _get_status(business_id: str) -> QboStatusResponse:
    if SQLALCHEMY_AVAILABLE and SessionLocal is not None:
        session = SessionLocal()
        try:
            row = session.get(BusinessDB, business_id)
            if row:
                return QboStatusResponse(
                    connected=(
                        getattr(row, "integration_qbo_status", "") == "connected"
                    ),
                    realm_id=getattr(row, "qbo_realm_id", None),
                    token_expires_at=getattr(row, "qbo_token_expires_at", None),
                )
        finally:
            session.close()
    return QboStatusResponse(connected=False, realm_id=None, token_expires_at=None)


def _refresh_tokens(business_id: str) -> None:
    """Refresh QBO tokens and extend expiry."""
    session = _require_db()
    try:
        row = session.get(BusinessDB, business_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Business not found")
        refresh_token = getattr(row, "qbo_refresh_token", None)
        if not refresh_token:
            raise HTTPException(status_code=400, detail="No refresh token available")
        settings = get_settings().quickbooks
        client_id = settings.client_id
        client_secret = settings.client_secret
        token_url = settings.token_base
        if client_id and client_secret:
            data = {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            }
            try:
                auth = (client_id, client_secret)
                with httpx.Client(timeout=8.0) as client:
                    resp = client.post(token_url, data=data, auth=auth)
                if resp.status_code == 200:
                    payload = resp.json()
                    row.qbo_access_token = payload.get(
                        "access_token", row.qbo_access_token
                    )
                    row.qbo_refresh_token = payload.get(
                        "refresh_token", row.qbo_refresh_token
                    )
                    expires_in = int(payload.get("expires_in") or 3600)
                    row.qbo_token_expires_at = datetime.now(UTC) + timedelta(
                        seconds=expires_in
                    )
                    session.add(row)
                    session.commit()
                    return
                logger.warning(
                    "qbo_refresh_failed_http",
                    extra={
                        "business_id": business_id,
                        "status": resp.status_code,
                        "body": resp.text,
                    },
                )
                raise HTTPException(status_code=502, detail="QuickBooks refresh failed")
            except Exception as exc:  # pragma: no cover - defensive logging
                logger.warning(
                    "qbo_refresh_exception",
                    exc_info=True,
                    extra={"business_id": business_id, "error": str(exc)},
                )
                raise HTTPException(status_code=502, detail="QuickBooks refresh failed")

        # Fallback stub refresh path only when not configured for live.
        new_access = f"access_{int(datetime.now(UTC).timestamp())}"
        row.qbo_access_token = new_access
        row.qbo_token_expires_at = datetime.now(UTC) + timedelta(hours=1)
        session.add(row)
        session.commit()
    finally:
        session.close()


def _require_connection(business_id: str) -> BusinessDB:
    session = _require_db()
    try:
        row = session.get(BusinessDB, business_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Business not found")
        if getattr(row, "integration_qbo_status", "") != "connected":
            raise HTTPException(status_code=400, detail="QuickBooks is not connected.")
        return row
    finally:
        session.close()


def _qbo_base_url() -> str:
    qb = get_settings().quickbooks
    return (
        "https://sandbox-quickbooks.api.intuit.com"
        if getattr(qb, "sandbox", True)
        else "https://quickbooks.api.intuit.com"
    )


def _qbo_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def _qbo_request(
    method: str, url: str, headers: dict, json: dict | None = None
) -> httpx.Response:
    with httpx.Client(timeout=10.0) as client:
        resp = client.request(method, url, headers=headers, json=json)
    return resp


def _push_customer_and_receipt(
    *,
    business_id: str,
    realm_id: str,
    access_token: str,
) -> tuple[int, int, int]:
    """Push latest appointment as a customer + sales receipt. Returns (customers, receipts, skipped)."""
    from ..repositories import appointments_repo  # local import

    appts = appointments_repo.list_for_business(business_id)
    if not appts:
        return (0, 0, 1)
    appts.sort(key=lambda a: getattr(a, "start_time", datetime.min))
    appt = appts[-1]
    customer = (
        customers_repo.get(appt.customer_id)
        if getattr(appt, "customer_id", None)
        else None
    )
    display_name = getattr(customer, "name", None) or "QBO Customer"
    email = getattr(customer, "email", None)
    phone = getattr(customer, "phone", None)

    base = _qbo_base_url()
    headers = _qbo_headers(access_token)
    customer_url = f"{base}/v3/company/{realm_id}/customer?minorversion=65"
    customer_body = {"DisplayName": display_name}
    if phone:
        customer_body["PrimaryPhone"] = {"FreeFormNumber": phone}
    if email:
        customer_body["PrimaryEmailAddr"] = {"Address": email}

    resp_customer = _qbo_request("POST", customer_url, headers, json=customer_body)
    if resp_customer.status_code not in {200, 201}:
        logger.warning(
            "qbo_customer_push_failed",
            extra={
                "business_id": business_id,
                "status": resp_customer.status_code,
                "body": resp_customer.text,
            },
        )
        raise HTTPException(status_code=502, detail="QuickBooks customer push failed")
    customer_id = None
    try:
        payload = resp_customer.json()
        customer_id = payload.get("Customer", {}).get("Id")
    except Exception:
        customer_id = None

    receipt_body = {
        "TxnDate": datetime.now(UTC).date().isoformat(),
        "CustomerRef": {"value": customer_id or "1", "name": display_name},
        "Line": [
            {
                "Amount": 100.0,
                "DetailType": "SalesItemLineDetail",
                "SalesItemLineDetail": {"ItemRef": {"value": "1", "name": "Service"}},
                "Description": getattr(appt, "description", None) or "Service",
            }
        ],
    }
    sales_url = f"{base}/v3/company/{realm_id}/salesreceipt?minorversion=65"
    resp_receipt = _qbo_request("POST", sales_url, headers, json=receipt_body)
    if resp_receipt.status_code not in {200, 201}:
        logger.warning(
            "qbo_receipt_push_failed",
            extra={
                "business_id": business_id,
                "status": resp_receipt.status_code,
                "body": resp_receipt.text,
            },
        )
        raise HTTPException(
            status_code=502, detail="QuickBooks sales receipt push failed"
        )

    return (1, 1, 0)


@router.get("/authorize", response_model=QboAuthorizeResponse)
def authorize_qbo(
    business_id: str = Depends(ensure_business_active),
) -> QboAuthorizeResponse:
    """Return the QuickBooks Online authorization URL for this tenant."""
    settings = get_settings().quickbooks
    if not settings.client_id or not settings.redirect_uri:
        raise HTTPException(
            status_code=503,
            detail="QuickBooks credentials are not configured.",
        )
    logger.info("qbo_authorize_start", extra={"business_id": business_id})
    params = {
        "client_id": settings.client_id,
        "redirect_uri": settings.redirect_uri,
        "response_type": "code",
        "scope": settings.scopes,
        "state": business_id,
    }
    url = f"{settings.authorize_base}?{urlencode(params)}"
    return QboAuthorizeResponse(authorization_url=url, state=business_id)


@router.get("/callback", response_model=QboCallbackResponse)
def callback_qbo(
    code: str = Query(..., description="Authorization code from Intuit"),
    realmId: str | None = Query(
        default=None, description="QuickBooks company realm ID"
    ),
    state: str = Query(..., description="Opaque state containing business_id"),
) -> QboCallbackResponse:
    """Handle the QuickBooks OAuth callback and store tokens."""
    business_id = state
    settings = get_settings().quickbooks

    configured = (
        settings.client_id
        and settings.client_secret
        and getattr(settings, "token_base", None)
        and settings.redirect_uri
    )

    if configured:
        token_url = settings.token_base
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": settings.redirect_uri,
        }
        try:
            auth = (settings.client_id, settings.client_secret)
            with httpx.Client(timeout=8.0) as client:
                resp = client.post(token_url, data=data, auth=auth)
            if resp.status_code != 200:
                logger.warning(
                    "qbo_token_exchange_failed",
                    extra={
                        "business_id": business_id,
                        "status": resp.status_code,
                        "body": resp.text,
                    },
                )
                raise HTTPException(
                    status_code=502, detail="QuickBooks token exchange failed"
                )
            payload = resp.json()
            access_token = payload.get("access_token")
            refresh_token = payload.get("refresh_token")
            expires_in = int(payload.get("expires_in") or 3600)
            if not access_token or not refresh_token:
                raise HTTPException(
                    status_code=502, detail="QuickBooks token exchange missing tokens"
                )
            _mark_connected(
                business_id, realmId, access_token, refresh_token, expires_in=expires_in
            )
        except HTTPException:
            raise
        except Exception as exc:
            logger.exception(
                "qbo_callback_unexpected_error",
                extra={"business_id": business_id, "error": str(exc)},
            )
            raise HTTPException(status_code=502, detail="QuickBooks callback failed")
    else:
        # Stub path when not configured.
        fake_access = f"access_{code}"
        fake_refresh = f"refresh_{code}"
        _mark_connected(
            business_id, realmId, fake_access, fake_refresh, expires_in=None
        )

    metrics.qbo_connections += 1
    logger.info(
        "qbo_connected",
        extra={
            "business_id": business_id,
            "realm_id": realmId,
        },
    )
    return QboCallbackResponse(
        connected=True,
        business_id=business_id,
        realm_id=realmId,
    )


@router.get("/status", response_model=QboStatusResponse)
def qbo_status(business_id: str = Depends(ensure_business_active)) -> QboStatusResponse:
    """Return current QuickBooks connection status for this tenant."""
    return _get_status(business_id)


@router.post("/sync", response_model=QboSyncResponse)
def qbo_sync_contacts(
    business_id: str = Depends(ensure_business_active),
    enqueue: bool | None = Query(
        default=False,
        description="Run sync in background when true (defaults to inline for predictability).",
    ),
) -> QboSyncResponse:
    """Push customers/appointments into QuickBooks as customers + sales receipts."""
    status = _get_status(business_id)
    if not status.connected:
        metrics.qbo_sync_errors += 1
        logger.warning(
            "qbo_sync_attempt_without_connection",
            extra={"business_id": business_id},
        )
        raise HTTPException(status_code=400, detail="QuickBooks is not connected.")

    settings = get_settings().quickbooks
    if not (settings.client_id and settings.client_secret and settings.token_base):
        return QboSyncResponse(
            customers_pushed=0,
            receipts_pushed=0,
            skipped=0,
            note="Stubbed QuickBooks export (credentials not configured).",
        )

    # Refresh tokens if expired or close to expiring.
    now = datetime.now(UTC)
    expires = status.token_expires_at
    if expires:
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=UTC)
        if expires < now or expires < now + timedelta(minutes=5):
            _refresh_tokens(business_id)

    # Load latest tokens.
    row = _require_connection(business_id)
    access_token = getattr(row, "qbo_access_token", None)
    refresh_token = getattr(row, "qbo_refresh_token", None)
    realm_id = getattr(row, "qbo_realm_id", None)
    if not (access_token and refresh_token and realm_id):
        metrics.qbo_sync_errors += 1
        raise HTTPException(
            status_code=400, detail="QuickBooks tokens are missing; reconnect."
        )

    # Optionally enqueue for background processing.
    if enqueue:
        job_queue.enqueue(
            _background_sync,
            business_id,
            realm_id,
            access_token,
        )
        return QboSyncResponse(
            customers_pushed=0,
            receipts_pushed=0,
            skipped=0,
            note="QuickBooks sync enqueued.",
        )

    attempts = 0
    last_error: str | None = None
    while attempts < 3:
        attempts += 1
        try:
            customers_pushed, receipts_pushed, skipped = _push_customer_and_receipt(
                business_id=business_id,
                realm_id=realm_id,
                access_token=access_token,
            )
            logger.info(
                "qbo_sync_completed",
                extra={
                    "business_id": business_id,
                    "customers_pushed": customers_pushed,
                    "receipts_pushed": receipts_pushed,
                    "skipped": skipped,
                },
            )
            return QboSyncResponse(
                customers_pushed=customers_pushed,
                receipts_pushed=receipts_pushed,
                skipped=skipped,
                note="QuickBooks sync completed.",
            )
        except HTTPException as exc:
            last_error = exc.detail if isinstance(exc.detail, str) else str(exc)
            if attempts >= 2:
                metrics.qbo_sync_errors += 1
                raise
            backoff = attempts * 0.25
            time.sleep(backoff)
        except Exception as exc:
            last_error = str(exc)
            if attempts >= 2:
                metrics.qbo_sync_errors += 1
                metrics.background_job_errors += 1
                logger.exception(
                    "qbo_sync_failed",
                    extra={"business_id": business_id, "error": last_error},
                )
                raise HTTPException(status_code=502, detail="QuickBooks sync failed")
            time.sleep(attempts * 0.25)

    metrics.qbo_sync_errors += 1
    raise HTTPException(status_code=502, detail=last_error or "QuickBooks sync failed")


def _background_sync(business_id: str, realm_id: str, access_token: str) -> None:
    try:
        customers_pushed, receipts_pushed, skipped = _push_customer_and_receipt(
            business_id=business_id,
            realm_id=realm_id,
            access_token=access_token,
        )
        logger.info(
            "qbo_async_sync_completed",
            extra={
                "business_id": business_id,
                "customers_pushed": customers_pushed,
                "receipts_pushed": receipts_pushed,
                "skipped": skipped,
            },
        )
    except HTTPException as exc:
        metrics.qbo_sync_errors += 1
        logger.warning(
            "qbo_async_sync_failed",
            extra={"business_id": business_id, "detail": exc.detail},
        )
    except Exception as exc:
        metrics.qbo_sync_errors += 1
        metrics.background_job_errors += 1
        logger.exception(
            "qbo_async_sync_exception",
            extra={"business_id": business_id, "error": str(exc)},
        )
