from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import logging
import time

from .calendar import TimeSlot, calendar_service
from .stt_tts import speech_service  # noqa: F401  (re-exported for voice router)
from .sessions import CallSession
from .sms import sms_service
from .nlu import (
    parse_address,
    parse_name,
    classify_intent,
    classify_intent_with_metadata,
)
from . import subscription as subscription_service
from ..config import get_settings
from ..db import SQLALCHEMY_AVAILABLE, SessionLocal
from ..db_models import BusinessDB
from ..metrics import metrics
from ..repositories import appointments_repo, customers_repo, conversations_repo
from ..business_config import (
    get_calendar_id_for_business,
    get_language_for_business,
    get_vertical_for_business,
)


logger = logging.getLogger(__name__)


EMERGENCY_KEYWORDS = [
    "burst",
    "flood",
    "flooding",
    "no water",
    "no hot water",
    "sewage",
    "sewer",
    "backing up",
    "backup",
    "gas leak",
]


def _intent_threshold_for_business(business_id: str | None) -> float:
    settings = get_settings()
    default_threshold = getattr(settings.nlu, "intent_confidence_threshold", 0.35)
    if not business_id or not (SQLALCHEMY_AVAILABLE and SessionLocal is not None):
        return float(default_threshold)
    session = SessionLocal()
    try:
        row = session.get(BusinessDB, business_id)
    finally:
        session.close()
    raw = getattr(row, "intent_threshold", None) if row is not None else None
    try:
        val = float(raw) if raw is not None else float(default_threshold)
        return val / 100.0 if val > 1 else val
    except Exception:
        return float(default_threshold)

DEFAULT_BUSINESS_NAME = "Bristol Plumbing"


def _get_emergency_keywords_for_business(business_id: str | None) -> list[str]:
    """Return per-tenant emergency keywords, falling back to defaults."""
    if business_id and SQLALCHEMY_AVAILABLE and SessionLocal is not None:
        session_db = SessionLocal()
        try:
            row = session_db.get(BusinessDB, business_id)
        finally:
            session_db.close()
        if row is not None and getattr(row, "emergency_keywords", None):
            raw = row.emergency_keywords or ""
            keywords = [k.strip().lower() for k in raw.split(",") if k.strip()]
            if keywords:
                return keywords
    return EMERGENCY_KEYWORDS


def _get_business_name(business_id: str | None) -> str:
    """Return the business display name for voice/SMS copy."""
    if business_id and SQLALCHEMY_AVAILABLE and SessionLocal is not None:
        session_db = SessionLocal()
        try:
            row = session_db.get(BusinessDB, business_id)
        finally:
            session_db.close()
        if row is not None and getattr(row, "name", None):
            return row.name  # type: ignore[return-value]
    return DEFAULT_BUSINESS_NAME


def _infer_service_type(problem_summary: str | None) -> str | None:
    """Best-effort classification of service type from the problem summary."""
    if not problem_summary:
        return None
    text = problem_summary.lower()

    # Tankless water heaters (signature specialty).
    if "tankless" in text or "navien" in text or "rinnai" in text or "noritz" in text:
        return "tankless_water_heater"

    # Traditional water heaters.
    if "water heater" in text:
        return "water_heater"

    # Drains and sewer issues.
    if "sewer" in text or "sewage" in text or "drain" in text or "main line" in text:
        return "drain_or_sewer"

    # Gas line work.
    if "gas line" in text or "gas leak" in text or "gas" in text:
        return "gas_line"

    # Sump pumps.
    if "sump pump" in text or "sump" in text:
        return "sump_pump"

    # Fixtures and general repairs.
    if (
        "faucet" in text
        or "sink" in text
        or "toilet" in text
        or "disposal" in text
        or "garbage disposal" in text
        or "leak" in text
    ):
        return "fixture_or_leak_repair"

    return "general_plumbing"


