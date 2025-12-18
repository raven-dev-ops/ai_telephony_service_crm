from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..config import get_settings
from ..deps import ensure_business_active
from ..repositories import conversations_repo, customers_repo
from ..services import conversation, sessions
from ..db import SQLALCHEMY_AVAILABLE, SessionLocal
from ..db_models import BusinessDB


router = APIRouter()


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
            name = str(getattr(row, "name", name) or name)
        if row is not None and getattr(row, "language_code", None):
            language_code = str(
                getattr(row, "language_code", language_code) or language_code
            )
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
    session = sessions.session_store.create(
        caller_phone=payload.customer_phone,
        business_id=business_id,
        lead_source=payload.lead_source,
        channel="web",
    )
    conv = conversations_repo.create(
        channel="web",
        customer_id=customer.id if customer else None,
        session_id=session.id,
        business_id=business_id,
    )

    result = await conversation.conversation_manager.handle_input(session, None)
    conversations_repo.append_message(conv.id, role="assistant", text=result.reply_text)
    return ChatStartResponse(conversation_id=conv.id, reply_text=result.reply_text)


@router.post("/{conversation_id}/message", response_model=ChatMessageResponse)
async def chat_message(
    conversation_id: str, payload: ChatMessageRequest
) -> ChatMessageResponse:
    conv = conversations_repo.get(conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    session_id = getattr(conv, "session_id", None)
    if not session_id:
        raise HTTPException(
            status_code=409, detail="Chat session expired. Please start a new chat."
        )
    session = sessions.session_store.get(session_id)
    if not session:
        raise HTTPException(
            status_code=409, detail="Chat session expired. Please start a new chat."
        )
    session.channel = conv.channel or session.channel
    session.business_id = conv.business_id or session.business_id
    conversations_repo.append_message(conversation_id, role="user", text=payload.text)
    result = await conversation.conversation_manager.handle_input(session, payload.text)
    conversations_repo.append_message(
        conversation_id, role="assistant", text=result.reply_text
    )
    return ChatMessageResponse(
        conversation_id=conversation_id, reply_text=result.reply_text
    )
