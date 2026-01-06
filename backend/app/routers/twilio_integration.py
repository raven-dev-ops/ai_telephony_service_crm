from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import os
import struct
import time
import wave
from datetime import UTC, datetime, timedelta
from html import escape
import logging

from fastapi import (
    APIRouter,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from pydantic import BaseModel
from typing import Dict, TYPE_CHECKING

from ..config import get_settings
from ..db import SQLALCHEMY_AVAILABLE, SessionLocal
from ..db_models import BusinessDB
from ..context import call_sid_ctx, message_sid_ctx
from ..deps import DEFAULT_BUSINESS_ID, ensure_onboarding_ready
from ..metrics import (
    BusinessSmsMetrics,
    BusinessTwilioMetrics,
    CallbackItem,
    metrics,
)
from ..repositories import conversations_repo, customers_repo, appointments_repo
from ..services import (
    appointment_actions,
    conversation,
    audit as audit_service,
    sessions,
    subscription as subscription_service,
)
from ..services.idempotency import idempotency_store
from ..services.stt_tts import speech_service
from ..services.sms import sms_service
from ..business_config import get_language_for_business
from ..services.twilio_state import PendingAction, twilio_state_store
from . import owner as owner_routes

if TYPE_CHECKING:
    from ..models import Appointment, Conversation


logger = logging.getLogger(__name__)
router = APIRouter()


class TwilioStreamEvent(BaseModel):
    call_sid: str
    stream_sid: str | None = None
    event: str
    transcript: str | None = None
    business_id: str | None = None
    lead_source: str | None = None
    from_number: str | None = None


class TwilioStreamResponse(BaseModel):
    status: str
    session_id: str | None = None
    reply_text: str | None = None
    completed: bool = False


def _check_twilio_replay(event_id: str, window_seconds: int) -> None:
    """Basic replay protection for Twilio webhook event IDs."""
    if window_seconds <= 0:
        return
    key = f"twilio_event:{event_id}"
    if not idempotency_store.set_if_new(key, ttl_seconds=window_seconds):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Duplicate Twilio webhook event",
        )


async def _maybe_verify_twilio_signature(
    request: Request, form_params: Dict[str, str]
) -> None:
    """Optionally verify the Twilio signature on inbound webhooks.

    If VERIFY_TWILIO_SIGNATURES=true and TWILIO_AUTH_TOKEN is set in the
    environment, this validates the X-Twilio-Signature header using the
    standard Twilio algorithm (URL + sorted query/body params). In production
    (ENVIRONMENT=prod) signatures are required when provider is Twilio.
    """
    settings = get_settings()
    sms_cfg = settings.sms
    env = os.getenv("ENVIRONMENT", "dev").lower()
    provider = (getattr(sms_cfg, "provider", "") or "").lower()
    auth_token = getattr(sms_cfg, "twilio_auth_token", None)
    replay_window = getattr(sms_cfg, "replay_protection_seconds", 300) or 300
    require_sig = bool(
        getattr(sms_cfg, "verify_twilio_signatures", False)
        or (env == "prod" and provider == "twilio")
    )
    if provider == "stub" or not require_sig:
        return
    if not auth_token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Twilio auth token not configured",
        )

    twilio_sig = request.headers.get("X-Twilio-Signature")
    if not twilio_sig:
        await audit_service.record_security_event(
            request=request,
            event_type=audit_service.SECURITY_EVENT_WEBHOOK_SIGNATURE_MISSING,
            status_code=status.HTTP_401_UNAUTHORIZED,
            business_id=request.query_params.get("business_id"),
            meta={"provider": "twilio"},
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Twilio signature",
        )

    # Build the string to sign: URL (without query) plus sorted parameters
    # (query + form). This mirrors Twilio's validation helpers and includes
    # all form fields present on the request, not just the subset bound via
    # FastAPI function parameters.
    url = f"{request.url.scheme}://{request.url.netloc}{request.url.path}"
    params: Dict[str, str] = {}
    for key, value in request.query_params.multi_items():
        params[key] = value
    try:
        form = await request.form()
    except Exception:
        form = {}
    for key, value in form.items():
        params[str(key)] = str(value)
    # Include any explicitly provided form parameters as a final override to
    # keep behaviour stable with earlier revisions of this module.
    params.update(form_params)

    data = url + "".join(f"{k}{params[k]}" for k in sorted(params.keys()))
    digest = hmac.new(
        sms_cfg.twilio_auth_token.encode("utf-8"),
        data.encode("utf-8"),
        hashlib.sha1,
    ).digest()
    expected_sig = base64.b64encode(digest).decode("utf-8")

    if not hmac.compare_digest(expected_sig, twilio_sig):
        await audit_service.record_security_event(
            request=request,
            event_type=audit_service.SECURITY_EVENT_WEBHOOK_SIGNATURE_INVALID,
            status_code=status.HTTP_401_UNAUTHORIZED,
            business_id=request.query_params.get("business_id"),
            meta={"provider": "twilio"},
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Twilio signature",
        )

    # Optional timestamp guard to bound skew; when absent we skip the check.
    ts_header = request.headers.get(
        "X-Twilio-Request-Timestamp"
    ) or request.headers.get("Twilio-Request-Timestamp")
    if ts_header:
        try:
            ts_val = int(float(ts_header))
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid Twilio timestamp",
            )
        if abs(time.time() - ts_val) > replay_window:
            await audit_service.record_security_event(
                request=request,
                event_type=audit_service.SECURITY_EVENT_WEBHOOK_REPLAY_BLOCKED,
                status_code=status.HTTP_400_BAD_REQUEST,
                business_id=request.query_params.get("business_id"),
                meta={"provider": "twilio", "reason": "timestamp_out_of_window"},
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Twilio request timestamp outside allowed window",
            )

    event_id = request.headers.get("X-Twilio-EventId") or request.headers.get(
        "Twilio-Event-Id"
    )
    if event_id:
        try:
            _check_twilio_replay(event_id, replay_window)
        except HTTPException as exc:
            if exc.status_code == status.HTTP_409_CONFLICT:
                await audit_service.record_security_event(
                    request=request,
                    event_type=audit_service.SECURITY_EVENT_WEBHOOK_REPLAY_BLOCKED,
                    status_code=exc.status_code,
                    business_id=request.query_params.get("business_id"),
                    meta={"provider": "twilio", "reason": "event_id_replay"},
                )
            raise


def _get_business_name(business_id: str | None) -> str:
    """Return the business display name for SMS text.

    Falls back to the reference tenant name when the DB is unavailable.
    """
    default_name = conversation.DEFAULT_BUSINESS_NAME
    if not business_id or not (SQLALCHEMY_AVAILABLE and SessionLocal is not None):
        return default_name
    session_db = SessionLocal()
    try:
        row = session_db.get(BusinessDB, business_id)
    finally:
        session_db.close()
    if row is not None and getattr(row, "name", None):
        return row.name
    return default_name


def _twilio_say_language_attr(language_code: str) -> str:
    """Return a TwiML language attribute for <Say> based on tenant language.

    The mapping is driven by SMS/Twilio settings so deployments can override
    the language codes without touching application code.
    """
    settings = get_settings()
    sms_cfg = settings.sms
    lang: str | None
    if language_code.lower().startswith("es"):
        lang = getattr(sms_cfg, "twilio_say_language_es", None)
    else:
        lang = getattr(sms_cfg, "twilio_say_language_default", None)
    if not lang:
        return ""
    return f' language="{lang}"'


def _build_stream_url(
    request: Request,
    call_sid: str,
    business_id: str,
    lead_source: str | None,
    from_number: str | None,
) -> str:
    """Return the WebSocket URL Twilio should stream audio to.

    Uses TWILIO_STREAM_BASE_URL when provided; otherwise builds a ws/wss URL
    from the inbound request host.
    """
    settings = get_settings()
    base = getattr(settings, "telephony", None)
    stream_base = getattr(base, "twilio_stream_base_url", None) if base else None
    stream_token = getattr(base, "twilio_stream_token", None) if base else None
    if stream_base:
        url = stream_base
    else:
        scheme = "wss" if request.url.scheme == "https" else "ws"
        url = f"{scheme}://{request.url.netloc}/v1/twilio/voice-stream"
    sep = "&" if "?" in url else "?"
    url = f"{url}{sep}call_sid={call_sid}&business_id={business_id}"
    if lead_source:
        url = f"{url}&lead_source={lead_source}"
    if from_number:
        url = f"{url}&from_number={from_number}"
    if stream_token:
        url = f"{url}&stream_token={stream_token}"
    return url


def _safe_int(value: object, default: int) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _stream_min_bytes(sample_rate: int, encoding: str, min_seconds: float) -> int:
    encoding_norm = (encoding or "").lower()
    bytes_per_sample = 1 if ("mulaw" in encoding_norm or "ulaw" in encoding_norm) else 2
    safe_seconds = max(min_seconds, 0.1)
    return max(1, int(sample_rate * bytes_per_sample * safe_seconds))


# audioop was removed in Python 3.13; keep a local mu-law decoder for streaming.
def _build_mulaw_decode_table() -> tuple[int, ...]:
    table: list[int] = []
    for value in range(256):
        mu_law = (~value) & 0xFF
        sign = mu_law & 0x80
        exponent = (mu_law >> 4) & 0x07
        mantissa = mu_law & 0x0F
        sample = ((mantissa << 3) + 0x84) << exponent
        sample -= 0x84
        if sign:
            sample = -sample
        table.append(sample)
    return tuple(table)


_MULAW_DECODE_TABLE = _build_mulaw_decode_table()


def _mulaw_to_pcm(payload: bytes) -> bytes:
    if not payload:
        return b""
    pcm = bytearray(len(payload) * 2)
    for idx, byte_value in enumerate(payload):
        struct.pack_into("<h", pcm, idx * 2, _MULAW_DECODE_TABLE[byte_value])
    return bytes(pcm)


def _pcm_to_wav_bytes(
    pcm_bytes: bytes, sample_rate: int, sample_width: int = 2
) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(sample_width)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm_bytes)
    return buffer.getvalue()


def _twilio_payload_to_wav_base64(
    payload: bytes, encoding: str, sample_rate: int
) -> str | None:
    if not payload:
        return None
    encoding_norm = (encoding or "").lower()
    pcm_bytes = payload
    sample_width = 2
    if "mulaw" in encoding_norm or "ulaw" in encoding_norm:
        try:
            pcm_bytes = _mulaw_to_pcm(payload)
        except Exception:
            return None
    wav_bytes = _pcm_to_wav_bytes(pcm_bytes, sample_rate, sample_width=sample_width)
    return base64.b64encode(wav_bytes).decode("ascii")


