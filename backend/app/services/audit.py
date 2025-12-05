from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import Request

from ..db import SQLALCHEMY_AVAILABLE, SessionLocal
from ..db_models import AuditEventDB, Business

logger = logging.getLogger(__name__)


@dataclass
class RequestActor:
    """Lightweight description of who is calling the API."""

    role: str
    business_id: str | None = None


def _resolve_business_id_from_headers(request: Request) -> str | None:
    """Best-effort tenant resolution for audit logging.

    For performance and simplicity we only consult the database when an
    API key or widget token is present. When unavailable we fall back to
    any explicit X-Business-ID header if provided.
    """
    if not SQLALCHEMY_AVAILABLE or SessionLocal is None:
        return request.headers.get("X-Business-ID") or None

    x_api_key = request.headers.get("X-API-Key")
    x_widget_token = request.headers.get("X-Widget-Token")
    explicit_business_id = request.headers.get("X-Business-ID")

    if not x_api_key and not x_widget_token:
        return explicit_business_id or None

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
                .filter(Business.widget_token == x_widget_token)
                .one_or_none()
            )
        if business:
            return business.id
        return explicit_business_id or None
    except Exception:  # pragma: no cover - defensive
        logger.exception("audit_business_resolution_failed")
        return explicit_business_id or None
    finally:
        session.close()


def _derive_actor(request: Request) -> RequestActor:
    headers = request.headers
    if headers.get("X-Admin-API-Key"):
        role = "admin"
    elif headers.get("X-Owner-Token"):
        role = "owner_dashboard"
    elif headers.get("X-API-Key"):
        role = "tenant_api"
    elif headers.get("X-Widget-Token"):
        role = "widget"
    else:
        role = "anonymous"

    business_id = _resolve_business_id_from_headers(request)
    return RequestActor(role=role, business_id=business_id)


async def record_audit_event(request: Request, status_code: int) -> None:
    """Persist a minimal audit event or fall back to structured logging.

    This is designed to be called from middleware; failures are logged but
    never allowed to break the main request/response flow.
    """
    actor = _derive_actor(request)

    # When the database is unavailable we still emit a structured log record.
    if not SQLALCHEMY_AVAILABLE or SessionLocal is None:
        logger.info(
            "audit_event",
            extra={
                "actor_type": actor.role,
                "business_id": actor.business_id,
                "path": request.url.path,
                "method": request.method,
                "status_code": status_code,
            },
        )
        return

    session = SessionLocal()
    try:
        now = datetime.now(UTC)
        path = request.url.path
        # Keep path reasonably bounded for storage.
        if len(path) > 255:
            path = path[:252] + "..."
        event = AuditEventDB(  # type: ignore[call-arg]
            created_at=now,
            actor_type=actor.role,
            business_id=actor.business_id,
            path=path,
            method=request.method,
            status_code=status_code,
        )
        session.add(event)
        session.commit()
    except Exception:  # pragma: no cover - defensive
        logger.exception("audit_event_persist_failed")
    finally:
        session.close()

