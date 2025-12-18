from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
from typing import Optional
from urllib.parse import urlsplit, urlunsplit

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)
from pydantic import BaseModel, Field

from ..db import SQLALCHEMY_AVAILABLE, SessionLocal
from ..db_models import FeedbackDB
from ..deps import ensure_business_active, require_admin_auth
from ..services.feedback_store import FeedbackEntry, feedback_store
from ..services.privacy import redact_text


router = APIRouter()


class FeedbackPayload(BaseModel):
    category: str | None = Field(
        default=None, description="bug, idea, support, other", max_length=32
    )
    summary: str = Field(..., description="Short summary or title", max_length=200)
    steps: str | None = Field(
        default=None,
        description="Steps to reproduce (optional)",
        max_length=3000,
    )
    expected: str | None = Field(
        default=None, description="What you expected to happen", max_length=3000
    )
    actual: str | None = Field(
        default=None, description="What actually happened", max_length=3000
    )
    call_sid: str | None = Field(
        default=None, description="Twilio CallSid if applicable", max_length=64
    )
    contact: str | None = Field(
        default=None, description="Email or phone for follow-up", max_length=200
    )
    conversation_id: str | None = Field(
        default=None,
        description="Conversation ID when reporting from chat/widget",
        max_length=64,
    )
    session_id: str | None = Field(
        default=None,
        description="Internal session ID when available",
        max_length=64,
    )
    url: str | None = Field(
        default=None, description="Page URL where issue occurred", max_length=1024
    )


class FeedbackResponse(BaseModel):
    submitted: bool
    message: str


def _infer_source(request: Request) -> str:
    headers = request.headers
    if headers.get("X-Widget-Token"):
        return "widget"
    if headers.get("X-Owner-Token"):
        return "owner_dashboard"
    if headers.get("X-Admin-API-Key"):
        return "admin"
    if headers.get("X-API-Key"):
        return "tenant_api"
    return "anonymous"


def _clean_text(value: str | None, *, max_len: int) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    cleaned = redact_text(cleaned)
    if len(cleaned) > max_len:
        cleaned = cleaned[: max(0, max_len - 3)] + "..."
    return cleaned


def _clean_url(value: str | None) -> str | None:
    if value is None:
        return None
    raw = value.strip()
    if not raw:
        return None
    try:
        parts = urlsplit(raw)
        # Keep scheme + host + path only (avoid capturing tokens in querystrings).
        raw = urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
    except Exception:
        # If parsing fails, keep best-effort raw string.
        return _clean_text(raw, max_len=1024)
    return _clean_text(raw, max_len=1024)


@router.post("/feedback", response_model=FeedbackResponse)
async def submit_feedback(
    payload: FeedbackPayload,
    request: Request,
    business_id: str = Depends(ensure_business_active),
    user_agent: Optional[str] = Header(default=None, alias="User-Agent"),
) -> FeedbackResponse:
    """Accept structured feedback from dashboard/widget users."""
    now = datetime.now(UTC)
    source = _infer_source(request)
    request_id = getattr(request.state, "request_id", None)
    entry = FeedbackEntry(
        created_at=now,
        business_id=business_id,
        source=source,
        category=payload.category,
        summary=_clean_text(payload.summary, max_len=200) or "",
        steps=_clean_text(payload.steps, max_len=3000),
        expected=_clean_text(payload.expected, max_len=3000),
        actual=_clean_text(payload.actual, max_len=3000),
        call_sid=_clean_text(payload.call_sid, max_len=64),
        conversation_id=_clean_text(payload.conversation_id, max_len=64),
        session_id=_clean_text(payload.session_id, max_len=64),
        request_id=_clean_text(str(request_id) if request_id else None, max_len=64),
        contact=(payload.contact or "").strip() or None,
        url=_clean_url(payload.url),
        user_agent=user_agent,
    )

    stored_in_db = False
    if SQLALCHEMY_AVAILABLE and SessionLocal is not None:
        session = SessionLocal()
        try:
            row = FeedbackDB(  # type: ignore[call-arg]
                created_at=now,
                business_id=business_id,
                source=source,
                category=entry.category,
                summary=entry.summary,
                steps=entry.steps,
                expected=entry.expected,
                actual=entry.actual,
                call_sid=entry.call_sid,
                conversation_id=entry.conversation_id,
                session_id=entry.session_id,
                request_id=entry.request_id,
                url=entry.url,
                contact=entry.contact,
                user_agent=entry.user_agent,
            )
            session.add(row)
            session.commit()
            stored_in_db = True
        except Exception:
            session.rollback()
        finally:
            session.close()

    if not stored_in_db:
        feedback_store.append(entry)
    return FeedbackResponse(submitted=True, message="Thanks for the feedback!")