SERVICE_TYPE_DURATIONS_MINUTES: dict[str, int] = {
    "tankless_water_heater": 240,
    "water_heater": 120,
    "drain_or_sewer": 90,
    "sump_pump": 90,
    "fixture_or_leak_repair": 60,
    "gas_line": 120,
    "general_plumbing": 60,
}


def _get_service_duration_overrides(business_id: str | None) -> dict[str, int]:
    """Return per-tenant overrides for service durations, if configured."""
    if not business_id or not (SQLALCHEMY_AVAILABLE and SessionLocal is not None):
        return {}
    session_db = SessionLocal()
    try:
        row = session_db.get(BusinessDB, business_id)
    finally:
        session_db.close()
    raw = getattr(row, "service_duration_config", None) if row is not None else None
    if not raw:
        return {}
    overrides: dict[str, int] = {}
    for part in str(raw).split(","):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key or not value:
            continue
        try:
            minutes = int(value)
        except ValueError:
            continue
        if minutes <= 0:
            continue
        overrides[key] = minutes
    return overrides


def _infer_duration_minutes(
    problem_summary: str | None,
    is_emergency: bool,
    business_id: str | None,
) -> int:
    """Return a default duration for scheduling based on service type."""
    service_type = _infer_service_type(problem_summary) or "general_plumbing"
    overrides = _get_service_duration_overrides(business_id)
    base = overrides.get(
        service_type, SERVICE_TYPE_DURATIONS_MINUTES.get(service_type, 60)
    )
    # Ensure emergencies are not scheduled for unrealistically short windows.
    if is_emergency and base < 60:
        return 60
    return base


def _infer_quote_for_service_type(
    service_type: str | None,
    is_emergency: bool,
) -> tuple[float | None, float | None]:
    """Return a simple (min, max) quote range for the service type.

    This is intentionally rough and uses fixed heuristics so owners can
    configure their own pricing later.
    """
    if service_type is None:
        return None, None
    base_ranges: dict[str, tuple[float, float]] = {
        "tankless_water_heater": (2500.0, 4500.0),
        "water_heater": (1500.0, 2800.0),
        "drain_or_sewer": (350.0, 900.0),
        "sump_pump": (600.0, 1500.0),
        "fixture_or_leak_repair": (150.0, 450.0),
        "gas_line": (800.0, 2500.0),
        "general_plumbing": (200.0, 600.0),
    }
    low, high = base_ranges.get(service_type, (0.0, 0.0))
    if low == 0.0 and high == 0.0:
        return None, None
    if is_emergency:
        low *= 1.15
        high *= 1.25
    return round(low, 2), round(high, 2)


def _normalize_lead_source(channel: str, campaign: str | None = None) -> str:
    """Return a human-friendly lead_source label for analytics.

    - Normalizes core channels (phone/web/sms) to title-cased labels.
    - Optionally appends a campaign tag, e.g. "Phone – Google Ads – KS plumbing".
    """
    base_map = {
        "phone": "Phone",
        "sms": "SMS",
        "web": "Web",
    }
    key = (channel or "phone").lower()
    label = base_map.get(key, (channel or "Unknown").title())
    if campaign:
        campaign_clean = campaign.strip()
        if campaign_clean:
            return f"{label} ? {campaign_clean}"
    return label


@dataclass
class ConversationResult:
    reply_text: str
    new_state: dict


def _session_state(session: CallSession, pending_slot: TimeSlot | None = None) -> dict:
    state: dict = {
        "session_id": session.id,
        "stage": session.stage,
        "status": session.status,
        "caller_phone": session.caller_phone,
        "caller_name": session.caller_name,
        "address": session.address,
        "problem_summary": session.problem_summary,
        "requested_time": session.requested_time,
        "is_emergency": session.is_emergency,
    }
    if pending_slot:
        state["proposed_slot"] = {
            "start": pending_slot.start.isoformat(),
            "end": pending_slot.end.isoformat(),
        }
    return state