def _find_next_appointment_for_phone(
    phone: str,
    business_id: str,
) -> Appointment | None:
    """Return the next upcoming appointment for this phone/business, if any.

    This is intentionally conservative: we only look for the earliest future
    appointment and ignore cancelled ones.
    """
    from datetime import UTC, datetime as _dt  # local import to avoid cycles

    now = _dt.now(UTC)
    # Resolve customer first; if we cannot, bail out.
    customer = customers_repo.get_by_phone(phone, business_id=business_id)
    if not customer:
        return None
    upcoming = []
    for appt in appointments_repo.list_for_business(business_id):
        if appt.customer_id != customer.id:
            continue
        if getattr(appt, "status", "").upper() == "CANCELLED":
            continue
        if appt.start_time <= now:
            continue
        upcoming.append(appt)
    if not upcoming:
        return None
    upcoming.sort(key=lambda a: a.start_time)
    return upcoming[0]


def _format_appointment_time(appt) -> str:
    when = getattr(appt, "start_time", None)
    if not when:
        return ""
    return when.strftime("%a %b %d at %I:%M %p UTC")


def _ensure_sms_conversation(business_id: str, from_phone: str) -> Conversation | None:
    """Return or create the SMS conversation for a customer/phone."""

    link = twilio_state_store.get_sms_conversation(business_id, from_phone)
    conv = conversations_repo.get(link.conversation_id) if link else None
    if conv:
        return conv
    customer = customers_repo.get_by_phone(from_phone, business_id=business_id)
    conv = conversations_repo.create(
        channel="sms",
        customer_id=customer.id if customer else None,
        business_id=business_id,
    )
    twilio_state_store.set_sms_conversation(business_id, from_phone, conv.id)
    return conv


def _email_alerts_enabled(business_row: BusinessDB | None) -> bool:
    if business_row is None:
        return True
    val = getattr(business_row, "owner_email_alerts_enabled", None)
    return True if val is None else bool(val)


async def _maybe_alert_on_speech_circuit(
    business_id: str,
    owner_phone: str | None,
    owner_email: str | None,
    business_row: BusinessDB | None,
    call_sid: str | None = None,
) -> None:
    """Notify owners when the speech circuit is open to prompt troubleshooting."""
    diag = speech_service.diagnostics()
    circuit_open = bool(diag.get("circuit_open"))
    if not circuit_open:
        metrics.speech_alerted_businesses.discard(business_id)
        return
    if business_id in metrics.speech_alerted_businesses:
        return
    last_error = diag.get("last_error")
    base_message = "Speech provider is degraded; using fallback prompts."
    if last_error:
        base_message = f"Speech provider issue: {last_error}. Using fallback prompts."
    alert_sent = False
    if owner_phone or (owner_email and _email_alerts_enabled(business_row)):
        try:
            from ..services.owner_notifications import notify_owner_with_fallback

            result = await notify_owner_with_fallback(
                business_id=business_id,
                message=base_message,
                subject="Speech provider degraded",
                dedupe_key="speech_circuit_open",
                send_email_copy=bool(
                    owner_email and _email_alerts_enabled(business_row)
                ),
            )
            alert_sent = result.delivered
        except Exception:
            logger.warning(
                "speech_circuit_sms_alert_failed",
                exc_info=True,
                extra={"business_id": business_id, "call_sid": call_sid},
            )
    if alert_sent:
        metrics.speech_alerted_businesses.add(business_id)


def _owner_emergency_counts_last_days(business_id: str, days: int) -> tuple[int, int]:
    """Return (total_appointments, emergency_appointments) for the last N days."""
    now = datetime.now(UTC)
    window = now - timedelta(days=days)
    total = 0
    emergency = 0
    for appt in appointments_repo.list_for_business(business_id):
        start_time = getattr(appt, "start_time", None)
        if not start_time:
            continue
        if start_time < window or start_time > now:
            continue
        status = getattr(appt, "status", "SCHEDULED").upper()
        if status not in {"SCHEDULED", "CONFIRMED"}:
            continue
        total += 1
        if bool(getattr(appt, "is_emergency", False)):
            emergency += 1
    return total, emergency


def _owner_summary_for_selection(
    selection: str,
    business_id: str,
    language_code: str,
    business_name: str,
) -> str:
    """Return a spoken summary for the owner IVR based on the menu selection.

    This helper is used for both the voice reply and optional SMS copy so
    that "read out my pipeline" aligns with what is sent over text.
    """
    # Tomorrow's schedule.
    if selection == "1":
        schedule = owner_routes.tomorrow_schedule(business_id=business_id)
        if language_code == "es":
            appointments = list(schedule.appointments or [])
            if not appointments:
                if business_name:
                    return f"Mañana no tienes citas programadas para {business_name}."
                return "Mañana no tienes citas programadas."
            count = len(appointments)
            first = appointments[0]
            time_str = first.start_time.strftime("%I:%M %p").lstrip("0")
            prefix = f"Mañana tienes {count} cita{'s' if count != 1 else ''}"
            if business_name:
                prefix = f"{prefix} para {business_name}"
            return (
                f"{prefix}. Tu primera cita comienza a las {time_str} con "
                f"{first.customer_name}."
            )
        # Default to the existing English summary used by the owner dashboard.
        return schedule.reply_text

    # Emergency appointments in the last 7 days.
    if selection == "2":
        total, emergency = _owner_emergency_counts_last_days(business_id, days=7)
        if language_code == "es":
            if total == 0:
                return "En los últimos siete días no tienes citas en el calendario."
            return (
                f"En los últimos siete días tienes {total} cita"
                f"{'s' if total != 1 else ''}, de las cuales {emergency} "
                "están marcadas como trabajos de emergencia."
            )
        if total == 0:
            return "In the last seven days, you have no appointments on the calendar."
        return (
            f"In the last seven days, you have {total} appointments, "
            f"of which {emergency} are flagged as emergency jobs."
        )

    # Pipeline summary for the last 30 days.
    if selection == "3":
        pipeline = owner_routes.owner_pipeline(business_id=business_id, days=30)
        stages = pipeline.stages
        total_value = pipeline.total_estimated_value or 0.0
        if language_code == "es":
            if not stages:
                return "Actualmente no tienes citas con etapas de trabajo registradas en tu pipeline."
            top_stage = stages[0]
            return (
                "Tu pipeline de los últimos treinta días tiene un valor "
                f"estimado total de {total_value:.0f} dólares. "
                f"La etapa más grande es {top_stage.stage} con "
                f"{top_stage.count} trabajos."
            )
        if not stages:
            return "You currently have no appointments with job stages recorded in your pipeline."
        top_stage = stages[0]
        return (
            f"Your pipeline over the last thirty days has an estimated total value of "
            f"{total_value:.0f} dollars. "
            f"The largest stage is {top_stage.stage} with {top_stage.count} jobs."
        )

    # Fallback for unknown selections (the caller will normally be redirected
    # back to the menu before this is reached).
    if language_code == "es":
        return (
            "No entendí esa selección. Para el horario de mañana, marca 1. "
            "Para las citas de emergencia de los últimos siete días, marca 2. "
            "Para un resumen de tu pipeline, marca 3."
        )
    return (
        "I did not understand that selection. "
        "For tomorrow's schedule, press 1. "
        "For emergency appointments in the last seven days, press 2. "
        "For your pipeline summary, press 3."
    )


def _build_gather_twiml(
    reply_text: str,
    action: str,
    say_language_attr: str,
) -> str:
    safe_reply = escape(reply_text)
    return f"""
<Response>
  <Say voice="alice"{say_language_attr}>{safe_reply}</Say>
  <Gather input="speech" action="{action}" method="POST" speechTimeout="auto" />
</Response>
""".strip()


