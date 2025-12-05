import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..deps import ensure_business_active
from ..metrics import BusinessVoiceSessionMetrics, metrics
from ..repositories import conversations_repo, customers_repo
from ..services import conversation, sessions


router = APIRouter()
logger = logging.getLogger(__name__)


class InboundCallRequest(BaseModel):
    caller_phone: str | None = None
    provider_call_id: str | None = None
    lead_source: str | None = None


class InboundCallResponse(BaseModel):
    session_id: str
    reply_text: str
    session_state: dict
    audio: str | None = None


class CallAudioRequest(BaseModel):
    session_id: str
    audio: str | None = None
    text: str | None = None


class CallAudioResponse(BaseModel):
    reply_text: str
    session_state: dict
    audio: str | None = None


class CallEndRequest(BaseModel):
    session_id: str


class CallEndResponse(BaseModel):
    status: str


@router.post("/inbound", response_model=InboundCallResponse)
async def inbound_call(
    payload: InboundCallRequest,
    business_id: str = Depends(ensure_business_active),
) -> InboundCallResponse:
    """Entry point for a new telephony call.

    A real telephony provider (e.g., Twilio) would POST here when a call starts.
    """
    # Track voice/telephony session usage.
    metrics.voice_session_requests += 1
    per_tenant = metrics.voice_sessions_by_business.setdefault(
        business_id, BusinessVoiceSessionMetrics()
    )
    per_tenant.requests += 1

    session = sessions.session_store.create(
        caller_phone=payload.caller_phone,
        business_id=business_id,
        lead_source=payload.lead_source,
    )
    customer = (
        customers_repo.get_by_phone(payload.caller_phone, business_id=business_id)
        if payload.caller_phone
        else None
    )
    conv = conversations_repo.create(
        channel="phone",
        customer_id=customer.id if customer else None,
        session_id=session.id,
        business_id=business_id,
    )

    # Trigger initial greeting.
    logger.info(
        "telephony_inbound_call",
        extra={
            "session_id": session.id,
            "business_id": business_id,
            "caller_phone": payload.caller_phone,
            "provider_call_id": payload.provider_call_id,
        },
    )

    try:
        result = await conversation.conversation_manager.handle_input(session, None)
        audio = await conversation.speech_service.synthesize(result.reply_text)
    except Exception:
        metrics.voice_session_errors += 1
        per_err = metrics.voice_sessions_by_business.setdefault(
            business_id, BusinessVoiceSessionMetrics()
        )
        per_err.errors += 1
        logger.exception(
            "telephony_inbound_unhandled_error",
            extra={
                "session_id": session.id,
                "business_id": business_id,
            },
        )
        raise

    conversations_repo.append_message(conv.id, role="assistant", text=result.reply_text)

    return InboundCallResponse(
        session_id=session.id,
        reply_text=result.reply_text,
        session_state=result.new_state,
        audio=audio,
    )


@router.post("/audio", response_model=CallAudioResponse)
async def call_audio(payload: CallAudioRequest) -> CallAudioResponse:
    """Handle additional audio/text from an active call."""
    session = sessions.session_store.get(payload.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    text = payload.text
    if not text and payload.audio:
        text = await conversation.speech_service.transcribe(payload.audio)

    conv = conversations_repo.get_by_session(payload.session_id)
    if conv and text:
        conversations_repo.append_message(conv.id, role="user", text=text)

    logger.info(
        "telephony_audio_input",
        extra={
            "session_id": payload.session_id,
            "has_text": bool(text),
        },
    )

    # For telephony we do not currently have a resolved business_id dependency
    # on this route, so voice session metrics are tracked globally only.
    metrics.voice_session_requests += 1

    try:
        result = await conversation.conversation_manager.handle_input(session, text)
        audio = await conversation.speech_service.synthesize(result.reply_text)
    except Exception:
        metrics.voice_session_errors += 1
        logger.exception(
            "telephony_audio_unhandled_error",
            extra={
                "session_id": payload.session_id,
            },
        )
        raise

    if conv:
        conversations_repo.append_message(conv.id, role="assistant", text=result.reply_text)

    return CallAudioResponse(
        reply_text=result.reply_text,
        session_state=result.new_state,
        audio=audio,
    )


@router.post("/end", response_model=CallEndResponse)
async def end_call(payload: CallEndRequest) -> CallEndResponse:
    """Mark a telephony call as ended."""
    sessions.session_store.end(payload.session_id)
    logger.info(
        "telephony_call_end",
        extra={
            "session_id": payload.session_id,
        },
    )
    return CallEndResponse(status=f"ended:{payload.session_id}")