class ConversationManager:
    """Simple state-machine-based conversation manager for Phase 1."""

    async def handle_input(
        self, session: CallSession, text: str | None
    ) -> ConversationResult:
        start = time.perf_counter()
        success = False
        try:
            result = await self._handle_input_impl(session, text)
            success = True
            return result
        finally:
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            metrics.record_conversation_latency(elapsed_ms)
            business_id = (
                getattr(session, "business_id", "default_business")
                or "default_business"
            )
            if success:
                metrics.conversation_messages += 1
            else:
                metrics.conversation_failures += 1
            if elapsed_ms > 1800:
                logger.warning(
                    "conversation_latency_slow",
                    extra={
                        "business_id": business_id,
                        "session_id": session.id,
                        "latency_ms": round(elapsed_ms, 2),
                    },
                )

    async def _handle_input_impl(
        self, session: CallSession, text: str | None
    ) -> ConversationResult:
        session.updated_at = datetime.now(UTC)
        normalized = (text or "").strip()
        lower = normalized.lower()
        business_id = (
            getattr(session, "business_id", "default_business") or "default_business"
        )
        intent_meta = None
        if normalized:
            try:
                intent_meta = await classify_intent_with_metadata(
                    normalized, business_id
                )
                session.intent = intent_meta["intent"]
                session.intent_confidence = intent_meta.get("confidence")
            except Exception:
                session.intent = session.intent or None
                session.intent_confidence = getattr(session, "intent_confidence", None)
        threshold = _intent_threshold_for_business(business_id)
        if (
            getattr(session, "intent_confidence", None) is not None
            and session.intent_confidence < threshold
        ):
            session.intent = None
        if session.intent == "emergency":
            session.is_emergency = True
        conv = conversations_repo.get_by_session(session.id)
        if conv:
            conversations_repo.set_intent(
                conv.id, session.intent, getattr(session, "intent_confidence", None)
            )

        # Resolve language and business context up-front.
        language_code = get_language_for_business(business_id)
        business_name = _get_business_name(business_id)
        vertical = get_vertical_for_business(business_id).lower()

        # Best-effort detection of returning customers by phone number.
        is_returning_customer = False
        returning_customer_name: str | None = None
        returning_customer_address: str | None = None
        if session.caller_phone:
            customer = customers_repo.get_by_phone(
                session.caller_phone, business_id=business_id
            )
            if customer:
                is_returning_customer = True
                returning_customer_name = customer.name
                returning_customer_address = getattr(customer, "address", None)

        # Emergency detection (best-effort, per-tenant keywords).
        emergency_keywords = _get_emergency_keywords_for_business(business_id)
        if lower and any(keyword in lower for keyword in emergency_keywords):
            session.is_emergency = True

        # Initial greeting.
        if session.stage == "GREETING":
            logger.info(
                "conversation_start",
                extra={
                    "session_id": session.id,
                    "business_id": business_id,
                    "caller_phone": session.caller_phone,
                    "channel": "voice_or_sms",
                },
            )

            if not normalized:
                if language_code == "es":
                    if is_returning_customer:
                        name_part = (
                            f" {returning_customer_name}"
                            if returning_customer_name
                            else ""
                        )
                        reply = (
                            f"Hola{name_part}, te habla el asistente automatizado de {business_name}. "
                            "Parece que ya hemos trabajado contigo antes. "
                            "Para empezar, ¿cuál es tu nombre para esta visita? "
                        )
                    else:
                        reply = (
                            f"Hola, te habla el asistente automatizado de {business_name}. "
                            "Puedo ayudarte a programar una visita de plomería. "
                            "Para empezar, ¿cuál es tu nombre? "
                        )
                else:
                    if is_returning_customer:
                        name_part = (
                            f" {returning_customer_name}"
                            if returning_customer_name
                            else ""
                        )
                        reply = (
                            f"Hi{name_part}, this is the automated assistant for {business_name}. "
                            "It looks like we've worked with you before. "
                            "To confirm our records, what name should I put on this visit? "
                        )
                    else:
                        reply = (
                            f"Hi, this is the automated assistant for {business_name}. "
                            f"I can help you schedule a {vertical} visit. "
                            "To get started, what is your name? "
                        )
                session.stage = "ASK_NAME"
                return ConversationResult(
                    reply_text=reply, new_state=_session_state(session)
                )

            # If the caller says something on the greeting turn, treat it as a name.
            parsed_name = parse_name(normalized) or normalized
            session.caller_name = parsed_name
            session.stage = "ASK_ADDRESS"
            if language_code == "es":
                reply = f"Gracias, {parsed_name}. ¿Cuál es la dirección del servicio para esta visita?"
            else:
                reply = f"Thanks, {parsed_name}. What is the service address for this visit?"
            return ConversationResult(
                reply_text=reply, new_state=_session_state(session)
            )

        # ASK_NAME: capture caller name.
        if session.stage == "ASK_NAME":
            if not normalized:
                if language_code == "es":
                    reply = "No alcancé a escuchar tu nombre. ¿Cómo te llamas?"
                else:
                    reply = "Sorry, I didn't catch your name. What is your name?"
                return ConversationResult(
                    reply_text=reply, new_state=_session_state(session)
                )

            parsed_name = parse_name(normalized) or normalized
            session.caller_name = parsed_name
            session.stage = "ASK_ADDRESS"
            if language_code == "es":
                reply = f"Gracias, {parsed_name}. ¿Cuál es la dirección del servicio para esta visita?"
            else:
                # Test suite looks for this phrase.
                reply = "Okay, what is the service address for this visit?"
            return ConversationResult(
                reply_text=reply, new_state=_session_state(session)
            )

        # ASK_ADDRESS: collect or confirm address.
        if session.stage == "ASK_ADDRESS":
            if not normalized and returning_customer_address:
                # Offer to reuse known address.
                session.address = returning_customer_address
                session.stage = "CONFIRM_ADDRESS"
                if language_code == "es":
                    reply = (
                        f"Tengo tu dirección como {returning_customer_address}. "
                        "¿Sigue siendo correcta para esta visita?"
                    )
                else:
                    reply = (
                        f"I have your address as {returning_customer_address}. "
                        "Does that still work for this visit?"
                    )
                return ConversationResult(
                    reply_text=reply, new_state=_session_state(session)
                )

            if not normalized:
                if language_code == "es":
                    reply = (
                        "¿Cuál es la dirección completa del servicio para esta visita?"
                    )
                else:
                    reply = "What is the full service address for this visit?"
                return ConversationResult(
                    reply_text=reply, new_state=_session_state(session)
                )

            # Treat any non-empty answer as an address.
            parsed_address = parse_address(normalized) or normalized
            session.address = parsed_address
            session.stage = "ASK_PROBLEM"
            if language_code == "es":
                reply = (
                    "Perfecto. Describe brevemente qué está pasando con la plomería."
                )
            else:
                # Test suite looks for this phrase.
                reply = (
                    f"Got it. Briefly describe what's going on with your {vertical}."
                )
            return ConversationResult(
                reply_text=reply, new_state=_session_state(session)
            )

        # CONFIRM_ADDRESS: confirm or replace stored address.
        if session.stage == "CONFIRM_ADDRESS":
            if "no" in lower:
                session.address = None
                session.stage = "ASK_ADDRESS"
                if language_code == "es":
                    reply = "De acuerdo, ¿cuál es la dirección del servicio para esta visita?"
                else:
                    reply = "Okay, what is the service address for this visit?"
                return ConversationResult(
                    reply_text=reply, new_state=_session_state(session)
                )

            # Any non-negative answer confirms the stored address.
            session.stage = "ASK_PROBLEM"
            if language_code == "es":
                reply = (
                    "Perfecto. Describe brevemente qué está pasando con la plomería."
                )
            else:
                reply = (
                    f"Got it. Briefly describe what's going on with your {vertical}."
                )
            return ConversationResult(
                reply_text=reply, new_state=_session_state(session)
            )

        # ASK_PROBLEM: capture problem summary and move to scheduling.
        if session.stage == "ASK_PROBLEM":
            if not normalized:
                if language_code == "es":
                    reply = "Por favor describe el problema de plomería para saber cómo prepararnos."
                else:
                    reply = f"Please describe the {vertical} issue so we know what to prepare for."
                return ConversationResult(
                    reply_text=reply, new_state=_session_state(session)
                )

            session.problem_summary = normalized
            session.stage = "ASK_SCHEDULE"
            if session.is_emergency:
                if language_code == "es":
                    reply_prefix = (
                        "Gracias, eso suena urgente. Marcaré esto como un trabajo de emergencia. "
                        "No puedo contactar a los servicios de emergencia por ti, así que si se trata de una "
                        "situación que pone en riesgo la vida, cuelga y llama al 911 o a tu número de emergencias local. "
                    )
                else:
                    reply_prefix = (
                        "Thanks, that sounds urgent. I'll flag this as an emergency job. "
                        "I cannot contact emergency services for you, so if this is life-threatening, "
                        "hang up and call 911 or your local emergency number. "
                    )
            else:
                if language_code == "es":
                    reply_prefix = "Gracias por los detalles. "
                else:
                    # Test suite checks for this phrase.
                    reply_prefix = "Thanks for the details. "

            if language_code == "es":
                reply = (
                    reply_prefix + "¿Quieres que busque la siguiente cita disponible?"
                )
            else:
                reply = (
                    reply_prefix
                    + "Would you like me to look for the next available appointment time?"
                )
            return ConversationResult(
                reply_text=reply, new_state=_session_state(session)
            )

        # ASK_SCHEDULE: search for slots or mark for follow-up.
        if session.stage == "ASK_SCHEDULE":
            if "no" in lower:
                if language_code == "es":
                    reply = (
                        "De acuerdo, no agendaré nada por ahora. "
                        f"Alguien de {business_name} se pondrá en contacto contigo."
                    )
                else:
                    reply = (
                        "Okay, I won't schedule anything right now. "
                        f"Someone from {business_name} will follow up with you."
                    )
                session.stage = "COMPLETED"
                session.status = "PENDING_FOLLOWUP"
                return ConversationResult(
                    reply_text=reply, new_state=_session_state(session)
                )

            # Any non-negative response is treated as consent to search for a slot.
            duration_minutes = _infer_duration_minutes(
                session.problem_summary,
                session.is_emergency,
                business_id,
            )
            calendar_id = get_calendar_id_for_business(business_id)
            slots = await calendar_service.find_slots(
                duration_minutes=duration_minutes,
                calendar_id=calendar_id,
                business_id=business_id,
                address=session.address,
                is_emergency=session.is_emergency,
            )
            slot: TimeSlot | None = slots[0] if slots else None
            if not slot:
                if language_code == "es":
                    reply = (
                        "No puedo encontrar un horario disponible en este momento. "
                        "Alguien revisará tu solicitud y te llamará pronto."
                    )
                else:
                    reply = (
                        "I'm unable to find an open time slot right now. "
                        "Someone will review your request and call you back shortly."
                    )
                session.stage = "COMPLETED"
                session.status = "PENDING_FOLLOWUP"
                return ConversationResult(
                    reply_text=reply, new_state=_session_state(session)
                )

            session.stage = "CONFIRM_SLOT"
            session.requested_time = slot.start.isoformat()
            when_str = slot.start.strftime("%A at %I:%M %p UTC")
            if language_code == "es":
                reply = f"Te puedo agendar el {when_str}. ¿Ese horario te funciona?"
            else:
                # Test suite looks for this phrase.
                reply = (
                    f"I can book you for {when_str}. " "Does that time work for you?"
                )
            return ConversationResult(
                reply_text=reply,
                new_state=_session_state(session, pending_slot=slot),
            )

        # CONFIRM_SLOT: finalize appointment or mark for follow-up.
        if session.stage == "CONFIRM_SLOT":
            if "no" in lower:
                if language_code == "es":
                    reply = (
                        "De acuerdo, no reservaré ese horario. "
                        "Un miembro del equipo se pondrá en contacto contigo para encontrar otra hora."
                    )
                else:
                    reply = (
                        "Okay, I won't schedule that time. "
                        "A team member will contact you to find a different slot."
                    )
                session.stage = "COMPLETED"
                session.status = "PENDING_FOLLOWUP"
                return ConversationResult(
                    reply_text=reply, new_state=_session_state(session)
                )

            # Confirm the proposed slot and create the appointment.
            duration_minutes = _infer_duration_minutes(
                session.problem_summary,
                session.is_emergency,
                business_id,
            )
            if session.requested_time:
                start = datetime.fromisoformat(session.requested_time)
                end = start + timedelta(minutes=duration_minutes)
                slot = TimeSlot(start=start, end=end)
            else:  # pragma: no cover - defensive fallback
                calendar_id = get_calendar_id_for_business(business_id)
                slots = await calendar_service.find_slots(
                    duration_minutes=duration_minutes,
                    calendar_id=calendar_id,
                    business_id=business_id,
                    is_emergency=session.is_emergency,
                    address=session.address,
                )
                slot = slots[0] if slots else None
                if not slot:
                    if language_code == "es":
                        reply = (
                            "No puedo confirmar un horario en este momento. "
                            "Alguien revisará tu solicitud y te llamará pronto."
                        )
                    else:
                        reply = (
                            "I'm unable to confirm a time slot right now. "
                            "Someone will review your request and call you back shortly."
                        )
                    session.stage = "COMPLETED"
                    session.status = "PENDING_FOLLOWUP"
                    return ConversationResult(
                        reply_text=reply,
                        new_state=_session_state(session),
                    )

            summary_name = session.caller_name or "Customer"
            service_type = _infer_service_type(session.problem_summary)
            summary = f"Plumbing appointment for {summary_name}"
            description_parts = [
                f"Phone: {session.caller_phone}",
                f"Address: {session.address}",
                f"Problem: {session.problem_summary}",
            ]
            if service_type:
                description_parts.append(f"Service type: {service_type}")
            if session.is_emergency:
                description_parts.append("EMERGENCY: true")
            description = "\n".join(part for part in description_parts if part)
            await subscription_service.check_access(
                business_id, feature="appointments", upcoming_appointments=1
            )
            calendar_id = get_calendar_id_for_business(business_id)
            event_id = await calendar_service.create_event(
                summary=summary,
                slot=slot,
                description=description,
                calendar_id=calendar_id,
                business_id=business_id,
            )

            quoted_min, quoted_max = _infer_quote_for_service_type(
                service_type,
                session.is_emergency,
            )
            quoted_value = None
            quote_status: str | None = None
            if quoted_min is not None and quoted_max is not None:
                quoted_value = float((quoted_min + quoted_max) / 2.0)
                quote_status = "QUOTED"

            # Mirror into in-memory CRM repositories.
            customer = customers_repo.upsert(
                name=session.caller_name or "Customer",
                phone=session.caller_phone or "",
                address=session.address,
                business_id=business_id,
            )
            # Derive a simple lead source from the session channel.
            # This feeds owner lead-source analytics.
            channel = getattr(session, "channel", "phone") or "phone"
            campaign_tag = getattr(session, "lead_source", None)
            lead_source = _normalize_lead_source(channel, campaign_tag)
            appointment = appointments_repo.create(
                customer_id=customer.id,
                start_time=slot.start,
                end_time=slot.end,
                service_type=service_type,
                is_emergency=session.is_emergency,
                description=session.problem_summary,
                lead_source=lead_source,
                estimated_value=None,
                job_stage="Booked",
                business_id=business_id,
                calendar_event_id=event_id,
                tags=[],
                quoted_value=quoted_value,
                quote_status=quote_status,
            )
            metrics.appointments_scheduled += 1

            logger.info(
                "appointment_created",
                extra={
                    "appointment_id": appointment.id,
                    "business_id": business_id,
                    "customer_id": customer.id,
                    "is_emergency": session.is_emergency,
                    "start_time": slot.start.isoformat(),
                },
            )

            # Notify owner via SMS if configured.
            if sms_service.owner_number:
                when_str = slot.start.strftime("%a %b %d at %I:%M %p UTC")
                if language_code == "es":
                    if session.is_emergency:
                        owner_body = (
                            f"[EMERGENCIA] Nueva cita de emergencia para {summary_name} el {when_str}.\n"
                            f"Dirección: {session.address or 'n/a'}\n"
                            f"Problema: {session.problem_summary or 'n/a'}"
                        )
                    else:
                        owner_body = (
                            f"[Estándar] Nueva cita para {summary_name} el {when_str}.\n"
                            f"Dirección: {session.address or 'n/a'}\n"
                            f"Problema: {session.problem_summary or 'n/a'}"
                        )
                else:
                    if session.is_emergency:
                        owner_body = (
                            f"[EMERGENCY] New emergency appointment for {summary_name} on {when_str}.\n"
                            f"Address: {session.address or 'n/a'}\n"
                            f"Problem: {session.problem_summary or 'n/a'}"
                        )
                    else:
                        owner_body = (
                            f"[Standard] New appointment for {summary_name} on {when_str}.\n"
                            f"Address: {session.address or 'n/a'}\n"
                            f"Problem: {session.problem_summary or 'n/a'}"
                        )
                await sms_service.notify_owner(owner_body, business_id=business_id)

            # Send confirmation to customer if we have a phone number and they have not opted out.
            if session.caller_phone and not getattr(customer, "sms_opt_out", False):
                when_str = slot.start.strftime("%a %b %d at %I:%M %p UTC")
                if language_code == "es":
                    customer_body = (
                        f"Habla {business_name}. Tu cita está programada para el {when_str}.\n"
                        "Si ese horario no te funciona, por favor llama o envíanos un mensaje de texto para reprogramar."
                    )
                else:
                    customer_body = (
                        f"This is {business_name}. Your appointment is scheduled for {when_str}.\n"
                        "If this time does not work, please call or text to reschedule."
                    )
                await sms_service.notify_customer(
                    session.caller_phone,
                    customer_body,
                    business_id=business_id,
                )

            session.stage = "COMPLETED"
            session.status = "SCHEDULED"
            if language_code == "es":
                reply = "Listo. Hemos programado tu cita y te veremos entonces."
                if session.is_emergency:
                    reply += " Como fue marcada como emergencia, la trataremos como una prioridad alta."
            else:
                # Test suite checks for this phrase.
                reply = "You're all set. We've scheduled your appointment and will see you then."
                if session.is_emergency:
                    reply += " Because this was flagged as an emergency, we will treat it as a high priority."
            return ConversationResult(
                reply_text=reply, new_state=_session_state(session)
            )

        # Fallback for completed or unknown stages.
        if language_code == "es":
            reply = "Esta sesión parece completa. Si necesitas algo más, por favor vuelve a llamar."
        else:
            reply = "This session looks complete. If you need anything else, please call back."
        return ConversationResult(reply_text=reply, new_state=_session_state(session))


conversation_manager = ConversationManager()