@router.post("/voice", response_class=Response)
async def twilio_voice(
    request: Request,
    CallSid: str = Form(...),
    From: str | None = Form(default=None),
    CallStatus: str | None = Form(default=None),
    SpeechResult: str | None = Form(default=None),
    business_id_param: str | None = Query(default=None, alias="business_id"),
    lead_source_param: str | None = Query(default=None, alias="lead_source"),
    uptime_check_param: bool = Query(default=False, alias="uptime_check"),
) -> Response:
    """Twilio Voice webhook that bridges to the conversation manager.

    This endpoint expects Twilio to be configured for speech input in a <Gather>
    and will respond with TwiML that speaks the assistant reply and gathers
    further speech input.
    """
    # Resolve tenant for this webhook. For multi-tenant scenarios, configure
    # the Twilio webhook URL with a `?business_id=...` query parameter per
    # tenant; otherwise we fall back to the default single-tenant ID.
    call_sid_ctx.set(CallSid)
    business_id = business_id_param or DEFAULT_BUSINESS_ID
    business_row: BusinessDB | None = None

    logger.info(
        "twilio_voice_webhook",
        extra={
            "business_id": business_id,
            "call_sid": CallSid,
            "from": From,
            "call_status": CallStatus,
        },
    )

    sub_state = await subscription_service.check_access(
        business_id, feature="calls", upcoming_calls=1, graceful=True
    )
    if sub_state.blocked:
        msg = sub_state.message or "Subscription inactive. Calls are paused."
        return Response(
            content=f"<Response><Say>{escape(msg)}</Say></Response>",
            media_type="text/xml",
            status_code=status.HTTP_200_OK,
        )

    # Track Twilio voice webhook usage.
    metrics.twilio_voice_requests += 1
    per_tenant = metrics.twilio_by_business.setdefault(
        business_id, BusinessTwilioMetrics()
    )
    per_tenant.voice_requests += 1

    # Enforce onboarding completion for telephony flows unless disabled in tests.
    await ensure_onboarding_ready(business_id)

    # Resolve language once for this call so we can adjust TwiML voices and
    # error messages when tenants are configured for Spanish.
    language_code = get_language_for_business(business_id)
    say_language_attr = _twilio_say_language_attr(language_code)

    owner_phone = None
    owner_email = None
    # If the business is suspended, reject early.
    conv_id_for_log: str | None = None
    if SQLALCHEMY_AVAILABLE and SessionLocal is not None:
        session_db = SessionLocal()
        try:
            business_row = session_db.get(BusinessDB, business_id)
        finally:
            session_db.close()
        if (
            business_row is not None
            and getattr(business_row, "status", "ACTIVE") != "ACTIVE"
        ):
            logger.warning(
                "twilio_voice_business_suspended",
                extra={"business_id": business_id, "call_sid": CallSid},
            )
            return Response(
                content="<Response></Response>",
                media_type="text/xml",
                status_code=status.HTTP_403_FORBIDDEN,
            )
        if business_row is not None:
            owner_phone = getattr(business_row, "owner_phone", None)
            owner_email = getattr(business_row, "owner_email", None)

    await _maybe_alert_on_speech_circuit(
        business_id, owner_phone, owner_email, business_row, call_sid=CallSid
    )

    # Optional signature verification.
    form_params: Dict[str, str] = {"CallSid": CallSid}
    if From is not None:
        form_params["From"] = From
    if CallStatus is not None:
        form_params["CallStatus"] = CallStatus
    if SpeechResult is not None:
        form_params["SpeechResult"] = SpeechResult
    await _maybe_verify_twilio_signature(request, form_params)

    if uptime_check_param:
        return Response(content="<Response/>", media_type="text/xml")
    event_id = request.headers.get("X-Twilio-EventId") or request.headers.get(
        "Twilio-Event-Id"
    )

    try:
        # If the call has ended, we can clean up and optionally enqueue a callback
        # or partial-lead follow-up SMS.
        ended_statuses = {
            "completed",
            "canceled",
            "busy",
            "failed",
            "no-answer",
        }
        # Per-tenant configuration of which Twilio call statuses count as
        # "missed" (and should be enqueued for callbacks). When not
        # configured, fall back to a conservative default set.
        if business_row is not None:
            raw = getattr(business_row, "twilio_missed_statuses", None)
            if raw:
                parts = [p.strip().lower() for p in str(raw).split(",") if p.strip()]
                if parts:
                    ended_statuses.update(parts)
        if CallStatus and CallStatus.lower() in ended_statuses:
            link = twilio_state_store.get_call_session(CallSid)
            if link and event_id and getattr(link, "last_event_id", None) == event_id:
                logger.info(
                    "twilio_webhook_duplicate",
                    extra={
                        "call_sid": CallSid,
                        "event_id": event_id,
                        "status": CallStatus,
                    },
                )
                return Response(content="<Response/>", media_type="text/xml")
            link = twilio_state_store.clear_call_session(CallSid)
            session = None
            is_partial_lead = False
            if link and link.session_id:
                session = sessions.session_store.get(link.session_id)
                # Detect calls that dropped before the assistant finished intake.
                if session is not None:
                    status_val = (getattr(session, "status", "") or "").upper()
                    if status_val not in conversation.ALLOWED_TERMINAL_STATUSES:
                        is_partial_lead = True
                sessions.session_store.end(link.session_id)
            from ..metrics import metrics as _metrics  # local import
            from ..services.sms import sms_service  # local import to avoid cycles
            from ..business_config import (  # local import
                get_language_for_business as _get_language_for_business,
            )

            phone = From or CallSid or ""
            now = datetime.now(UTC)
            queue = _metrics.callbacks_by_business.setdefault(business_id, {})
            existing = queue.get(phone)
            lead_source = getattr(session, "lead_source", None)
            reason_code = "PARTIAL_INTAKE" if is_partial_lead else "MISSED_CALL"
            if existing is None:
                queue[phone] = CallbackItem(
                    phone=phone,
                    first_seen=now,
                    last_seen=now,
                    count=1,
                    channel="phone",
                    lead_source=lead_source,
                    reason=reason_code,
                )
            else:
                existing.last_seen = now
                existing.count += 1
                if lead_source:
                    existing.lead_source = lead_source
                if is_partial_lead:
                    existing.reason = "PARTIAL_INTAKE"
                if getattr(existing, "status", "PENDING").upper() != "PENDING":
                    existing.status = "PENDING"
                    existing.last_result = None

            # Notify owner about missed/partial calls.
            reason = "Partial intake" if is_partial_lead else "Missed call"
            when_str = now.strftime("%Y-%m-%d %H:%M UTC")
            owner_message = f"{reason} from {phone} at {when_str}."
            if owner_phone or owner_email:
                try:
                    from ..services.owner_notifications import (
                        notify_owner_with_fallback,
                    )

                    await notify_owner_with_fallback(
                        business_id=business_id,
                        message=owner_message,
                        subject="Missed call alert",
                        dedupe_key=f"missed_call_{phone}",
                        send_email_copy=bool(
                            owner_email and _email_alerts_enabled(business_row)
                        ),
                    )
                except Exception:
                    logger.warning(
                        "owner_notify_failed",
                        exc_info=True,
                        extra={"business_id": business_id, "reason": reason},
                    )
            if link:
                twilio_state_store.set_call_session(
                    CallSid, link.session_id, state="ended", event_id=event_id
                )

            # For partial leads where the assistant answered but the caller
            # dropped before intake completed, send a gentle SMS asking
            # for a quick summary so the team can follow up.
            if is_partial_lead and phone:
                # Best-effort check for SMS opt-out.
                customer = customers_repo.get_by_phone(phone, business_id=business_id)
                if not customer or not getattr(customer, "sms_opt_out", False):
                    language_code = _get_language_for_business(business_id)
                    business_name = conversation.DEFAULT_BUSINESS_NAME
                    if business_row is not None and getattr(business_row, "name", None):
                        business_name = business_row.name  # type: ignore[assignment]
                    if language_code == "es":
                        body = (
                            f"Sentimos no haber podido completar tu llamada con {business_name}. "
                            "Si aun necesitas ayuda, respóndenos con un breve resumen del problema "
                            "y te contactaremos."
                        )
                    else:
                        body = (
                            f"Sorry we couldn't finish your call with {business_name}. "
                            "If you still need help, please reply with a quick summary of the issue "
                            "and we'll follow up."
                        )
                    # Fire-and-forget SMS; errors are handled inside sms_service.
                    await sms_service.notify_customer(
                        phone,
                        body,
                        business_id=business_id,
                    )
            return Response(content="<Response/>", media_type="text/xml")

        # Get or create an internal session for this Twilio call.
        link = twilio_state_store.get_call_session(CallSid)
        if link:
            if event_id and getattr(link, "last_event_id", None) == event_id:
                logger.info(
                    "twilio_webhook_duplicate",
                    extra={
                        "call_sid": CallSid,
                        "event_id": event_id,
                        "status": CallStatus,
                    },
                )
                return Response(content="<Response/>", media_type="text/xml")
            session = sessions.session_store.get(link.session_id)
            session_id = link.session_id
        else:
            session = sessions.session_store.create(
                caller_phone=From,
                business_id=business_id,
                lead_source=lead_source_param,
            )
            session_id = session.id
            twilio_state_store.set_call_session(
                CallSid, session_id, state="active", event_id=event_id
            )
            # Create a conversation record for logging.
            customer = (
                customers_repo.get_by_phone(From or "", business_id=business_id)
                if From
                else None
            )
            conv = conversations_repo.create(
                channel="phone",
                customer_id=customer.id if customer else None,
                session_id=session_id,
                business_id=business_id,
            )
            conv_id_for_log = conv.id
            conversations_repo.append_message(
                conv.id,
                role="assistant",
                text="Call started",
            )

        # Bridge Twilio's speech result into the conversation manager.
        text = SpeechResult or ""
        silent_turn = not (text and text.strip())
        if silent_turn:
            session.no_input_count = getattr(session, "no_input_count", 0) + 1  # type: ignore[attr-defined]
        else:
            session.no_input_count = 0  # type: ignore[attr-defined]
        if text:
            conv = conversations_repo.get_by_session(session_id)
            if conv:
                conversations_repo.append_message(conv.id, role="user", text=text)

        if silent_turn and getattr(session, "no_input_count", 0) == 1:
            session.updated_at = datetime.now(UTC)
            sessions.session_store.save(session)
            if language_code == "es":
                prompt = "No alcancAc a escucharte bien. Por favor di tu respuesta o marca 1 para sA- o 2 para no."
            else:
                prompt = "I'm having trouble hearing you. Please say your answer, or press 1 for yes or 2 for no."
            safe_reply = escape(prompt)
            gather_action = (
                f"/twilio/voice?business_id={business_id}"
                if business_id_param
                else "/twilio/voice"
            )
            twiml = f"""
  <Response>
    <Say voice="alice"{say_language_attr}>{safe_reply}</Say>
    <Gather input="speech dtmf" numDigits="1" action="{gather_action}" method="POST" />
  </Response>
  """.strip()
            return Response(content=twiml, media_type="text/xml")

        if silent_turn and getattr(session, "no_input_count", 0) >= 2:
            queue = metrics.callbacks_by_business.setdefault(business_id, {})
            now = datetime.now(UTC)
            phone = From or CallSid or ""
            existing = queue.get(phone)
            lead_source = getattr(session, "lead_source", None)
            if existing is None:
                queue[phone] = CallbackItem(
                    phone=phone,
                    first_seen=now,
                    last_seen=now,
                    count=1,
                    channel="phone",
                    lead_source=lead_source,
                    reason="NO_INPUT",
                )
            else:
                existing.last_seen = now
                existing.count += 1
                existing.reason = "NO_INPUT"
                if lead_source:
                    existing.lead_source = lead_source
                if getattr(existing, "status", "PENDING").upper() != "PENDING":
                    existing.status = "PENDING"
                    existing.last_result = None
            session.stage = "COMPLETED"
            session.status = "PENDING_FOLLOWUP"
            session.updated_at = datetime.now(UTC)
            sessions.session_store.save(session)
            if language_code == "es":
                reply_text = "Tengo problemas para escucharte. Te enviarAc al buzA3n de voz para que dejes tu nombre y direcciA3n."
            else:
                reply_text = "I'm having trouble hearing you. I'm sending you to voicemail so you can leave your name and address."
            safe_reply = escape(reply_text)
            allow_voicemail = getattr(get_settings().sms, "enable_voicemail", True)
            record_block = ""
            if allow_voicemail:
                action = "/twilio/voicemail"
                if business_id_param:
                    action = f"{action}?business_id={business_id}"
                record_block = f'<Record action="{action}" method="POST" playBeep="true" timeout="5" />'
            twiml = f"""
  <Response>
    <Say voice="alice"{say_language_attr}>{safe_reply}</Say>
    {record_block}
    <Hangup/>
  </Response>
  """.strip()
            return Response(content=twiml, media_type="text/xml")

        result = await conversation.conversation_manager.handle_input(
            session, text or None
        )

        conv = conversations_repo.get_by_session(session_id)
        conv_id = conv.id if conv else conv_id_for_log
        if conv_id:
            conversations_repo.append_message(
                conv_id, role="assistant", text=result.reply_text
            )

        # Build TwiML response. Preserve the business_id query parameter on the
        # <Gather> action when present so subsequent turns stay routed to the
        # same tenant.
        safe_reply = escape(result.reply_text)
        if business_id_param:
            gather_action = f"/twilio/voice?business_id={business_id}"
        else:
            gather_action = "/twilio/voice"
        twiml = f"""
  <Response>
    <Say voice="alice"{say_language_attr}>{safe_reply}</Say>
    <Gather input="speech" action="{gather_action}" method="POST" />
  </Response>
  """.strip()
        return Response(content=twiml, media_type="text/xml")
    except Exception:  # pragma: no cover - defensive
        # Track Twilio voice errors globally and per tenant.
        metrics.twilio_voice_errors += 1
        per_tenant = metrics.twilio_by_business.setdefault(
            business_id, BusinessTwilioMetrics()
        )
        per_tenant.voice_errors += 1
        logger.exception(
            "twilio_voice_unhandled_error",
            extra={
                "business_id": business_id,
                "call_sid": CallSid,
                "from": From,
                "call_status": CallStatus,
            },
        )
        if language_code == "es":
            message = (
                "Lo siento, hubo un problema al manejar tu llamada. "
                "Por favor cuelga e inténtalo de nuevo en unos minutos. "
                "Si se trata de una emergencia que ponga en riesgo la vida, "
                "cuelga ahora y llama al 911 o a tu número de emergencias local."
            )
        else:
            message = (
                "Sorry, something went wrong while handling your call. "
                "Please hang up and try again in a few minutes. "
                "If this is a life-threatening emergency, hang up now and call 911 or your local emergency number."
            )
        safe_reply = escape(message)
        settings = get_settings()
        allow_voicemail = getattr(settings.sms, "enable_voicemail", True)
        record_block = ""
        if allow_voicemail:
            action = "/twilio/voicemail"
            if business_id_param:
                action = f"{action}?business_id={business_id}"
            record_block = f'<Record action="{action}" method="POST" playBeep="true" timeout="5" />'
        twiml = f"""
<Response>
  <Say voice="alice"{say_language_attr}>{safe_reply}</Say>
  {record_block}
  <Hangup/>
</Response>
""".strip()
        return Response(content=twiml, media_type="text/xml")


