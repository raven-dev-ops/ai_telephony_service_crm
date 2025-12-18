import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from ..deps import ensure_business_active
from ..metrics import BusinessVoiceSessionMetrics, metrics
from ..repositories import conversations_repo, customers_repo
from ..services import conversation, sessions
from ..services import subscription as subscription_service
from ..services.sms import sms_service
from ..business_config import get_voice_for_business


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
    request: Request,
    payload: InboundCallRequest,
    business_id: str = Depends(ensure_business_active),
) -> InboundCallResponse:
    """Entry point for a new telephony call.

    A real telephony provider (e.g., Twilio) would POST here when a call starts.
    """
    graceful = not request.url.path.startswith("/v1/")
    state = getattr(request.state, "subscription_state", None)
    if state is None:
        state = await subscription_service.check_access(
            business_id, feature="calls", upcoming_calls=1, graceful=graceful
        )
    if state.blocked:
        if not graceful:
            raise HTTPException(
                status_code=402,
                detail=state.message
                or "Subscription inactive. Calls will be routed to voicemail and automation is paused.",
                headers={"X-Subscription-Status": state.status},
            )
        msg = state.message or "Subscription inactive. Calls are routed to voicemail."
        voice = get_voice_for_business(business_id)
        try:
            audio = await conversation.speech_service.synthesize(msg, voice=voice)
        except Exception:
            audio = None
        # Best-effort owner notification so emergencies are not dropped silently.
        try:
            await sms_service.notify_owner(
                f"Call blocked for {business_id}: {msg}",
                business_id=business_id,
            )
        except Exception:
            logger.warning(
                "telephony_subscription_alert_failed",
                exc_info=True,
                extra={"business_id": business_id},
            )
        return InboundCallResponse(
            session_id="subscription_blocked",
            reply_text=msg,
            session_state={"subscription_blocked": True, "status": state.status},
            audio=audio,
        )
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
        voice = get_voice_for_business(business_id)
        audio = await conversation.speech_service.synthesize(
            result.reply_text, voice=voice
        )
        reply_text = result.reply_text
        new_state = result.new_state
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
        # Fail-safe so callers hear something instead of silence/timeouts.
        reply_text = (
            "We're having trouble right now. We'll call you back or you can leave a "
            "voicemail with your name and address."
        )
        audio = "audio://placeholder"
        new_state = {"stage": "ERROR", "status": "FAILED"}

    conversations_repo.append_message(conv.id, role="assistant", text=reply_text)

    return InboundCallResponse(
        session_id=session.id,
        reply_text=reply_text,
        session_state=new_state,
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
        voice = get_voice_for_business(
            getattr(session, "business_id", "default_business")
        )
        audio = await conversation.speech_service.synthesize(
            result.reply_text, voice=voice
        )
        reply_text = result.reply_text
        new_state = result.new_state
    except Exception:
        metrics.voice_session_errors += 1
        logger.exception(
            "telephony_audio_unhandled_error",
            extra={
                "session_id": payload.session_id,
            },
        )
        reply_text = (
            "Sorry, something went wrong on our end. "
            "We'll send a confirmation by text shortly."
        )
        audio = "audio://placeholder"
        new_state = {"stage": "ERROR", "status": "FAILED"}

    if conv:
        conversations_repo.append_message(conv.id, role="assistant", text=reply_text)

    return CallAudioResponse(
        reply_text=reply_text,
        session_state=new_state,
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
