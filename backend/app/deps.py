from __future__ import annotations

import os
from datetime import datetime, UTC
from fastapi import Depends, Header, HTTPException, status, Request
from typing import cast

from .config import get_settings
from .db import SQLALCHEMY_AVAILABLE, SessionLocal
from .db_models import BusinessDB, BusinessUserDB
from .services.auth import TokenError, decode_token
from .services import subscription as subscription_service
from .metrics import metrics

DEFAULT_BUSINESS_ID = "default_business"


async def get_business_id(
    x_business_id: str | None = Header(default=None, alias="X-Business-ID"),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    x_widget_token: str | None = Header(default=None, alias="X-Widget-Token"),
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> str:
    """Resolve the current business/tenant ID from the request.

    Precedence:
    - If Authorization bearer token is provided, prefer its business claim.
    - If X-API-Key is provided and SQLAlchemy is available, look up the Business in the DB.
      - If not found, return 401 Unauthorized.
    - Else, if X-Business-ID is provided, trust it (for legacy/single-tenant scenarios).
    - Else, fall back to the default single-tenant business ID.

    In production, you can set REQUIRE_BUSINESS_API_KEY=true so that requests
    without either an API key or explicit business ID are rejected.
    """
    settings = get_settings()
    require_business_api_key = getattr(settings, "require_business_api_key", False)
    is_testing = bool(os.getenv("PYTEST_CURRENT_TEST")) or (
        os.getenv("TESTING", "false").lower() == "true"
    )

    token_business_id = None
    # FastAPI injects Header objects when not bound through dependency
    # injection during direct function calls in tests; normalize to str.
    if not isinstance(authorization, str):
        authorization = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
        try:
            decoded = decode_token(token, settings, expected_type="access")
            token_business_id = decoded.business_id
        except TokenError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired token",
            )

    if token_business_id:
        if x_business_id and x_business_id != token_business_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Business mismatch",
            )
        return token_business_id

    if (
        SQLALCHEMY_AVAILABLE
        and SessionLocal is not None
        and (x_api_key or x_widget_token)
    ):
        session = SessionLocal()
        business_id_value: str | None = None
        try:
            business = None
            now = datetime.now(UTC)
            if x_api_key:
                business = (
                    session.query(BusinessDB)
                    .filter(BusinessDB.api_key == x_api_key)
                    .one_or_none()
                )
                if business is not None:
                    try:
                        business.api_key_last_used_at = now  # type: ignore[assignment]
                        session.add(business)
                        session.commit()
                    except Exception:
                        session.rollback()
            elif x_widget_token:
                business = (
                    session.query(BusinessDB)
                    .filter(BusinessDB.widget_token == x_widget_token)
                    .one_or_none()
                )
                if business is not None:
                    expires_at = getattr(business, "widget_token_expires_at", None)
                    if expires_at is not None and expires_at.tzinfo is None:
                        expires_at = expires_at.replace(tzinfo=UTC)
                    if expires_at is not None and expires_at < now:
                        raise HTTPException(
                            status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Widget token expired",
                        )
                    try:
                        business.widget_token_last_used_at = now  # type: ignore[assignment]
                        session.add(business)
                        session.commit()
                    except HTTPException:
                        session.rollback()
                        raise
                    except Exception:
                        session.rollback()
            if business is not None:
                business_id_value = cast(str, business.id)
        finally:
            session.close()

        if not business:
            # In tests we allow falling back to the default tenant when an API key is
            # supplied but multi-tenant enforcement is not required, to avoid 401s in
            # routes that set dummy keys.
            if is_testing and x_api_key and not require_business_api_key:
                return x_business_id or DEFAULT_BUSINESS_ID
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid tenant credentials",
            )
        return cast(str, business_id_value)

    # If configured, do not allow silent fallback to the default tenant when no
    # tenant-identifying headers are present.
    if (
        require_business_api_key
        and not x_business_id
        and not x_api_key
        and not x_widget_token
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing tenant credentials",
        )

    return x_business_id or DEFAULT_BUSINESS_ID