@router.post("/owner-voice", response_class=Response)
async def twilio_owner_voice(
    request: Request,
    CallSid: str = Form(...),
    From: str | None = Form(default=None),
    Digits: str | None = Form(default=None),
    business_id_param: str | None = Query(default=None, alias="business_id"),
    step_param: str | None = Query(default=None, alias="step"),
    selection_param: str | None = Query(default=None, alias="selection"),
) -> Response:
    """Twilio Voice webhook for a simple owner-focused IVR.

    Menu:
    - 1: Tomorrow's schedule summary.
    - 2: Emergency appointments in the last 7 days.
    - 3: Pipeline summary for the last 30 days.
    """
    call_sid_ctx.set(CallSid)
    business_id = business_id_param or DEFAULT_BUSINESS_ID
    language_code = get_language_for_business(business_id)

    logger.info(
        "twilio_owner_voice_webhook",
        extra={
            "business_id": business_id,
            "call_sid": CallSid,
            "from": From,
            "digits": Digits,
        },
    )

    # Track Twilio voice webhook usage (owner line shares the same counters).
    metrics.twilio_voice_requests += 1
    per_tenant = metrics.twilio_by_business.setdefault(
        business_id, BusinessTwilioMetrics()
    )
    per_tenant.voice_requests += 1

    # Optional signature verification.
    form_params: Dict[str, str] = {"CallSid": CallSid}
    if From is not None:
        form_params["From"] = From
    if Digits is not None:
        form_params["Digits"] = Digits
    await _maybe_verify_twilio_signature(request, form_params)

    # Best-effort owner phone validation and tenant status check.
    business_name = _get_business_name(business_id)
    if SQLALCHEMY_AVAILABLE and SessionLocal is not None:
        session_db = SessionLocal()
        try:
            row = session_db.get(BusinessDB, business_id)
        finally:
            session_db.close()
        if row is not None:
            status_value = getattr(row, "status", "ACTIVE")
            owner_phone = getattr(row, "owner_phone", None)
            if status_value != "ACTIVE":
                if language_code == "es":
                    safe_reply = escape(
                        "Este negocio está actualmente suspendido. "
                        "Por favor revisa tu panel de administración."
                    )
                else:
                    safe_reply = escape(
                        "This business is currently suspended. Please check your admin dashboard."
                    )
                twiml = f"""
<Response>
  <Say voice="alice">{safe_reply}</Say>
  <Hangup/>
</Response>
""".strip()
                return Response(content=twiml, media_type="text/xml")
            require_owner_match = (
                os.getenv("OWNER_VOICE_REQUIRE_MATCH", "false").lower() == "true"
            )
            if require_owner_match and owner_phone and From and From != owner_phone:
                if language_code == "es":
                    safe_reply = escape(
                        "Esta línea está reservada para el dueño del negocio. "
                        "Si llegaste aquí por error, por favor cuelga."
                    )
                else:
                    safe_reply = escape(
                        "This line is reserved for the business owner. "
                        "If you reached this by mistake, please hang up."
                    )
                twiml = f"""
<Response>
  <Say voice="alice">{safe_reply}</Say>
  <Hangup/>
</Response>
""".strip()
                return Response(content=twiml, media_type="text/xml")

    try:
        step = (step_param or "").strip().lower() or "menu"
        say_language_attr = _twilio_say_language_attr(language_code)

        # If this is a post-summary interaction (e.g. "text me that summary"
        # or "let me ask another question"), handle that first.
        if step == "post":
            selection_ctx = (selection_param or "").strip()
            if not Digits:
                # No choice given; fall back to the main menu.
                step = "menu"
            else:
                followup = (Digits or "").strip()
                if followup == "1":
                    # Return to the main menu so the owner can ask another question.
                    safe_name = escape(business_name)
                    if business_id_param:
                        gather_action = f"/twilio/owner-voice?business_id={business_id}"
                    else:
                        gather_action = "/twilio/owner-voice"
                    if language_code == "es":
                        prompt = (
                            f"Hola, esta es la línea del dueño para {safe_name}. "
                            "Para el horario de mañana, marca 1. "
                            "Para las citas de emergencia de los últimos siete días, marca 2. "
                            "Para un resumen de tu pipeline, marca 3."
                        )
                    else:
                        prompt = (
                            f"Hello, this is the owner line for {safe_name}. "
                            "For tomorrow's schedule, press 1. "
                            "For emergency appointments in the last seven days, press 2. "
                            "For your pipeline summary, press 3."
                        )
                    safe_prompt = escape(prompt)
                    twiml = f"""
<Response>
  <Say voice="alice"{say_language_attr}>{safe_prompt}</Say>
  <Gather input="dtmf" numDigits="1" action="{gather_action}" method="POST" />
</Response>
""".strip()
                    return Response(content=twiml, media_type="text/xml")
                if followup == "9" and selection_ctx in {"1", "2", "3"} and From:
                    # Send the same summary via SMS to the owner.
                    summary_text = _owner_summary_for_selection(
                        selection_ctx,
                        business_id=business_id,
                        language_code=language_code,
                        business_name=business_name,
                    )
                    from ..services.owner_notifications import (
                        notify_owner_with_fallback,
                    )

                    await notify_owner_with_fallback(
                        business_id=business_id,
                        message=summary_text,
                        subject="Owner summary",
                        dedupe_key=f"summary_{selection_ctx}",
                    )
                    if language_code == "es":
                        ack = "De acuerdo, te he enviado este resumen por mensaje de texto. Adiós."
                    else:
                        ack = "Okay, I've sent this summary to you by text message. Goodbye."
                    safe_ack = escape(ack)
                    twiml = f"""
<Response>
  <Say voice="alice"{say_language_attr}>{safe_ack}</Say>
  <Hangup/>
</Response>
""".strip()
                    return Response(content=twiml, media_type="text/xml")
                # Unknown follow-up choice; fall through to re-present the menu.
                step = "menu"

        # If no selection yet (or we fell back from post-step), present the menu.
        if step == "menu" and not Digits:
            safe_name = escape(business_name)
            if business_id_param:
                gather_action = f"/twilio/owner-voice?business_id={business_id}"
            else:
                gather_action = "/twilio/owner-voice"
            if language_code == "es":
                prompt = (
                    f"Hola, esta es la línea del dueño para {safe_name}. "
                    "Para el horario de mañana, marca 1. "
                    "Para las citas de emergencia de los últimos siete días, marca 2. "
                    "Para un resumen de tu pipeline, marca 3."
                )
            else:
                prompt = (
                    f"Hello, this is the owner line for {safe_name}. "
                    "For tomorrow's schedule, press 1. "
                    "For emergency appointments in the last seven days, press 2. "
                    "For your pipeline summary, press 3."
                )
            safe_prompt = escape(prompt)
            twiml = f"""
<Response>
  <Say voice="alice"{say_language_attr}>{safe_prompt}</Say>
  <Gather input="dtmf" numDigits="1" action="{gather_action}" method="POST" />
</Response>
""".strip()
            return Response(content=twiml, media_type="text/xml")

        # Handle primary menu selections.
        selection = (Digits or "").strip()
        if selection not in {"1", "2", "3"}:
            if business_id_param:
                gather_action = f"/twilio/owner-voice?business_id={business_id}"
            else:
                gather_action = "/twilio/owner-voice"
            invalid_msg = _owner_summary_for_selection(
                "invalid",
                business_id=business_id,
                language_code=language_code,
                business_name=business_name,
            )
            safe_invalid = escape(invalid_msg)
            twiml = f"""
<Response>
  <Say voice="alice"{say_language_attr}>{safe_invalid}</Say>
  <Gather input="dtmf" numDigits="1" action="{gather_action}" method="POST" />
</Response>
""".strip()
            return Response(content=twiml, media_type="text/xml")

        reply_text = _owner_summary_for_selection(
            selection,
            business_id=business_id,
            language_code=language_code,
            business_name=business_name,
        )
        safe_reply = escape(reply_text)

        # After reading the summary, allow the owner to either ask another
        # question or have the summary sent by SMS.
        if business_id_param:
            gather_action = (
                f"/twilio/owner-voice?business_id={business_id}"
                f"&step=post&selection={selection}"
            )
        else:
            gather_action = f"/twilio/owner-voice?step=post&selection={selection}"
        if language_code == "es":
            followup_prompt = (
                "Para escuchar otro resumen, marca 1. "
                "Para recibir este resumen por mensaje de texto, marca 9. "
                "También puedes colgar en cualquier momento."
            )
        else:
            followup_prompt = (
                "For another owner summary, press 1. "
                "To receive this summary by text message, press 9. "
                "Or you can hang up at any time."
            )
        safe_followup = escape(followup_prompt)
        twiml = f"""
<Response>
  <Say voice="alice"{say_language_attr}>{safe_reply}</Say>
  <Pause length="1"/>
  <Say voice="alice"{say_language_attr}>{safe_followup}</Say>
  <Gather input="dtmf" numDigits="1" action="{gather_action}" method="POST" />
</Response>
""".strip()
        return Response(content=twiml, media_type="text/xml")
    except Exception:  # pragma: no cover - defensive
        metrics.twilio_voice_errors += 1
        per_err = metrics.twilio_by_business.setdefault(
            business_id, BusinessTwilioMetrics()
        )
        per_err.voice_errors += 1
        logger.exception(
            "twilio_owner_voice_unhandled_error",
            extra={
                "business_id": business_id,
                "call_sid": CallSid,
                "from": From,
                "digits": Digits,
            },
        )
        safe_reply = escape(
            "Sorry, something went wrong while handling your call. "
            "Please hang up and try again in a few minutes."
        )
        twiml = f"""
<Response>
  <Say voice="alice">{safe_reply}</Say>
  <Hangup/>
</Response>
""".strip()
        return Response(content=twiml, media_type="text/xml")


