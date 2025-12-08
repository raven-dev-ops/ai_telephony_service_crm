from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
import time
from typing import Optional, Iterable

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..deps import ensure_business_active, require_owner_dashboard_auth
from ..repositories import appointments_repo, conversations_repo, customers_repo
from ..db import SQLALCHEMY_AVAILABLE, SessionLocal
from ..db_models import BusinessDB
from ..services.owner_assistant import owner_assistant_service
from ..metrics import metrics


router = APIRouter(dependencies=[Depends(require_owner_dashboard_auth)])
logger = logging.getLogger(__name__)


class ChatRequest(BaseModel):
    text: str
    conversation_id: Optional[str] = None


class ChatResponse(BaseModel):
    reply_text: str
    conversation_id: str
    used_model: Optional[str] = None


def _business_label(business_id: str) -> tuple[str, str | None]:
    """Return the business name and service tier when available."""
    name = "Default Business"
    tier = None
    if SQLALCHEMY_AVAILABLE and SessionLocal is not None:
        session = SessionLocal()
        try:
            row = session.get(BusinessDB, business_id)
            if row is not None:
                name = getattr(row, "name", name) or name
                tier = getattr(row, "service_tier", None)
        finally:
            session.close()
    return name, tier


def _format_dt(dt: datetime) -> str:
    try:
        return dt.astimezone(UTC).strftime("%Y-%m-%d %I:%M %p").lstrip("0")
    except Exception:
        try:
            return dt.strftime("%Y-%m-%d %I:%M %p").lstrip("0")
        except Exception:
            return ""


def _build_business_context(business_id: str) -> str:
    """Assemble lightweight business context for the assistant prompt."""
    name, tier = _business_label(business_id)
    today = datetime.now(UTC).date()
    in_7_days = today + timedelta(days=7)

    appointments = appointments_repo.list_for_business(business_id)
    customers = customers_repo.list_for_business(business_id)

    upcoming = [
        a
        for a in appointments
        if getattr(a, "start_time", None) and a.start_time.date() >= today
    ]
    next_week = [
        a
        for a in appointments
        if getattr(a, "start_time", None) and today <= a.start_time.date() <= in_7_days
    ]
    emergencies_week = sum(1 for a in next_week if getattr(a, "is_emergency", False))
    upcoming_sorted = sorted(
        upcoming, key=lambda a: getattr(a, "start_time", datetime.max)
    )[:3]

    context_lines = [
        f"Business: {name} (id={business_id})",
        f"Service tier: {tier or 'Unselected'}",
        f"Customers on file: {len(customers)}",
        f"Upcoming appointments (next 7 days): {len(next_week)}, emergencies: {emergencies_week}",
    ]

    if upcoming_sorted:
        human_list = []
        for appt in upcoming_sorted:
            ts = _format_dt(appt.start_time)
            label = appt.service_type or "appointment"
            status = "emergency" if getattr(appt, "is_emergency", False) else "standard"
            human_list.append(f"{ts} - {label} ({status})")
        context_lines.append("Next appointments: " + "; ".join(human_list))

    return "\n".join(context_lines)


def _get_or_create_conversation(conversation_id: Optional[str], business_id: str):
    if conversation_id:
        conv = conversations_repo.get(conversation_id)
        if conv and getattr(conv, "business_id", None) == business_id:
            return conv
    return conversations_repo.create(channel="owner_chat", business_id=business_id)


