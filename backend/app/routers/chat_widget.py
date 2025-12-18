from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..config import get_settings
from ..deps import ensure_business_active
from ..repositories import conversations_repo, customers_repo
from ..services.conversation import ConversationManager
from ..db import SQLALCHEMY_AVAILABLE, SessionLocal
from ..db_models import BusinessDB


router = APIRouter()
manager = ConversationManager()


class ChatStartRequest(BaseModel):
    customer_phone: str | None = None
    customer_name: str | None = None
    customer_email: str | None = None
    lead_source: str | None = None


class ChatStartResponse(BaseModel):
    conversation_id: str
    reply_text: str


class ChatMessageRequest(BaseModel):
    text: str


class ChatMessageResponse(BaseModel):
    reply_text: str
    conversation_id: str


class WidgetBusinessResponse(BaseModel):
    id: str
    name: str
    language_code: str


@router.get("/business", response_model=WidgetBusinessResponse)
async def widget_business(
    business_id: str = Depends(ensure_business_active),
) -> WidgetBusinessResponse:
    """Return basic business info for the widget / web chat context."""
    settings = get_settings()
    default_language = getattr(settings, "default_language_code", "en")
    name = "Default Business"
    language_code = default_language
    if SQLALCHEMY_AVAILABLE and SessionLocal is not None:
        session_db = SessionLocal()
        try:
            row = session_db.get(BusinessDB, business_id)
        finally:
            session_db.close()
        if row is not None and getattr(row, "name", None):
            name = row.name
        if row is not None and getattr(row, "language_code", None):
            language_code = row.language_code
    return WidgetBusinessResponse(
        id=business_id, name=name, language_code=language_code
    )


@router.post("/start", response_model=ChatStartResponse)
async def start_chat(
    payload: ChatStartRequest,
    business_id: str = Depends(ensure_business_active),
) -> ChatStartResponse:
    customer = None
    if payload.customer_phone:
        customer = customers_repo.get_by_phone(
            payload.customer_phone, business_id=business_id
        )
        if customer is None:
            customer = customers_repo.upsert(
                name=payload.customer_name or "",
                phone=payload.customer_phone,
                email=payload.customer_email,
                address=None,
                business_id=business_id,
            )
    conv = conversations_repo.create(
        channel="web",
        customer_id=customer.id if customer else None,
        business_id=business_id,
    )

    # For now, treat the first turn as if we are at GREETING with no prior input.
    from ..services.sessions import CallSession  # local import to avoid cycles

    session = CallSession(
        id=conv.id,
        caller_phone=payload.customer_phone,
        business_id=business_id,
        channel="web",
        lead_source=payload.lead_source,
    )
    result = await manager.handle_input(session, None)
    conversations_repo.append_message(conv.id, role="assistant", text=result.reply_text)
    return ChatStartResponse(conversation_id=conv.id, reply_text=result.reply_text)


@router.post("/{conversation_id}/message", response_model=ChatMessageResponse)
async def chat_message(
    conversation_id: str, payload: ChatMessageRequest
) -> ChatMessageResponse:
    conv = conversations_repo.get(conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    from ..services.sessions import CallSession  # local import to avoid cycles

    session = CallSession(
        id=conversation_id,
        business_id=conv.business_id,
        channel=conv.channel or "web",
    )
    conversations_repo.append_message(conversation_id, role="user", text=payload.text)
    result = await manager.handle_input(session, payload.text)
    conversations_repo.append_message(
        conversation_id, role="assistant", text=result.reply_text
    )
    return ChatMessageResponse(
        conversation_id=conversation_id, reply_text=result.reply_text
    )