@router.post("/voice-assistant", response_class=Response)
async def twilio_voice_assistant(
    request: Request,
    CallSid: str = Form(...),
    From: str | None = Form(default=None),
    CallStatus: str | None = Form(default=None),
    SpeechResult: str | None = Form(default=None),
    business_id_param: str | None = Query(default=None, alias="business_id"),
    lead_source_param: str | None = Query(default=None, alias="lead_source"),
    uptime_check_param: bool = Query(default=False, alias="uptime_check"),
) -> Response:
    """Twilio voice webhook that bridges live calls into the conversation manager.

    Uses Twilio <Gather input="speech"> for a simple streaming-lite loop:
    - On first hit, create a conversation session and prompt the assistant reply.
    - On subsequent hits with SpeechResult, route text into the assistant and
      return another Gather with the reply spoken via <Say>.
    """
    call_sid_ctx.set(CallSid)
    business_id = business_id_param or DEFAULT_BUSINESS_ID
    settings = get_settings()
    stream_enabled = bool(
        getattr(settings.telephony, "twilio_streaming_enabled", False)
    )

    sub_state = await subscription_service.check_access(
        business_id, feature="calls", upcoming_calls=1, graceful=True
    )
    if sub_state.blocked:
        msg = sub_state.message or "Subscription inactive. Calls are paused."
        return Response(
            content=f"<Response><Say>{escape(msg)}</Say></Response>",
            media_type="text/xml",
            status_code=status.HTTP_200_OK,
        )
    metrics.twilio_voice_requests += 1
    per_tenant = metrics.twilio_by_business.setdefault(
        business_id, BusinessTwilioMetrics()
    )
    per_tenant.voice_requests += 1

    await ensure_onboarding_ready(business_id)

    business_row: BusinessDB | None = None
    owner_phone = None
    owner_email = None
    if SQLALCHEMY_AVAILABLE and SessionLocal is not None:
        session_db = SessionLocal()
        try:
            business_row = session_db.get(BusinessDB, business_id)
        finally:
            session_db.close()
        if business_row is not None:
            owner_phone = getattr(business_row, "owner_phone", None)
            owner_email = getattr(business_row, "owner_email", None)

    await _maybe_alert_on_speech_circuit(
        business_id, owner_phone, owner_email, business_row, call_sid=CallSid
    )

    # Optional signature verification.
    form_params: Dict[str, str] = {"CallSid": CallSid}
    if From is not None:
        form_params["From"] = From
    if CallStatus is not None:
        form_params["CallStatus"] = CallStatus
    if SpeechResult is not None:
        form_params["SpeechResult"] = SpeechResult
    await _maybe_verify_twilio_signature(request, form_params)

    if uptime_check_param:
        return Response(content="<Response/>", media_type="text/xml")

    # Handle call completion quickly and enqueue a callback follow-up.
    if CallStatus and CallStatus.lower() in {
        "completed",
        "canceled",
        "busy",
        "failed",
        "no-answer",
    }:
        link = twilio_state_store.clear_call_session(CallSid)
        if link:
            sessions.session_store.end(link.session_id)
        # Record missed/partial call for owner follow-up.
        phone = From or CallSid or ""
        queue = metrics.callbacks_by_business.setdefault(business_id, {})
        existing = queue.get(phone)
        if existing is None:
            queue[phone] = CallbackItem(
                phone=phone,
                first_seen=datetime.now(UTC),
                last_seen=datetime.now(UTC),
                count=1,
                channel="phone",
                reason="MISSED_CALL",
            )
        else:
            existing.last_seen = datetime.now(UTC)
            existing.count += 1
            if getattr(existing, "status", "PENDING").upper() != "PENDING":
                existing.status = "PENDING"
                existing.last_result = None
        return Response(content="<Response/>", media_type="text/xml")

    # Resolve language for <Say>.
    language_code = get_language_for_business(business_id)
    say_language_attr = _twilio_say_language_attr(language_code)

    # Get or create the assistant session for this call.
    link = twilio_state_store.get_call_session(CallSid)
    session = None
    if link:
        session = sessions.session_store.get(link.session_id)
    if not session:
        session = sessions.session_store.create(
            caller_phone=From,
            business_id=business_id,
            lead_source=lead_source_param,
        )
        twilio_state_store.set_call_session(CallSid, session.id)
        customer = (
            customers_repo.get_by_phone(From, business_id=business_id) if From else None
        )
        conversations_repo.create(
            channel="phone",
            customer_id=customer.id if customer else None,
            session_id=session.id,
            business_id=business_id,
        )

    # Route speech input into the assistant.
    text = SpeechResult.strip() if SpeechResult else None
    silent_turn = not (text and text.strip())
    if silent_turn:
        count = getattr(session, "no_input_count", 0) + 1
        session.no_input_count = count  # type: ignore[attr-defined]
    else:
        session.no_input_count = 0  # type: ignore[attr-defined]
    conv = conversations_repo.get_by_session(session.id)
    if conv and text:
        conversations_repo.append_message(conv.id, role="user", text=text)

    if silent_turn and getattr(session, "no_input_count", 0) == 1:
        session.updated_at = datetime.now(UTC)
        sessions.session_store.save(session)
        if language_code == "es":
            reply_text = "No escuchA© tu respuesta. Por favor di tu respuesta o presiona 1 para sA- o 2 para no."
        else:
            reply_text = "I didn't catch that. Please say your answer, or press 1 for yes or 2 for no."
        safe_reply = escape(reply_text)
        gather_action = (
            f"/twilio/voice-assistant?business_id={business_id}"
            if business_id_param
            else "/twilio/voice-assistant"
        )
        twiml = f"""
<Response>
  <Say voice="alice"{say_language_attr}>{safe_reply}</Say>
  <Gather input="speech dtmf" numDigits="1" action="{gather_action}" method="POST" speechTimeout="auto" />
</Response>
""".strip()
        return Response(content=twiml, media_type="text/xml")

    if silent_turn and getattr(session, "no_input_count", 0) >= 2:
        queue = metrics.callbacks_by_business.setdefault(business_id, {})
        now = datetime.now(UTC)
        phone = From or CallSid or ""
        existing = queue.get(phone)
        if existing is None:
            queue[phone] = CallbackItem(
                phone=phone,
                first_seen=now,
                last_seen=now,
                count=1,
                channel="phone",
                reason="NO_INPUT",
            )
        else:
            existing.last_seen = now
            existing.count += 1
            existing.reason = "NO_INPUT"
            if getattr(existing, "status", "PENDING").upper() != "PENDING":
                existing.status = "PENDING"
                existing.last_result = None

        session.stage = "COMPLETED"
        session.status = "PENDING_FOLLOWUP"
        session.updated_at = datetime.now(UTC)
        sessions.session_store.save(session)
        if language_code == "es":
            reply_text = "Tengo problemas para escucharte. Te transferirAc para que dejes un breve buzA3n de voz con tu nombre y direcciA3n."
        else:
            reply_text = "I'm having trouble hearing you. I'm sending you to voicemail so you can leave your name and address."
        safe_reply = escape(reply_text)
        allow_voicemail = getattr(get_settings().sms, "enable_voicemail", True)
        record_block = ""
        if allow_voicemail:
            action = "/twilio/voicemail"
            if business_id_param:
                action = f"{action}?business_id={business_id}"
            record_block = f'<Record action="{action}" method="POST" playBeep="true" timeout="5" />'
        twiml = f"""
<Response>
  <Say voice="alice"{say_language_attr}>{safe_reply}</Say>
  {record_block}
  <Hangup/>
</Response>
""".strip()
        return Response(content=twiml, media_type="text/xml")

    try:
        result = await conversation.conversation_manager.handle_input(session, text)
        reply_text = result.reply_text
        if conv:
            conversations_repo.append_message(
                conv.id, role="assistant", text=reply_text
            )
            # Notify the owner when a new appointment gets booked via voice.
            appointments = getattr(result, "appointments", []) or []
            if appointments:
                appt = appointments[0]
                when = _format_appointment_time(appt)
                cust = (
                    customers_repo.get(appt.customer_id) if appt.customer_id else None
                )
                cust_name = cust.name if cust else "Customer"
                service = getattr(appt, "service_type", None) or "service"
                body = f"New voice booking: {cust_name} on {when} ({service})."
                try:
                    from ..services.owner_notifications import (
                        notify_owner_with_fallback,
                    )

                    await notify_owner_with_fallback(
                        business_id=business_id,
                        message=body,
                        subject="New voice booking",
                        dedupe_key=f"voice_booking_{appt.id}",
                    )
                except Exception:
                    logger.warning(
                        "owner_notify_failed",
                        exc_info=True,
                        extra={"business_id": business_id},
                    )
        if stream_enabled:
            stream_url = _build_stream_url(
                request, CallSid, business_id, lead_source_param, From
            )
            safe_reply = escape(reply_text or "Connecting you to the assistant.")
            twiml = f"""
<Response>
  <Start>
    <Stream url="{stream_url}" />
  </Start>
  <Say voice="alice"{say_language_attr}>{safe_reply}</Say>
</Response>
""".strip()
            return Response(content=twiml, media_type="text/xml")
        if business_id_param:
            gather_action = f"/twilio/voice-assistant?business_id={business_id}"
        else:
            gather_action = "/twilio/voice-assistant"
        twiml = _build_gather_twiml(reply_text, gather_action, say_language_attr)
        return Response(content=twiml, media_type="text/xml")
    except Exception:  # pragma: no cover - defensive
        metrics.twilio_voice_errors += 1
        per_err = metrics.twilio_by_business.setdefault(
            business_id, BusinessTwilioMetrics()
        )
        per_err.voice_errors += 1
        logger.exception(
            "twilio_voice_assistant_unhandled_error",
            extra={"business_id": business_id, "call_sid": CallSid},
        )
        safe_reply = escape(
            "Sorry, something went wrong while handling your call. "
            "Please hang up and try again later."
        )
        twiml = f"""
<Response>
  <Say voice="alice"{say_language_attr}>{safe_reply}</Say>
</Response>
""".strip()
        return Response(content=twiml, media_type="text/xml")


