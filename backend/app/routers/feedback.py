from __future__ import annotations

from datetime import UTC, datetime
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from pydantic import BaseModel, Field

from ..deps import ensure_business_active, require_admin_auth
from ..services.feedback_store import FeedbackEntry, feedback_store
import json


router = APIRouter()


class FeedbackPayload(BaseModel):
    category: str | None = Field(default=None, description="bug, idea, support, other")
    summary: str = Field(..., description="Short summary or title")
    steps: str | None = Field(default=None, description="Steps to reproduce (optional)")
    expected: str | None = Field(default=None, description="What you expected to happen")
    actual: str | None = Field(default=None, description="What actually happened")
    call_sid: str | None = Field(default=None, description="Twilio CallSid if applicable")
    contact: str | None = Field(default=None, description="Email or phone for follow-up")
    url: str | None = Field(default=None, description="Page URL where issue occurred")


class FeedbackResponse(BaseModel):
    submitted: bool
    message: str


@router.post("/feedback", response_model=FeedbackResponse)
async def submit_feedback(
    payload: FeedbackPayload,
    business_id: str = Depends(ensure_business_active),
    user_agent: Optional[str] = Header(default=None, alias="User-Agent"),
) -> FeedbackResponse:
    """Accept structured feedback from dashboard/widget users."""
    entry = FeedbackEntry(
        created_at=datetime.now(UTC),
        business_id=business_id,
        category=payload.category,
        summary=payload.summary.strip(),
        steps=payload.steps,
        expected=payload.expected,
        actual=payload.actual,
        call_sid=payload.call_sid,
        contact=payload.contact,
        url=payload.url,
        user_agent=user_agent,
    )
    feedback_store.append(entry)
    return FeedbackResponse(submitted=True, message="Thanks for the feedback!")


@router.get("/admin/feedback")
def export_feedback(
    business_id: str | None = Query(default=None, description="Filter by tenant business_id"),
    since: str | None = Query(default=None, description="ISO datetime to filter from"),
    limit: int = Query(default=200, ge=1, le=1000),
    _: str = Depends(require_admin_auth),
):
    """Admin-only export of feedback entries with optional filters."""
    since_dt = None
    if since:
        try:
            since_dt = datetime.fromisoformat(since)
        except Exception:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid 'since' timestamp")
    rows = feedback_store.list(business_id=business_id, since=since_dt, limit=limit)
    content = json.dumps({"feedback": rows}, indent=2)
    return Response(content=content, media_type="application/json")
