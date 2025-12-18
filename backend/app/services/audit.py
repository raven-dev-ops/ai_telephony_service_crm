from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from fastapi import Request

from ..db import SQLALCHEMY_AVAILABLE, SessionLocal
from ..db_models import AuditEventDB, BusinessDB, SecurityEventDB
from ..metrics import metrics
from .privacy import redact_text

logger = logging.getLogger(__name__)


SECURITY_EVENT_WEBHOOK_SIGNATURE_MISSING = "webhook_signature_missing"
SECURITY_EVENT_WEBHOOK_SIGNATURE_INVALID = "webhook_signature_invalid"
SECURITY_EVENT_WEBHOOK_REPLAY_BLOCKED = "webhook_replay_blocked"
SECURITY_EVENT_RATE_LIMIT_BLOCKED = "rate_limit_blocked"
SECURITY_EVENT_AUTH_FAILURE = "auth_failure"


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
            try:
                filter_expr = BusinessDB.api_key == x_api_key  # type: ignore[attr-defined]
            except AttributeError:
                # Placeholder models without SQLAlchemy columns may not expose attributes.
                filter_expr = True
            business = session.query(BusinessDB).filter(filter_expr).one_or_none()
        elif x_widget_token:
            try:
                filter_expr = BusinessDB.widget_token == x_widget_token  # type: ignore[attr-defined]
            except AttributeError:
                filter_expr = True
            business = session.query(BusinessDB).filter(filter_expr).one_or_none()
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


def hash_value(value: str | None) -> str | None:
    """Return a stable, salted hash suitable for logs/storage (no raw PII)."""
    if not value:
        return None
    salt = os.getenv("AUDIT_HASH_SALT", "")
    try:
        digest = hashlib.blake2b(
            value.encode("utf-8"),
            key=salt.encode("utf-8"),
            digest_size=16,
        ).hexdigest()
    except Exception:
        return None
    return digest[:24]


def _best_effort_client_ip(request: Request) -> str | None:
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        first = forwarded_for.split(",", 1)[0].strip()
        return first or None
    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip.strip() or None
    if request.client:
        return request.client.host or None
    return None


def _safe_meta_json(meta: dict[str, Any] | None) -> str | None:
    if not meta:
        return None
    safe: dict[str, Any] = {}
    for key, value in meta.items():
        if value is None:
            continue
        if isinstance(value, (bool, int, float)):
            safe[str(key)] = value
            continue
        if isinstance(value, str):
            safe[str(key)] = redact_text(value)[:200]
            continue
        # Drop complex types to avoid accidentally storing payloads.
    if not safe:
        return None
    try:
        raw = json.dumps(safe, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        return None
    # Keep storage/log size bounded.
    return raw[:1000]


async def record_security_event(
    *,
    request: Request | None,
    event_type: str,
    status_code: int,
    business_id: str | None = None,
    severity: str = "warning",
    meta: dict[str, Any] | None = None,
) -> None:
    """Record a security-relevant event to logs + (when available) the database.

    This is designed to be safe-by-default:
    - Do not store raw tokens, signatures, request bodies, IPs, or phone numbers.
    - Hash potentially sensitive values (e.g., IP, user agent) for correlation.
    """
    try:
        request_id = None
        path = "-"
        method = "-"
        actor_type = "unknown"
        ip_hash = None
        user_agent_hash = None
        actor_business_id = None

        if request is not None:
            try:
                request_id = getattr(request.state, "request_id", None)
            except Exception:
                request_id = None
            try:
                path = redact_text(request.url.path)
            except Exception:
                path = "-"
            method = getattr(request, "method", "-") or "-"
            try:
                actor = _derive_actor(request)
                actor_type = actor.role
                actor_business_id = actor.business_id
            except Exception:
                actor_type = "unknown"
                actor_business_id = None
            try:
                ip_hash = hash_value(_best_effort_client_ip(request))
            except Exception:
                ip_hash = None
            try:
                user_agent_hash = hash_value(request.headers.get("User-Agent"))
            except Exception:
                user_agent_hash = None

        effective_business_id = business_id or actor_business_id
        meta_json = _safe_meta_json(meta)

        # Metrics counters (best-effort).
        try:
            metrics.security_events_total += 1
            metrics.security_events_by_type[event_type] = (
                metrics.security_events_by_type.get(event_type, 0) + 1
            )
            biz_key = effective_business_id or "unknown"
            per_biz = metrics.security_events_by_business.setdefault(biz_key, {})
            per_biz[event_type] = per_biz.get(event_type, 0) + 1
        except Exception:
            logger.debug("security_event_metrics_failed", exc_info=True)

        logger.warning(
            "security_event",
            extra={
                "event_type": event_type,
                "severity": severity,
                "actor_type": actor_type,
                "business_id": effective_business_id,
                "path": path,
                "method": method,
                "status_code": status_code,
                "ip_hash": ip_hash,
                "user_agent_hash": user_agent_hash,
                "request_id": request_id,
                "meta": meta_json,
            },
        )

        if not SQLALCHEMY_AVAILABLE or SessionLocal is None:
            return

        session = SessionLocal()
        try:
            now = datetime.now(UTC)
            # Keep path reasonably bounded for storage.
            stored_path = path
            if len(stored_path) > 255:
                stored_path = stored_path[:252] + "..."
            event = SecurityEventDB(  # type: ignore[call-arg]
                created_at=now,
                event_type=event_type,
                severity=severity,
                actor_type=actor_type,
                business_id=effective_business_id,
                path=stored_path,
                method=method,
                status_code=status_code,
                ip_hash=ip_hash,
                user_agent_hash=user_agent_hash,
                request_id=request_id,
                meta=meta_json,
            )
            session.add(event)
            session.commit()
        except Exception:  # pragma: no cover - defensive
            logger.exception("security_event_persist_failed")
        finally:
            session.close()
    except Exception:  # pragma: no cover - never break request flow
        logger.exception("security_event_record_failed")


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
                "path": redact_text(request.url.path),
                "method": request.method,
                "status_code": status_code,
            },
        )
        return

    session = SessionLocal()
    try:
        now = datetime.now(UTC)
        path = redact_text(request.url.path)
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