@router.websocket("/voice-stream")
async def twilio_voice_stream_websocket(websocket: WebSocket) -> None:
    """Handle Twilio Media Streams and forward transcripts to the HTTP stream handler."""
    await websocket.accept()
    settings = get_settings()
    if not getattr(settings.telephony, "twilio_streaming_enabled", False):
        await websocket.close(code=1008)
        return
    stream_token = getattr(settings.telephony, "twilio_stream_token", None)

    params = websocket.query_params
    if stream_token:
        provided_token = params.get("stream_token")
        if not provided_token or not hmac.compare_digest(provided_token, stream_token):
            await websocket.close(code=1008)
            return
    call_sid = params.get("call_sid") or ""
    business_id = params.get("business_id") or DEFAULT_BUSINESS_ID
    lead_source = params.get("lead_source")
    from_number = params.get("from_number")
    stream_sid = params.get("stream_sid")

    encoding = "audio/x-mulaw"
    sample_rate = 8000
    min_seconds = float(
        getattr(settings.telephony, "twilio_stream_min_seconds", 1.0) or 1.0
    )
    buffer = bytearray()

    async def flush_buffer() -> str | None:
        nonlocal buffer
        if not buffer:
            return None
        audio_b64 = _twilio_payload_to_wav_base64(bytes(buffer), encoding, sample_rate)
        buffer = bytearray()
        if not audio_b64:
            return None
        transcript = await speech_service.transcribe(audio_b64)
        transcript = (transcript or "").strip()
        return transcript or None

    async def handle_transcript(text: str) -> None:
        if not text or not call_sid:
            return
        await twilio_voice_stream(
            TwilioStreamEvent(
                call_sid=call_sid,
                stream_sid=stream_sid,
                event="media",
                transcript=text,
                business_id=business_id,
                lead_source=lead_source,
                from_number=from_number,
            )
        )

    async def handle_start() -> None:
        if not call_sid:
            return
        await twilio_voice_stream(
            TwilioStreamEvent(
                call_sid=call_sid,
                stream_sid=stream_sid,
                event="start",
                business_id=business_id,
                lead_source=lead_source,
                from_number=from_number,
            )
        )

    async def handle_stop() -> None:
        if not call_sid:
            return
        await twilio_voice_stream(
            TwilioStreamEvent(
                call_sid=call_sid,
                stream_sid=stream_sid,
                event="stop",
                business_id=business_id,
                lead_source=lead_source,
                from_number=from_number,
            )
        )

    try:
        while True:
            try:
                message = await websocket.receive_text()
            except WebSocketDisconnect:
                break
            try:
                payload = json.loads(message)
            except json.JSONDecodeError:
                logger.warning("twilio_stream_invalid_json")
                continue

            event = (payload.get("event") or "").lower()
            if event in {"start", "connected"}:
                start = payload.get("start") or {}
                call_sid = start.get("callSid") or call_sid
                stream_sid = start.get("streamSid") or stream_sid
                media_format = start.get("mediaFormat") or {}
                encoding = media_format.get("encoding") or encoding
                sample_rate = _safe_int(media_format.get("sampleRate"), sample_rate)
                custom_params = start.get("customParameters") or {}
                if not from_number:
                    from_number = custom_params.get("from_number") or custom_params.get(
                        "from"
                    )
                await handle_start()
                continue

            if event == "media":
                media = payload.get("media") or {}
                track = (media.get("track") or "").lower()
                if track and track != "inbound":
                    continue
                raw_payload = media.get("payload") or ""
                if not raw_payload:
                    continue
                try:
                    audio_bytes = base64.b64decode(raw_payload)
                except Exception:
                    logger.warning("twilio_stream_payload_decode_failed")
                    continue
                buffer.extend(audio_bytes)
                min_bytes = _stream_min_bytes(sample_rate, encoding, min_seconds)
                if len(buffer) >= min_bytes:
                    transcript = await flush_buffer()
                    if transcript:
                        await handle_transcript(transcript)
                continue

            if event == "stop":
                transcript = await flush_buffer()
                if transcript:
                    await handle_transcript(transcript)
                await handle_stop()
                break
    except Exception:  # pragma: no cover - defensive
        logger.exception("twilio_stream_websocket_error")
    finally:
        try:
            await websocket.close()
        except RuntimeError:
            pass


@router.post("/voice-stream", response_model=TwilioStreamResponse)
async def twilio_voice_stream(
    payload: TwilioStreamEvent,
) -> TwilioStreamResponse:
    """Handle Twilio media stream events and route transcripts to the assistant."""
    settings = get_settings()
    if not getattr(settings.telephony, "twilio_streaming_enabled", False):
        return TwilioStreamResponse(
            status="disabled", session_id=None, reply_text=None, completed=True
        )

    business_id = payload.business_id or DEFAULT_BUSINESS_ID
    owner_phone = None
    owner_email = None
    business_row: BusinessDB | None = None
    if SQLALCHEMY_AVAILABLE and SessionLocal is not None:
        session_db = SessionLocal()
        try:
            business_row = session_db.get(BusinessDB, business_id)
        finally:
            session_db.close()
        if business_row is not None:
            owner_phone = getattr(business_row, "owner_phone", None)
            owner_email = getattr(business_row, "owner_email", None)

    language_code = get_language_for_business(business_id)

    await _maybe_alert_on_speech_circuit(
        business_id, owner_phone, owner_email, business_row, call_sid=payload.call_sid
    )

    metrics.twilio_voice_requests += 1
    per_tenant = metrics.twilio_by_business.setdefault(
        business_id, BusinessTwilioMetrics()
    )
    per_tenant.voice_requests += 1

    link = twilio_state_store.get_call_session(payload.call_sid)
    session_obj = sessions.session_store.get(link.session_id) if link else None
    if not session_obj:
        await ensure_onboarding_ready(business_id)
        state = await subscription_service.check_access(
            business_id, feature="calls", upcoming_calls=1, graceful=True
        )
        if state.blocked:
            return TwilioStreamResponse(
                status="subscription_blocked",
                reply_text=state.message,
                completed=True,
            )
        session_obj = sessions.session_store.create(
            caller_phone=payload.from_number,
            business_id=business_id,
            lead_source=payload.lead_source,
        )
        twilio_state_store.set_call_session(payload.call_sid, session_obj.id)
        customer = (
            customers_repo.get_by_phone(payload.from_number, business_id=business_id)
            if payload.from_number
            else None
        )
        conversations_repo.create(
            channel="phone",
            customer_id=customer.id if customer else None,
            session_id=session_obj.id,
            business_id=business_id,
        )

    event = (payload.event or "").lower()
    if event == "stop":
        status_val = (getattr(session_obj, "status", "") or "").upper()
        is_partial_lead = status_val not in conversation.ALLOWED_TERMINAL_STATUSES
        phone = payload.from_number or ""
        lead_source = getattr(session_obj, "lead_source", None) or payload.lead_source
        if phone and (is_partial_lead or not session_obj):
            now = datetime.now(UTC)
            queue = metrics.callbacks_by_business.setdefault(business_id, {})
            existing = queue.get(phone)
            reason = "PARTIAL_INTAKE" if is_partial_lead else "MISSED_CALL"
            if existing is None:
                queue[phone] = CallbackItem(
                    phone=phone,
                    first_seen=now,
                    last_seen=now,
                    count=1,
                    channel="phone",
                    lead_source=lead_source,
                    reason=reason,
                )
            else:
                existing.last_seen = now
                existing.count += 1
                if lead_source:
                    existing.lead_source = lead_source
                if is_partial_lead:
                    existing.reason = "PARTIAL_INTAKE"
                if getattr(existing, "status", "PENDING").upper() != "PENDING":
                    existing.status = "PENDING"
                    existing.last_result = None

            owner_message = f"{'Partial intake' if is_partial_lead else 'Missed call'} from {phone} at {now.strftime('%Y-%m-%d %H:%M UTC')}."
            if owner_phone or owner_email:
                try:
                    from ..services.owner_notifications import (
                        notify_owner_with_fallback,
                    )

                    await notify_owner_with_fallback(
                        business_id=business_id,
                        message=owner_message,
                        subject="Missed call alert",
                        dedupe_key=f"stream_missed_{phone}",
                        send_email_copy=bool(owner_email),
                    )
                except Exception:
                    logger.warning(
                        "twilio_stream_owner_sms_failed",
                        exc_info=True,
                        extra={
                            "business_id": business_id,
                            "call_sid": payload.call_sid,
                        },
                    )

            if is_partial_lead and phone:
                customer = customers_repo.get_by_phone(phone, business_id=business_id)
                if not customer or not getattr(customer, "sms_opt_out", False):
                    language_code = get_language_for_business(business_id)
                    business_name = conversation.DEFAULT_BUSINESS_NAME
                    if business_row is not None and getattr(business_row, "name", None):
                        business_name = business_row.name  # type: ignore[assignment]
                    if language_code == "es":
                        body = (
                            f"Sentimos no haber podido completar tu llamada con {business_name}. "
                            "Si aAºn necesitas ayuda, respóndenos con un breve resumen del problema "
                            "y te contactaremos."
                        )
                    else:
                        body = (
                            f"Sorry we couldn't finish your call with {business_name}. "
                            "If you still need help, please reply with a quick summary of the issue "
                            "and we'll follow up."
                        )
                    await sms_service.notify_customer(
                        phone, body, business_id=business_id
                    )
        twilio_state_store.clear_call_session(payload.call_sid)
        sessions.session_store.end(session_obj.id)
        return TwilioStreamResponse(
            status="completed",
            session_id=session_obj.id,
            reply_text=None,
            completed=True,
        )

    conv = conversations_repo.get_by_session(session_obj.id)
    transcript = (payload.transcript or "").strip()
    reply_text: str | None = None
    silent_turn = event == "media" and not transcript
    if silent_turn:
        session_obj.no_input_count = getattr(session_obj, "no_input_count", 0) + 1  # type: ignore[attr-defined]
    else:
        session_obj.no_input_count = 0  # type: ignore[attr-defined]
    if silent_turn and getattr(session_obj, "no_input_count", 0) == 1:
        session_obj.updated_at = datetime.now(UTC)
        sessions.session_store.save(session_obj)
        if language_code == "es":
            prompt = "No alcanzo a escucharte bien. Por favor di tu respuesta o marca 1 para sí o 2 para no."
        else:
            prompt = "I'm having trouble hearing you. Please say your answer or press 1 for yes or 2 for no."
        if conv:
            conversations_repo.append_message(conv.id, role="assistant", text=prompt)
        return TwilioStreamResponse(
            status="ok",
            session_id=session_obj.id,
            reply_text=prompt,
            completed=False,
        )
    if silent_turn and getattr(session_obj, "no_input_count", 0) >= 2:
        queue = metrics.callbacks_by_business.setdefault(business_id, {})
        now = datetime.now(UTC)
        phone = payload.from_number or payload.call_sid or ""
        lead_source = getattr(session_obj, "lead_source", None) or payload.lead_source
        existing = queue.get(phone)
        if existing is None:
            queue[phone] = CallbackItem(
                phone=phone,
                first_seen=now,
                last_seen=now,
                count=1,
                channel="phone",
                lead_source=lead_source,
                reason="NO_INPUT",
            )
        else:
            existing.last_seen = now
            existing.count += 1
            existing.reason = "NO_INPUT"
            if lead_source:
                existing.lead_source = lead_source
            if getattr(existing, "status", "PENDING").upper() != "PENDING":
                existing.status = "PENDING"
                existing.last_result = None
        session_obj.stage = "COMPLETED"
        session_obj.status = "PENDING_FOLLOWUP"
        session_obj.updated_at = datetime.now(UTC)
        sessions.session_store.save(session_obj)
        fallback_reply = (
            "Tengo problemas para escucharte. Te enviaremos un seguimiento para programar tu servicio."
            if language_code == "es"
            else "I'm having trouble hearing you. We'll follow up shortly to finish scheduling."
        )
        if conv:
            conversations_repo.append_message(
                conv.id, role="assistant", text=fallback_reply
            )
        return TwilioStreamResponse(
            status="ok",
            session_id=session_obj.id,
            reply_text=fallback_reply,
            completed=True,
        )
    if event in {"start", "connected"} and not transcript:
        result = await conversation.conversation_manager.handle_input(session_obj, None)
        reply_text = result.reply_text
        if conv:
            conversations_repo.append_message(
                conv.id, role="assistant", text=reply_text
            )
    elif transcript:
        if conv:
            conversations_repo.append_message(conv.id, role="user", text=transcript)
        result = await conversation.conversation_manager.handle_input(
            session_obj, transcript
        )
        reply_text = result.reply_text
        if conv:
            conversations_repo.append_message(
                conv.id, role="assistant", text=reply_text
            )
    return TwilioStreamResponse(
        status="ok",
        session_id=session_obj.id,
        reply_text=reply_text,
        completed=False,
    )


