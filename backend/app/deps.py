from __future__ import annotations

from fastapi import Depends, Header, HTTPException, status

from .config import get_settings
from .db import SQLALCHEMY_AVAILABLE, SessionLocal
from .db_models import Business

DEFAULT_BUSINESS_ID = "default_business"


async def get_business_id(
    x_business_id: str | None = Header(default=None, alias="X-Business-ID"),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    x_widget_token: str | None = Header(default=None, alias="X-Widget-Token"),
) -> str:
    """Resolve the current business/tenant ID from the request.

    Precedence:
    - If X-API-Key is provided and SQLAlchemy is available, look up the Business in the DB.
      - If not found, return 401 Unauthorized.
    - Else, if X-Business-ID is provided, trust it (for legacy/single-tenant scenarios).
    - Else, fall back to the default single-tenant business ID.

    In production, you can set REQUIRE_BUSINESS_API_KEY=true so that requests
    without either an API key or explicit business ID are rejected.
    """
    settings = get_settings()
    require_business_api_key = getattr(settings, "require_business_api_key", False)

    if SQLALCHEMY_AVAILABLE and SessionLocal is not None and (x_api_key or x_widget_token):
        session = SessionLocal()
        try:
            business = None
            if x_api_key:
                business = (
                    session.query(Business)
                    .filter(Business.api_key == x_api_key)
                    .one_or_none()
                )
            elif x_widget_token:
                business = (
                    session.query(Business)
                    .filter(getattr(Business, "widget_token", None) == x_widget_token)
                    .one_or_none()
                )
        finally:
            session.close()

        if not business:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid tenant credentials",
            )
        return business.id

    # If configured, do not allow silent fallback to the default tenant when no
    # tenant-identifying headers are present.
    if require_business_api_key and not x_business_id and not x_api_key and not x_widget_token:
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
            row = session.get(Business, business_id)
        finally:
            session.close()
        if row is not None and getattr(row, "status", "ACTIVE") != "ACTIVE":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Business is suspended",
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


async def require_owner_dashboard_auth(
    x_owner_token: str | None = Header(default=None, alias="X-Owner-Token"),
) -> None:
    """Optional owner/dashboard authentication for CRM & owner routes.

    - If OWNER_DASHBOARD_TOKEN/DASHBOARD_OWNER_TOKEN is not set, these routes
      remain open (development mode).
    - If set, callers (e.g., the dashboard) must send a matching X-Owner-Token
      header or receive 401 Unauthorized.
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