@router.post("", response_model=ChatResponse)
async def chat(
    payload: ChatRequest,
    response: Response,
    business_id: str = Depends(ensure_business_active),
) -> ChatResponse:
    question = (payload.text or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="Message text is required.")

    context = _build_business_context(business_id)
    start = time.perf_counter()
    try:
        answer = await owner_assistant_service.answer(
            question, business_context=context
        )
    except Exception as exc:
        metrics.chat_failures += 1
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        metrics.chat_latency_ms_total += elapsed_ms
        metrics.chat_latency_ms_max = max(metrics.chat_latency_ms_max, elapsed_ms)
        metrics.chat_latency_samples += 1
        logger.exception(
            "chat_message_failed",
            extra={
                "business_id": business_id,
                "conversation_id": payload.conversation_id,
                "latency_ms": round(elapsed_ms, 2),
                "error": str(exc),
            },
        )
        raise
    latency_ms = (time.perf_counter() - start) * 1000.0
    metrics.record_chat_latency(latency_ms)

    conv = _get_or_create_conversation(payload.conversation_id, business_id)
    conversations_repo.append_message(conv.id, role="user", text=question)
    conversations_repo.append_message(conv.id, role="assistant", text=answer.answer)
    metrics.chat_messages += 1
    logger.info(
        "chat_message_sent",
        extra={
            "conversation_id": conv.id,
            "business_id": business_id,
            "used_model": answer.used_model,
            "latency_ms": round(latency_ms, 2),
        },
    )
    if latency_ms > 1200:
        logger.warning(
            "chat_latency_slow",
            extra={
                "business_id": business_id,
                "latency_ms": round(latency_ms, 2),
                "conversation_id": conv.id,
            },
        )

    response.headers["X-Conversation-ID"] = conv.id
    return ChatResponse(
        reply_text=answer.answer,
        conversation_id=conv.id,
        used_model=answer.used_model,
    )


def _chunk_text(text: str, chunk_size: int = 120) -> Iterable[str]:
    """Yield small chunks for streaming responses."""
    if not text:
        return []
    words = text.split()
    current: list[str] = []
    for word in words:
        current.append(word)
        if sum(len(w) for w in current) + len(current) - 1 >= chunk_size:
            yield " ".join(current)
            current = []
    if current:
        yield " ".join(current)


@router.post("/stream")
async def chat_stream(
    payload: ChatRequest,
    response: Response,
    business_id: str = Depends(ensure_business_active),
    stream: bool = Query(default=True),
):
    """Stream chat replies using text/event-stream (SSE)."""
    question = (payload.text or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="Message text is required.")

    context = _build_business_context(business_id)
    conv = _get_or_create_conversation(payload.conversation_id, business_id)
    conversations_repo.append_message(conv.id, role="user", text=question)

    start = time.perf_counter()
    try:
        answer = await owner_assistant_service.answer(
            question, business_context=context
        )
    except Exception as exc:
        metrics.chat_failures += 1
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        metrics.chat_latency_ms_total += elapsed_ms
        metrics.chat_latency_ms_max = max(metrics.chat_latency_ms_max, elapsed_ms)
        metrics.chat_latency_samples += 1
        logger.exception(
            "chat_message_failed",
            extra={
                "business_id": business_id,
                "conversation_id": payload.conversation_id,
                "latency_ms": round(elapsed_ms, 2),
                "error": str(exc),
            },
        )
        raise

    latency_ms = (time.perf_counter() - start) * 1000.0
    metrics.record_chat_latency(latency_ms)
    metrics.chat_messages += 1

    conversations_repo.append_message(conv.id, role="assistant", text=answer.answer)

    def event_stream() -> Iterable[str]:
        meta = {"conversation_id": conv.id, "used_model": answer.used_model}
        yield f"event: meta\ndata: {json.dumps(meta)}\n\n"
        for chunk in _chunk_text(answer.answer):
            yield f"data: {chunk}\n\n"
        yield "event: done\ndata: end\n\n"

    logger.info(
        "chat_message_sent",
        extra={
            "conversation_id": conv.id,
            "business_id": business_id,
            "used_model": answer.used_model,
            "latency_ms": round(latency_ms, 2),
            "stream": True,
        },
    )
    if latency_ms > 1200:
        logger.warning(
            "chat_latency_slow",
            extra={
                "business_id": business_id,
                "latency_ms": round(latency_ms, 2),
                "conversation_id": conv.id,
                "stream": True,
            },
        )

    headers = {
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    }
    response.headers["X-Conversation-ID"] = conv.id
    return StreamingResponse(
        event_stream(), media_type="text/event-stream", headers=headers
    )