@router.post("/sms", response_class=Response)
async def twilio_sms(
    request: Request,
    From: str = Form(...),
    Body: str = Form(...),
    MessageSid: str | None = Form(default=None),
    business_id_param: str | None = Query(default=None, alias="business_id"),
    lead_source_param: str | None = Query(default=None, alias="lead_source"),
) -> Response:
    """Twilio SMS webhook that bridges inbound texts into the assistant.

    Each unique (business, phone) pair is associated with a conversation so
    that back-and-forth SMS exchanges share context.
    """
    message_sid_ctx.set(MessageSid)
    business_id = business_id_param or DEFAULT_BUSINESS_ID

    logger.info(
        "twilio_sms_webhook",
        extra={
            "business_id": business_id,
            "from": From,
            "message_sid": MessageSid,
        },
    )

    # Track Twilio SMS webhook usage.
    metrics.twilio_sms_requests += 1
    per_tenant = metrics.twilio_by_business.setdefault(
        business_id, BusinessTwilioMetrics()
    )
    per_tenant.sms_requests += 1

    # Resolve language for this business for outgoing SMS copy and error
    # handling so emergency guidance is localized when tenants are Spanish.
    language_code = get_language_for_business(business_id)

    # If the business is suspended, reject early.
    if SQLALCHEMY_AVAILABLE and SessionLocal is not None:
        session_db = SessionLocal()
        try:
            row = session_db.get(BusinessDB, business_id)
        finally:
            session_db.close()
        if row is not None and getattr(row, "status", "ACTIVE") != "ACTIVE":
            return Response(
                content="<Response></Response>",
                media_type="text/xml",
                status_code=status.HTTP_403_FORBIDDEN,
            )

    # Optional signature verification.
    form_params: Dict[str, str] = {"From": From, "Body": Body}
    await _maybe_verify_twilio_signature(request, form_params)

    try:
        normalized_body = Body.strip().lower()
        opt_out_keywords = {"stop", "stopall", "unsubscribe", "end", "quit"}
        opt_in_keywords = {"start", "unstop"}
        confirm_keywords = {"yes", "y", "confirm"}
        decline_keywords = {"no", "n"}
        cancel_intent_keywords = {"cancel", "cancel appointment", "cancel appt"}
        reschedule_keywords = {"reschedule", "change time", "change appointment"}
        per_sms = metrics.sms_by_business.setdefault(business_id, BusinessSmsMetrics())
        pending_action = twilio_state_store.get_pending_action(business_id, From)

        if normalized_body in opt_out_keywords:
            # Mark this customer/phone as opted out of SMS.
            business_name = _get_business_name(business_id)
            customer = customers_repo.get_by_phone(From, business_id=business_id)
            if customer:
                customers_repo.set_sms_opt_out(
                    From, business_id=business_id, opt_out=True
                )
            # Track opt-out event in per-tenant SMS metrics.
            per_sms = metrics.sms_by_business.setdefault(
                business_id, BusinessSmsMetrics()
            )
            per_sms.sms_opt_out_events += 1
            # Simple confirmation message; do not route into the assistant.
            if language_code == "es":
                safe_reply = escape(
                    f"Te has dado de baja de las notificaciones por SMS de {business_name}. "
                    "Responde START para volver a activarlas."
                )
            else:
                safe_reply = escape(
                    f"You have opted out of SMS notifications from {business_name}. "
                    "Reply START to opt back in."
                )
            twiml = f"""
<Response>
  <Message>{safe_reply}</Message>
</Response>
""".strip()
            return Response(content=twiml, media_type="text/xml")

        if normalized_body in opt_in_keywords:
            # Clear any opt-out flag for this customer/phone.
            business_name = _get_business_name(business_id)
            customer = customers_repo.get_by_phone(From, business_id=business_id)
            if customer:
                customers_repo.set_sms_opt_out(
                    From, business_id=business_id, opt_out=False
                )
            # Track opt-in event in per-tenant SMS metrics.
            per_sms = metrics.sms_by_business.setdefault(
                business_id, BusinessSmsMetrics()
            )
            per_sms.sms_opt_in_events += 1
            if language_code == "es":
                safe_reply = escape(
                    f"Has vuelto a activar las notificaciones por SMS de {business_name}."
                )
            else:
                safe_reply = escape(
                    f"You have been opted back in to SMS notifications from {business_name}."
                )
            twiml = f"""
<Response>
  <Message>{safe_reply}</Message>
</Response>
""".strip()
            return Response(content=twiml, media_type="text/xml")

        if pending_action:
            appt = appointments_repo.get(pending_action.appointment_id)
            when_str = _format_appointment_time(appt) if appt else ""
            conv = _ensure_sms_conversation(business_id, From)
            if normalized_body in confirm_keywords:
                if pending_action.action == "cancel" and appt:
                    await appointment_actions.cancel_appointment(
                        appointment_id=appt.id,
                        business_id=business_id,
                        actor="customer_sms",
                        conversation_id=conv.id if conv else None,
                        notify_customer=False,
                    )
                    per_sms.sms_cancellations_via_sms += 1
                    safe_reply = escape(
                        f"Your appointment on {when_str} has been cancelled."
                        if language_code != "es"
                        else f"Tu cita el {when_str} ha sido cancelada."
                    )
                elif pending_action.action == "reschedule" and appt:
                    await appointment_actions.mark_pending_reschedule(
                        appointment_id=appt.id,
                        business_id=business_id,
                        actor="customer_sms",
                        conversation_id=conv.id if conv else None,
                    )
                    per_sms.sms_reschedules_via_sms += 1
                    safe_reply = escape(
                        "Got it. We've marked your appointment for rescheduling."
                        if language_code != "es"
                        else "Entendido. Hemos marcado tu cita para reprogramar."
                    )
                else:
                    safe_reply = escape(
                        "Thanks, noted."
                        if language_code != "es"
                        else "Gracias, anotado."
                    )
                twilio_state_store.clear_pending_action(business_id, From)
                twiml = f"""
<Response>
  <Message>{safe_reply}</Message>
</Response>
""".strip()
                return Response(content=twiml, media_type="text/xml")
            if normalized_body in decline_keywords:
                twilio_state_store.clear_pending_action(business_id, From)
                safe_reply = escape(
                    "Okay, keeping your current appointment."
                    if language_code != "es"
                    else "Listo, mantenemos tu cita actual."
                )
                twiml = f"""
<Response>
  <Message>{safe_reply}</Message>
</Response>
""".strip()
                return Response(content=twiml, media_type="text/xml")
            remind = escape(
                "Please reply YES to confirm or NO to keep your current time."
                if language_code != "es"
                else "Responde SI para confirmar o NO para mantener tu hora actual."
            )
            twiml = f"""
<Response>
  <Message>{remind}</Message>
</Response>
""".strip()
            return Response(content=twiml, media_type="text/xml")

        # Cancellation intent requires explicit confirmation.
        if normalized_body in cancel_intent_keywords:
            appt = _find_next_appointment_for_phone(From, business_id)
            if appt is not None:
                when_str = _format_appointment_time(appt)
                twilio_state_store.set_pending_action(
                    business_id,
                    From,
                    PendingAction(
                        action="cancel",
                        appointment_id=appt.id,
                        business_id=business_id,
                        created_at=datetime.now(UTC),
                    ),
                )
                safe_reply = escape(
                    f"Reply YES to cancel your appointment on {when_str}, or NO to keep it."
                    if language_code != "es"
                    else f"Responde SI para cancelar tu cita el {when_str}, o NO para mantenerla."
                )
            else:
                business_name = _get_business_name(business_id)
                safe_reply = escape(
                    "We could not find an upcoming appointment linked to this number for "
                    f"{business_name}. If this seems wrong, please call or text us with more details."
                    if language_code != "es"
                    else "No pudimos encontrar una pr?xima cita vinculada a este n?mero. Si crees que esto es un error, por favor llama o env?anos un mensaje de texto con m?s detalles."
                )
            twiml = f"""
<Response>
  <Message>{safe_reply}</Message>
</Response>
""".strip()
            return Response(content=twiml.strip(), media_type="text/xml")

        # Reschedule intent requires confirmation to avoid accidental changes.
        if normalized_body in reschedule_keywords:
            appt = _find_next_appointment_for_phone(From, business_id)
            if appt is not None:
                when_str = _format_appointment_time(appt)
                twilio_state_store.set_pending_action(
                    business_id,
                    From,
                    PendingAction(
                        action="reschedule",
                        appointment_id=appt.id,
                        business_id=business_id,
                        created_at=datetime.now(UTC),
                    ),
                )
                safe_reply = escape(
                    f"Reply YES to mark your appointment on {when_str} for rescheduling, or NO to keep it."
                    if language_code != "es"
                    else f"Responde SI para marcar tu cita el {when_str} para reprogramar, o NO para mantenerla."
                )
            else:
                business_name = _get_business_name(business_id)
                safe_reply = escape(
                    "We could not find an upcoming appointment linked to this number for "
                    f"{business_name}. If this seems wrong, please call or text us with more details."
                    if language_code != "es"
                    else "No pudimos encontrar una pr?xima cita vinculada a este n?mero. Si crees que esto es un error, por favor llama o env?anos un mensaje de texto con m?s detalles."
                )
            twiml = f"""
<Response>
  <Message>{safe_reply}</Message>
</Response>
""".strip()
            return Response(content=twiml.strip(), media_type="text/xml")

        # Simple confirmation for upcoming appointments when there is no pending action.
        if normalized_body in confirm_keywords:
            appt = _find_next_appointment_for_phone(From, business_id)
            if appt is not None:
                when_str = _format_appointment_time(appt)
                current_stage = getattr(appt, "job_stage", None)
                new_stage = current_stage or "Booked"
                appointments_repo.update(
                    appt.id, status="CONFIRMED", job_stage=new_stage
                )
                per_sms.sms_confirmations_via_sms += 1
                safe_reply = escape(
                    f"Gracias. Tu pr?xima cita el {when_str} ha sido confirmada."
                    if language_code == "es"
                    else f"Thanks. Your upcoming appointment on {when_str} is confirmed."
                )
            else:
                business_name = _get_business_name(business_id)
                safe_reply = escape(
                    "We could not find an upcoming appointment linked to this number for "
                    f"{business_name}. If this seems wrong, please call or text us with more details."
                    if language_code != "es"
                    else "No pudimos encontrar una pr?xima cita vinculada a este n?mero. Si crees que esto es un error, por favor llama o env?anos un mensaje de texto con m?s detalles."
                )
            twiml = f"""
<Response>
  <Message>{safe_reply}</Message>
</Response>
""".strip()
            return Response(content=twiml.strip(), media_type="text/xml")
        from_phone = From or ""
        link = twilio_state_store.get_sms_conversation(business_id, from_phone)
        if link:
            conv = conversations_repo.get(link.conversation_id)
            conv_id = link.conversation_id if conv else None
        else:
            customer = customers_repo.get_by_phone(From, business_id=business_id)
            conv = conversations_repo.create(
                channel="sms",
                customer_id=customer.id if customer else None,
                business_id=business_id,
            )
            conv_id = conv.id
            twilio_state_store.set_sms_conversation(
                business_id,
                from_phone,
                conv_id,
            )

        # Log user message.
        if conv_id:
            conversations_repo.append_message(conv_id, role="user", text=Body)

        # Reuse the conversation manager by synthesizing a CallSession.
        from ..services.sessions import CallSession  # local import to avoid cycles

        session = CallSession(
            id=conv_id or "",
            caller_phone=From,
            business_id=business_id,
            channel="sms",
            lead_source=lead_source_param,
        )
        result = await conversation.conversation_manager.handle_input(session, Body)

        if conv_id:
            conversations_repo.append_message(
                conv_id, role="assistant", text=result.reply_text
            )

        safe_reply = escape(result.reply_text)
        twiml = f"""
<Response>
  <Message>{safe_reply}</Message>
</Response>
""".strip()
        return Response(content=twiml, media_type="text/xml")
    except Exception:  # pragma: no cover - defensive
        logger.exception(
            "twilio_sms_unhandled_error",
            extra={
                "business_id": business_id,
                "from": From,
            },
        )
        if language_code == "es":
            message = (
                "Lo siento, hubo un problema al manejar tu mensaje. "
                "Por favor inténtalo de nuevo en unos minutos. "
                "Si se trata de una emergencia que ponga en riesgo la vida, "
                "llama al 911 o a tu número de emergencias local en lugar de enviar un mensaje de texto."
            )
        else:
            message = (
                "Sorry, something went wrong while handling your message. "
                "Please try again in a few minutes. "
                "If this is a life-threatening emergency, call 911 or your local emergency number instead of texting."
            )
        safe_reply = escape(message)
        # Track Twilio SMS errors globally and per tenant.
        metrics.twilio_sms_errors += 1
        per_tenant = metrics.twilio_by_business.setdefault(
            business_id, BusinessTwilioMetrics()
        )
        per_tenant.sms_errors += 1
        twiml = f"""
<Response>
  <Message>{safe_reply}</Message>
</Response>
""".strip()
        return Response(content=twiml, media_type="text/xml")


