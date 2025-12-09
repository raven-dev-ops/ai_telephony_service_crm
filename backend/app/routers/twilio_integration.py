from __future__ import annotations

import base64
import hashlib
import hmac
from datetime import UTC, datetime, timedelta
from html import escape
import logging

from fastapi import APIRouter, Form, HTTPException, Query, Request, Response, status
from typing import Dict, TYPE_CHECKING

from ..config import get_settings
from ..db import SQLALCHEMY_AVAILABLE, SessionLocal
from ..db_models import BusinessDB
from ..deps import DEFAULT_BUSINESS_ID
from ..metrics import BusinessSmsMetrics, BusinessTwilioMetrics, metrics
from ..repositories import conversations_repo, customers_repo, appointments_repo
from ..services import conversation, sessions
from ..services.calendar import calendar_service
from ..services.sms import sms_service
from ..business_config import get_calendar_id_for_business, get_language_for_business
from ..services.twilio_state import twilio_state_store
from . import owner as owner_routes

if TYPE_CHECKING:
    from ..models import Appointment


logger = logging.getLogger(__name__)
router = APIRouter()


async def _maybe_verify_twilio_signature(
    request: Request, form_params: Dict[str, str]
) -> None:
    """Optionally verify the Twilio signature on inbound webhooks.

    If VERIFY_TWILIO_SIGNATURES=true and TWILIO_AUTH_TOKEN is set in the
    environment, this validates the X-Twilio-Signature header using the
    standard Twilio algorithm (URL + sorted query/body params).
    """
    settings = get_settings()
    sms_cfg = settings.sms
    if not getattr(sms_cfg, "verify_twilio_signatures", False):
        return
    if not sms_cfg.twilio_auth_token:
        return

    twilio_sig = request.headers.get("X-Twilio-Signature")
    if not twilio_sig:
        logger.warning("twilio_signature_missing", extra={"path": str(request.url)})
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
        logger.warning("twilio_signature_invalid", extra={"path": str(request.url)})
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Twilio signature",
        )


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


