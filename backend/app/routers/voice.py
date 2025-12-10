import logging
import os

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..deps import ensure_onboarding_ready
from ..metrics import BusinessVoiceSessionMetrics, metrics
from ..repositories import conversations_repo, customers_repo
from ..services import conversation, sessions, subscription as subscription_service
from ..business_config import get_voice_for_business


router = APIRouter()
logger = logging.getLogger(__name__)


class SessionStartRequest(BaseModel):
    caller_phone: str | None = None
    provider_call_id: str | None = None
    lead_source: str | None = None


class SessionStartResponse(BaseModel):
    session_id: str


class SessionInputRequest(BaseModel):
    audio: str | None = None  # base64 or URL placeholder
    text: str | None = None


class SessionInputResponse(BaseModel):
    reply_text: str
    session_state: dict
    audio: str | None = None


class SessionEndResponse(BaseModel):
    status: str


@router.post("/session/start", response_model=SessionStartResponse)
async def start_session(
    payload: SessionStartRequest,
    business_id: str = Depends(ensure_onboarding_ready),
) -> SessionStartResponse:
    await subscription_service.check_access(
        business_id, feature="calls", upcoming_calls=1
    )
    # Track voice session API usage.
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
    conversations_repo.create(
        channel="phone",
        customer_id=customer.id if customer else None,
        session_id=session.id,
        business_id=business_id,
    )
    return SessionStartResponse(session_id=session.id)


@router.post("/session/{session_id}/input", response_model=SessionInputResponse)
async def session_input(
    session_id: str,
    payload: SessionInputRequest,
    business_id: str = Depends(ensure_onboarding_ready),
) -> SessionInputResponse:
    # Track voice session API usage.
    metrics.voice_session_requests += 1
    per_tenant = metrics.voice_sessions_by_business.setdefault(
        business_id, BusinessVoiceSessionMetrics()
    )
    per_tenant.requests += 1

    session = sessions.session_store.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    text = payload.text
    if not text and payload.audio:
        text = await conversation.speech_service.transcribe(payload.audio)

    conv = conversations_repo.get_by_session(session_id)
    if conv and text:
        conversations_repo.append_message(conv.id, role="user", text=text)

    try:
        result = await conversation.conversation_manager.handle_input(session, text)
        voice = get_voice_for_business(business_id)
        audio = await conversation.speech_service.synthesize(
            result.reply_text, voice=voice
        )
    except Exception as exc:
        # Track voice session errors globally and per tenant.
        metrics.voice_session_errors += 1
        per_tenant = metrics.voice_sessions_by_business.setdefault(
            business_id, BusinessVoiceSessionMetrics()
        )
        per_tenant.errors += 1
        logger.exception(
            "voice_session_unhandled_error",
            extra={"session_id": session_id, "business_id": business_id},
        )
        testing_mode = (
            os.getenv("TESTING", "false").lower() == "true"
            or os.getenv("PYTEST_CURRENT_TEST") is not None
        )
        if testing_mode:
            raise exc
        # Fail-safe: return a fallback response without raising, so telephony flows can
        # gracefully continue or forward the call.
        fallback_text = "I'm having trouble speaking right now. I'll notify the team and someone will call you back shortly."
        fallback_state: dict = {}
        session_state_attr = getattr(session, "state", None)
        if isinstance(session_state_attr, dict):
            fallback_state = session_state_attr
        else:
            stage = getattr(session, "stage", None)
            if stage is not None:
                fallback_state = {"stage": stage}
        return SessionInputResponse(
            reply_text=fallback_text,
            session_state=fallback_state,
            audio="audio://placeholder",
        )

    if conv:
        conversations_repo.append_message(
            conv.id, role="assistant", text=result.reply_text
        )

    return SessionInputResponse(
        reply_text=result.reply_text,
        session_state=result.new_state,
        audio=audio,
    )


@router.post("/session/{session_id}/end", response_model=SessionEndResponse)
async def end_session(session_id: str) -> SessionEndResponse:
    sessions.session_store.end(session_id)
    logger.info("voice_session_end", extra={"session_id": session_id})
    return SessionEndResponse(status=f"ended:{session_id}")