@router.post("/status-callback")
async def twilio_status_callback(request: Request) -> dict:
    """Capture Twilio delivery status callbacks for observability."""
    form_params = await request.form()
    await _maybe_verify_twilio_signature(request, form_params)
    event_id = request.headers.get("X-Twilio-EventId") or request.headers.get(
        "Twilio-Event-Id"
    )
    message_sid = form_params.get("MessageSid")
    message_sid_ctx.set(message_sid)
    message_status = form_params.get("MessageStatus")
    to = form_params.get("To")
    from_ = form_params.get("From")
    error_code = form_params.get("ErrorCode")
    # Deduplicate by EventId + MessageSid.
    if event_id:
        cache_key = f"status:{message_sid}"
        if twilio_state_store.get_call_session(cache_key):
            logger.info(
                "twilio_status_duplicate",
                extra={"event_id": event_id, "sid": message_sid},
            )
            return {"received": True, "status": message_status, "sid": message_sid}
        twilio_state_store.set_call_session(
            cache_key, cache_key, state=message_status, event_id=event_id
        )
    logger.info(
        "twilio_status_callback",
        extra={
            "message_sid": message_sid,
            "message_status": message_status,
            "to": to,
            "from": from_,
            "error_code": error_code,
        },
    )
    return {"received": True, "status": message_status, "sid": message_sid}


@router.api_route("/fallback", methods=["GET", "POST"])
async def twilio_fallback(request: Request) -> Response:
    """Fallback handler for voice/SMS if the primary webhook fails."""
    await _maybe_verify_twilio_signature(request, {})
    twiml = """
<Response>
  <Say voice="Polly.Joanna">We are unable to take your call at the moment. We will call you back shortly.</Say>
</Response>
""".strip()
    return Response(content=twiml, media_type="text/xml")


@router.post("/voicemail", response_class=Response)
async def twilio_voicemail(
    request: Request,
    CallSid: str = Form(...),
    From: str | None = Form(default=None),
    RecordingUrl: str | None = Form(default=None),
    RecordingDuration: str | None = Form(default=None),
    business_id_param: str | None = Query(default=None, alias="business_id"),
) -> Response:
    """Capture voicemail recordings when the assistant is unavailable."""
    call_sid_ctx.set(CallSid)
    business_id = business_id_param or DEFAULT_BUSINESS_ID
    # Optional signature verification.
    form_params: Dict[str, str] = {"CallSid": CallSid}
    if From is not None:
        form_params["From"] = From
    if RecordingUrl is not None:
        form_params["RecordingUrl"] = RecordingUrl
    await _maybe_verify_twilio_signature(request, form_params)

    phone = From or ""
    now = datetime.now(UTC)
    queue = metrics.callbacks_by_business.setdefault(business_id, {})
    existing = queue.get(phone)
    if existing is None:
        existing = CallbackItem(
            phone=phone,
            first_seen=now,
            last_seen=now,
            count=1,
            channel="phone",
            reason="VOICEMAIL",
        )
    else:
        existing.last_seen = now
        existing.count += 1
        existing.reason = "VOICEMAIL"
    if RecordingUrl:
        existing.voicemail_url = RecordingUrl
    existing.status = "PENDING"
    queue[phone] = existing

    # Best-effort owner notification with a callback link.
    owner_message = f"New voicemail from {phone or 'unknown'} at {now.strftime('%Y-%m-%d %H:%M UTC')}."
    business_row = None
    if SQLALCHEMY_AVAILABLE and SessionLocal is not None:
        session_db = SessionLocal()
        try:
            business_row = session_db.get(BusinessDB, business_id)
        finally:
            session_db.close()
    owner_phone = getattr(business_row, "owner_phone", None) if business_row else None
    owner_email = getattr(business_row, "owner_email", None) if business_row else None
    callback_hint = " Check dashboard callback queue to return the call."
    owner_message = owner_message + callback_hint
    if owner_phone:
        try:
            from ..services.owner_notifications import notify_owner_with_fallback

            await notify_owner_with_fallback(
                business_id=business_id,
                message=owner_message,
                subject="New voicemail",
                dedupe_key=f"voicemail_{CallSid or owner_message}",
                send_email_copy=bool(owner_email),
            )
        except Exception:
            logger.warning(
                "voicemail_owner_sms_failed",
                exc_info=True,
                extra={"business_id": business_id, "call_sid": CallSid},
            )

    safe_reply = escape(
        "Thank you. We received your message and will call you back shortly."
    )
    twiml = f"""
<Response>
  <Say voice="alice">{safe_reply}</Say>
</Response>
""".strip()
    return Response(content=twiml, media_type="text/xml")