@router.get("/admin/feedback")
def export_feedback(
    business_id: str | None = Query(
        default=None, description="Filter by tenant business_id"
    ),
    source: str | None = Query(default=None, description="Filter by feedback source"),
    category: str | None = Query(default=None, description="Filter by category"),
    call_sid: str | None = Query(default=None, description="Filter by Twilio CallSid"),
    conversation_id: str | None = Query(
        default=None, description="Filter by conversation id"
    ),
    session_id: str | None = Query(default=None, description="Filter by session id"),
    request_id: str | None = Query(default=None, description="Filter by request id"),
    since: str | None = Query(default=None, description="ISO datetime to filter from"),
    since_minutes: int | None = Query(
        default=None, ge=1, le=60 * 24 * 30, description="Sliding window in minutes"
    ),
    limit: int = Query(default=200, ge=1, le=1000),
    _: str = Depends(require_admin_auth),
):
    """Admin-only export of feedback entries with optional filters."""
    since_dt = None
    if since_minutes:
        since_dt = datetime.now(UTC) - timedelta(minutes=since_minutes)
    elif since:
        try:
            since_dt = datetime.fromisoformat(since)
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid 'since' timestamp",
            )

    if SQLALCHEMY_AVAILABLE and SessionLocal is not None:
        session = SessionLocal()
        try:
            query = session.query(FeedbackDB).order_by(FeedbackDB.id.desc())
            if business_id:
                query = query.filter(FeedbackDB.business_id == business_id)
            if source:
                query = query.filter(FeedbackDB.source == source)
            if category:
                query = query.filter(FeedbackDB.category == category)
            if call_sid:
                query = query.filter(FeedbackDB.call_sid == call_sid)
            if conversation_id:
                query = query.filter(FeedbackDB.conversation_id == conversation_id)
            if session_id:
                query = query.filter(FeedbackDB.session_id == session_id)
            if request_id:
                query = query.filter(FeedbackDB.request_id == request_id)
            if since_dt:
                query = query.filter(FeedbackDB.created_at >= since_dt)
            rows_db = query.limit(limit).all()
            rows = [
                {
                    "id": row.id,
                    "created_at": row.created_at.isoformat(),
                    "business_id": getattr(row, "business_id", None),
                    "source": getattr(row, "source", None),
                    "category": getattr(row, "category", None),
                    "summary": getattr(row, "summary", None),
                    "steps": getattr(row, "steps", None),
                    "expected": getattr(row, "expected", None),
                    "actual": getattr(row, "actual", None),
                    "call_sid": getattr(row, "call_sid", None),
                    "conversation_id": getattr(row, "conversation_id", None),
                    "session_id": getattr(row, "session_id", None),
                    "request_id": getattr(row, "request_id", None),
                    "contact": getattr(row, "contact", None),
                    "url": getattr(row, "url", None),
                    "user_agent": getattr(row, "user_agent", None),
                }
                for row in rows_db
            ]
        finally:
            session.close()
    else:
        rows = feedback_store.list(
            business_id=business_id,
            source=source,
            category=category,
            call_sid=call_sid,
            conversation_id=conversation_id,
            session_id=session_id,
            request_id=request_id,
            since=since_dt,
            limit=limit,
        )

    content = json.dumps({"feedback": rows}, indent=2)
    return Response(content=content, media_type="application/json")