@router.post("/voice", response_class=Response)
async def twilio_voice(
    request: Request,
    CallSid: str = Form(...),
    From: str | None = Form(default=None),
    CallStatus: str | None = Form(default=None),
    SpeechResult: str | None = Form(default=None),
    business_id_param: str | None = Query(default=None, alias="business_id"),
    lead_source_param: str | None = Query(default=None, alias="lead_source"),
) -> Response:
    """Twilio Voice webhook that bridges to the conversation manager.

    This endpoint expects Twilio to be configured for speech input in a <Gather>
    and will respond with TwiML that speaks the assistant reply and gathers
    further speech input.
    """
    # Resolve tenant for this webhook. For multi-tenant scenarios, configure
    # the Twilio webhook URL with a `?business_id=...` query parameter per
    # tenant; otherwise we fall back to the default single-tenant ID.
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

    # Track Twilio voice webhook usage.
    metrics.twilio_voice_requests += 1
    per_tenant = metrics.twilio_by_business.setdefault(
        business_id, BusinessTwilioMetrics()
    )
    per_tenant.voice_requests += 1

    # Resolve language once for this call so we can adjust TwiML voices and
    # error messages when tenants are configured for Spanish.
    language_code = get_language_for_business(business_id)
    say_language_attr = _twilio_say_language_attr(language_code)

    # If the business is suspended, reject early.
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

    # Optional signature verification.
    form_params: Dict[str, str] = {"CallSid": CallSid}
    if From is not None:
        form_params["From"] = From
    if CallStatus is not None:
        form_params["CallStatus"] = CallStatus
    if SpeechResult is not None:
        form_params["SpeechResult"] = SpeechResult
    await _maybe_verify_twilio_signature(request, form_params)

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
        default_missed = {"canceled", "busy", "failed", "no-answer"}
        missed_statuses = set(default_missed)
        if business_row is not None:
            raw = getattr(business_row, "twilio_missed_statuses", None)
            if raw:
                parts = [p.strip().lower() for p in str(raw).split(",") if p.strip()]
                if parts:
                    missed_statuses = set(parts)
        if CallStatus and CallStatus.lower() in ended_statuses:
            link = twilio_state_store.clear_call_session(CallSid)
            session = None
            is_partial_lead = False
            if link and link.session_id:
                session = sessions.session_store.get(link.session_id)
                # Detect calls that dropped before the assistant finished intake.
                if session is not None:
                    status_val = (getattr(session, "status", "") or "").upper()
                    if status_val not in {"SCHEDULED", "PENDING_FOLLOWUP", "COMPLETED"}:
                        is_partial_lead = True
                sessions.session_store.end(link.session_id)
            status_lower = CallStatus.lower()
            if status_lower in missed_statuses or is_partial_lead:
                from ..metrics import CallbackItem, metrics as _metrics  # local import
                from ..services.sms import sms_service  # local import to avoid cycles
                from ..business_config import (  # local import
                    get_language_for_business as _get_language_for_business,
                )

                phone = From or ""
                if phone:
                    now = datetime.now(UTC)
                    queue = _metrics.callbacks_by_business.setdefault(business_id, {})
                    existing = queue.get(phone)
                    lead_source = getattr(session, "lead_source", None)
                    if existing is None:
                        reason = "PARTIAL_INTAKE" if is_partial_lead else "MISSED_CALL"
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
                        # If this latest event is a partial-intake drop, upgrade
                        # the reason so the owner can see it clearly.
                        if is_partial_lead:
                            existing.reason = "PARTIAL_INTAKE"
                        # A new missed or partial call should re-open the callback
                        # if it was previously resolved.
                        if getattr(existing, "status", "PENDING").upper() != "PENDING":
                            existing.status = "PENDING"
                            existing.last_result = None

                    # For partial leads where the assistant answered but the caller
                    # dropped before intake completed, send a gentle SMS asking
                    # for a quick summary so the team can follow up.
                    if is_partial_lead:
                        # Best-effort check for SMS opt-out.
                        customer = None
                        if phone:
                            customer = customers_repo.get_by_phone(
                                phone, business_id=business_id
                            )
                        if not customer or not getattr(customer, "sms_opt_out", False):
                            language_code = _get_language_for_business(business_id)
                            business_name = conversation.DEFAULT_BUSINESS_NAME
                            if business_row is not None and getattr(
                                business_row, "name", None
                            ):
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
            session = sessions.session_store.get(link.session_id)
            session_id = link.session_id
        else:
            session = sessions.session_store.create(
                caller_phone=From,
                business_id=business_id,
                lead_source=lead_source_param,
            )
            session_id = session.id
            twilio_state_store.set_call_session(CallSid, session_id)
            # Create a conversation record for logging.
            customer = (
                customers_repo.get_by_phone(From or "", business_id=business_id)
                if From
                else None
            )
            conversations_repo.create(
                channel="phone",
                customer_id=customer.id if customer else None,
                session_id=session_id,
                business_id=business_id,
            )

        # Bridge Twilio's speech result into the conversation manager.
        text = SpeechResult or ""
        if text:
            conv = conversations_repo.get_by_session(session_id)
            if conv:
                conversations_repo.append_message(conv.id, role="user", text=text)

        result = await conversation.conversation_manager.handle_input(
            session, text or None
        )

        conv = conversations_repo.get_by_session(session_id)
        if conv:
            conversations_repo.append_message(
                conv.id, role="assistant", text=result.reply_text
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
        twiml = f"""
<Response>
  <Say voice="alice"{say_language_attr}>{safe_reply}</Say>
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
            if owner_phone and From and From != owner_phone:
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
                    await sms_service.notify_owner(
                        summary_text, business_id=business_id
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


@router.post("/sms", response_class=Response)
async def twilio_sms(
    request: Request,
    From: str = Form(...),
    Body: str = Form(...),
    business_id_param: str | None = Query(default=None, alias="business_id"),
    lead_source_param: str | None = Query(default=None, alias="lead_source"),
) -> Response:
    """Twilio SMS webhook that bridges inbound texts into the assistant.

    Each unique (business, phone) pair is associated with a conversation so
    that back-and-forth SMS exchanges share context.
    """
    business_id = business_id_param or DEFAULT_BUSINESS_ID

    logger.info(
        "twilio_sms_webhook",
        extra={
            "business_id": business_id,
            "from": From,
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
        opt_out_keywords = {"stop", "stopall", "unsubscribe", "cancel", "end", "quit"}
        opt_in_keywords = {"start", "unstop"}
        confirm_keywords = {"yes", "y", "confirm"}
        cancel_keywords = {"no", "n", "cancel"}
        reschedule_keywords = {"reschedule", "change time", "change appointment"}

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

        # Simple confirmation/cancellation for upcoming appointments.
        if normalized_body in confirm_keywords or normalized_body in cancel_keywords:
            appt = _find_next_appointment_for_phone(From, business_id)
            if appt is not None:
                when_str = _format_appointment_time(appt)
                if normalized_body in confirm_keywords:
                    # Mark the appointment as confirmed without changing the calendar.
                    current_stage = getattr(appt, "job_stage", None)
                    new_stage = current_stage or "Booked"
                    appointments_repo.update(
                        appt.id, status="CONFIRMED", job_stage=new_stage
                    )
                    per_sms = metrics.sms_by_business.setdefault(
                        business_id, BusinessSmsMetrics()
                    )
                    per_sms.sms_confirmations_via_sms += 1
                    if language_code == "es":
                        safe_reply = escape(
                            f"Gracias. Tu próxima cita el {when_str} ha sido confirmada."
                        )
                    else:
                        safe_reply = escape(
                            f"Thanks. Your upcoming appointment on {when_str} is confirmed."
                        )
                else:
                    # Cancel the appointment and attempt to remove the calendar event.
                    if getattr(appt, "calendar_event_id", None):
                        calendar_id = get_calendar_id_for_business(business_id)
                        await calendar_service.delete_event(
                            event_id=appt.calendar_event_id,
                            calendar_id=calendar_id,
                        )
                    current_stage = getattr(appt, "job_stage", None)
                    new_stage = current_stage or "Cancelled"
                    appointments_repo.update(
                        appt.id, status="CANCELLED", job_stage=new_stage
                    )
                    per_sms = metrics.sms_by_business.setdefault(
                        business_id, BusinessSmsMetrics()
                    )
                    per_sms.sms_cancellations_via_sms += 1
                    if language_code == "es":
                        safe_reply = escape(
                            f"Tu próxima cita el {when_str} ha sido cancelada. "
                            "Si necesitas reprogramarla, por favor llama o envía un mensaje de texto."
                        )
                    else:
                        safe_reply = escape(
                            f"Your upcoming appointment on {when_str} has been cancelled. "
                            "If you need to reschedule, please call or text."
                        )
                twiml = f"""
<Response>
  <Message>{safe_reply}</Message>
</Response>
""".strip()
                return Response(content=twiml, media_type="text/xml")
            # No upcoming appointment found for this number; respond clearly instead
            # of falling back into the generic assistant flow.
            business_name = _get_business_name(business_id)
            if language_code == "es":
                safe_reply = escape(
                    "No pudimos encontrar una próxima cita vinculada a este número para "
                    f"{business_name}. Si crees que esto es un error, por favor llama o envíanos un mensaje de texto con más detalles."
                )
            else:
                safe_reply = escape(
                    "We could not find an upcoming appointment linked to this number for "
                    f"{business_name}. If this seems wrong, please call or text us with more details."
                )
            twiml = f"""
<Response>
  <Message>{safe_reply}</Message>
</Response>
""".strip()
            return Response(content=twiml, media_type="text/xml")

        # Simple reschedule request for upcoming appointments.
        if normalized_body in reschedule_keywords:
            appt = _find_next_appointment_for_phone(From, business_id)
            if appt is not None:
                when_str = _format_appointment_time(appt)
                # Mark appointment as pending reschedule but do not change calendar automatically.
                current_stage = getattr(appt, "job_stage", None)
                new_stage = current_stage or "Pending Reschedule"
                appointments_repo.update(
                    appt.id, status="PENDING_RESCHEDULE", job_stage=new_stage
                )
                per_sms = metrics.sms_by_business.setdefault(
                    business_id, BusinessSmsMetrics()
                )
                per_sms.sms_reschedules_via_sms += 1
                if language_code == "es":
                    safe_reply = escape(
                        f"Entendido. Tu próxima cita el {when_str} ha sido marcada para reprogramarse. "
                        "Nos pondremos en contacto contigo para escoger una nueva hora."
                    )
                else:
                    safe_reply = escape(
                        f"Got it. Your upcoming appointment on {when_str} has been marked for rescheduling. "
                        "We will contact you to pick a new time."
                    )
            else:
                business_name = _get_business_name(business_id)
                if language_code == "es":
                    safe_reply = escape(
                        "No pudimos encontrar una próxima cita vinculada a este número para "
                        f"{business_name}. Si crees que esto es un error, por favor llama o envíanos un mensaje de texto con más detalles."
                    )
                else:
                    safe_reply = escape(
                        "We could not find an upcoming appointment linked to this number for "
                        f"{business_name}. If this seems wrong, please call or text us with more details."
                    )
            twiml = f"""
<Response>
  <Message>{safe_reply}</Message>
</Response>
""".strip()
            return Response(content=twiml, media_type="text/xml")

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
    message_sid = form_params.get("MessageSid")
    message_status = form_params.get("MessageStatus")
    to = form_params.get("To")
    from_ = form_params.get("From")
    error_code = form_params.get("ErrorCode")
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
    twiml = """
<Response>
  <Say voice="Polly.Joanna">We are unable to take your call at the moment. We will call you back shortly.</Say>
</Response>
""".strip()
    return Response(content=twiml, media_type="text/xml")