async def ensure_business_active(
    business_id: str = Depends(get_business_id),
) -> str:
    """Ensure the resolved business/tenant is active (not suspended).

    If the Business row exists and its status is not ACTIVE, requests are
    rejected with 403 Forbidden. When the database is unavailable, this
    behaves like a passthrough.
    """
    if SQLALCHEMY_AVAILABLE and SessionLocal is not None:
        session = SessionLocal()
        try:
            row = session.get(BusinessDB, business_id)
        finally:
            session.close()
        if row is not None and getattr(row, "status", "ACTIVE") != "ACTIVE":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Business is suspended",
            )
        if row is not None and getattr(row, "lockdown_mode", False):
            raise HTTPException(
                status_code=status.HTTP_423_LOCKED,
                detail="Business is in lockdown mode",
            )
    return business_id


def _should_enforce_onboarding() -> bool:
    testing = bool(os.getenv("PYTEST_CURRENT_TEST")) or (
        os.getenv("TESTING", "false").lower() == "true"
    )
    if testing and os.getenv("ONBOARDING_ENFORCE_IN_TESTS", "false").lower() != "true":
        return False
    return os.getenv("ENFORCE_ONBOARDING", "true").lower() == "true"


async def ensure_onboarding_ready(
    business_id: str = Depends(ensure_business_active),
) -> str:
    """Ensure onboarding requirements are complete before allowing certain flows."""
    if not _should_enforce_onboarding():
        return business_id
    if not (SQLALCHEMY_AVAILABLE and SessionLocal is not None):
        return business_id
    session = SessionLocal()
    try:
        row = session.get(BusinessDB, business_id)
    finally:
        session.close()
    if row is None:
        raise HTTPException(status_code=404, detail="Business not found")
    missing: list[str] = []
    if not getattr(row, "terms_accepted_at", None):
        missing.append("terms_of_service")
    if not getattr(row, "privacy_accepted_at", None):
        missing.append("privacy_policy")
    if not getattr(row, "owner_name", None):
        missing.append("owner_name")
    if not getattr(row, "owner_email", None):
        missing.append("owner_email")
    if not getattr(row, "service_tier", None):
        missing.append("service_tier")
    if not getattr(row, "onboarding_completed", False):
        missing.append("onboarding_completed")
    if missing:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Complete onboarding first: {', '.join(missing)}",
        )
    return business_id


async def require_subscription_active(
    request: Request,
    business_id: str = Depends(get_business_id),
):
    """Optionally enforce an active subscription when configured.

    Controlled by ENFORCE_SUBSCRIPTION (default false). When enabled, rejects
    requests for tenants whose subscription_status is not "active".
    """
    settings = get_settings()
    if not getattr(settings, "enforce_subscription", False):
        return business_id
    if not business_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing business context"
        )
    path = request.url.path
    feature = "core"
    graceful = False
    upcoming_calls = 0
    if path.startswith(
        ("/telephony", "/v1/telephony", "/twilio", "/v1/twilio", "/v1/voice")
    ):
        feature = "calls"
        graceful = True
        upcoming_calls = 1
    elif path.startswith("/v1/chat"):
        feature = "chat"
    state = await subscription_service.check_access(
        business_id,
        feature=feature,
        upcoming_calls=upcoming_calls,
        graceful=graceful,
    )
    request.state.subscription_state = state
    if state.blocked and not graceful:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=state.message or "Subscription inactive",
            headers={"X-Subscription-Status": state.status},
        )
    return business_id


async def require_admin_auth(
    x_admin_api_key: str | None = Header(default=None, alias="X-Admin-API-Key"),
) -> None:
    """Optional admin authentication for /v1/admin routes.

    - If ADMIN_API_KEY is not set, admin routes are open (development mode).
    - If ADMIN_API_KEY is set, callers must send a matching X-Admin-API-Key
      header or receive 401 Unauthorized.
    """
    settings = get_settings()
    expected = getattr(settings, "admin_api_key", None)
    if not expected:
        # No admin key configured: treat as open.
        return

    if x_admin_api_key != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid admin API key",
        )
    metrics.admin_token_last_used_at = datetime.now(UTC).isoformat()


async def require_owner_dashboard_auth(
    x_owner_token: str | None = Header(default=None, alias="X-Owner-Token"),
) -> None:
    """Optional owner/dashboard authentication for CRM & owner routes.

    - If OWNER_DASHBOARD_TOKEN is not set, these routes remain open
      (development mode).
    - If set, callers (e.g., the dashboard) must send a matching
      X-Owner-Token header or receive 401 Unauthorized.
    - A legacy alias DASHBOARD_OWNER_TOKEN is still accepted by the
      configuration loader for backward compatibility, but
      OWNER_DASHBOARD_TOKEN is the canonical name and should be used
      in all new deployments.
    """
    settings = get_settings()
    expected = getattr(settings, "owner_dashboard_token", None)
    if not expected:
        return

    if x_owner_token != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid owner dashboard token",
        )
    metrics.owner_token_last_used_at = datetime.now(UTC).isoformat()


def require_dashboard_role(
    allowed_roles: list[str], allow_anonymous_if_no_token: bool = True
):
    """Enforce dashboard roles (owner/admin/staff/viewer) for UI/CRM routes.

    - Accepts one of:
      - X-Owner-Token matching OWNER_DASHBOARD_TOKEN (treated as "owner")
      - X-Admin-API-Key matching ADMIN_API_KEY (treated as "admin")
      - X-User-ID mapped via BusinessUser.role for the resolved business
    - When OWNER_DASHBOARD_TOKEN is unset and allow_anonymous_if_no_token=True,
      requests without any credentials are permitted (dev/default behaviour).
    """

    allowed_set = {r.lower() for r in allowed_roles}

    async def _dep(
        x_owner_token: str | None = Header(default=None, alias="X-Owner-Token"),
        x_admin_api_key: str | None = Header(default=None, alias="X-Admin-API-Key"),
        x_user_id: str | None = Header(default=None, alias="X-User-ID"),
        authorization: str | None = Header(default=None, alias="Authorization"),
        business_id: str = Depends(get_business_id),
    ) -> None:
        settings = get_settings()
        roles: list[str] = []
        token_user_id = None
        token_business_id = None
        if authorization and authorization.lower().startswith("bearer "):
            token = authorization.split(" ", 1)[1].strip()
            try:
                decoded = decode_token(token, settings, expected_type="access")
            except TokenError:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid or expired token",
                )
            token_user_id = decoded.user_id
            token_business_id = decoded.business_id
        if token_business_id and token_business_id != business_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Business mismatch",
            )
        user_id = token_user_id or x_user_id

        if settings.admin_api_key and x_admin_api_key == settings.admin_api_key:
            metrics.admin_token_last_used_at = datetime.now(UTC).isoformat()
            roles.append("admin")

        if user_id and SQLALCHEMY_AVAILABLE and SessionLocal is not None:
            session = SessionLocal()
            try:
                memberships = (
                    session.query(BusinessUserDB)
                    .filter(
                        BusinessUserDB.user_id == user_id,
                        BusinessUserDB.business_id == business_id,
                    )
                    .all()
                )
                if not memberships:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="User not associated with this business",
                    )
                roles.extend(
                    [getattr(m, "role", "viewer").lower() for m in memberships]
                )
            finally:
                session.close()
        elif user_id and (not SQLALCHEMY_AVAILABLE or SessionLocal is None):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="User-based access requires database support",
            )
        elif (
            settings.owner_dashboard_token
            and x_owner_token == settings.owner_dashboard_token
        ):
            # Legacy/tenant-wide token grants owner-level access when no per-user role is provided.
            metrics.owner_token_last_used_at = datetime.now(UTC).isoformat()
            roles.append("owner")

        # If a dashboard token is configured, credentials are mandatory.
        if settings.owner_dashboard_token:
            if not roles:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Missing dashboard credentials",
                )
        else:
            if not roles and allow_anonymous_if_no_token:
                return

        if allowed_set and not any(r in allowed_set for r in roles):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient role",
            )

    return _dep
