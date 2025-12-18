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
    classify_intent_with_metadata,
)
from .email_service import email_service
from . import subscription as subscription_service
from ..config import get_settings
from ..db import SQLALCHEMY_AVAILABLE, SessionLocal
from ..db_models import BusinessDB
from ..metrics import CallbackItem, metrics
from ..repositories import appointments_repo, customers_repo, conversations_repo
from ..business_config import (
    get_calendar_id_for_business,
    get_language_for_business,
    get_vertical_for_business,
)
from ..assistant_i18n import conversation_text


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

AFFIRMATIVE = {"yes", "y", "yeah", "ya", "si", "sí", "sure", "affirmative"}
NEGATIVE = {"no", "n", "nope"}


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


def _score_emergency_signal(
    text: str | None,
    intent_label: str | None,
    intent_confidence: float | None,
    keywords: list[str],
    existing_confidence: float,
) -> tuple[float, list[str]]:
    """Return (confidence, reasons) for emergency detection."""

    if not text:
        return existing_confidence, []

    lower = text.lower()
    reasons: list[str] = []
    confidence = existing_confidence

    if intent_label == "emergency":
        reasons.append("intent:emergency")
        confidence = max(confidence, intent_confidence or 0.9, 0.85)

    hits = [kw for kw in keywords if kw in lower]
    if hits:
        reasons.extend(f"keyword:{kw}" for kw in hits[:3])
        keyword_conf = min(0.9, 0.6 + 0.1 * len(hits))
        confidence = max(confidence, keyword_conf)

    return confidence, reasons


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


ALLOWED_ASSISTANT_INTENTS = {
    "schedule",
    "reschedule",
    "cancel",
    "faq",
    "emergency",
    "fallback",
    "greeting",
    "other",
}
ALLOWED_TERMINAL_STATUSES = {
    "SCHEDULED",
    "PENDING_FOLLOWUP",
    "COMPLETED",
    "ABANDONED",
    "CANCELLED",
}


def _normalize_intent_label(intent: str | None) -> str | None:
    """Return a guard-railed intent label, coercing unknowns to fallback."""
    if not intent:
        return None
    return intent if intent in ALLOWED_ASSISTANT_INTENTS else "fallback"


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
        "emergency_confidence": getattr(session, "emergency_confidence", 0.0),
        "emergency_reasons": getattr(session, "emergency_reasons", []),
        "emergency_confirmation_pending": getattr(
            session, "emergency_confirmation_pending", False
        ),
    }
    if pending_slot:
        state["proposed_slot"] = {
            "start": pending_slot.start.isoformat(),
            "end": pending_slot.end.isoformat(),
        }
    return state


def _enqueue_callback_followup(
    session: CallSession,
    business_id: str,
    reason: str,
) -> None:
    """Ensure the caller is queued for manual follow-up/callback."""
    phone = session.caller_phone or session.id
    if not phone:
        return
    queue = metrics.callbacks_by_business.setdefault(business_id, {})
    now = datetime.now(UTC)
    existing = queue.get(phone)
    channel = getattr(session, "channel", "phone") or "phone"
    lead_source = getattr(session, "lead_source", None)
    if existing is None:
        queue[phone] = CallbackItem(
            phone=phone,
            first_seen=now,
            last_seen=now,
            count=1,
            channel=channel,
            lead_source=lead_source,
            reason=reason,
        )
        return
    existing.last_seen = now
    existing.count += 1
    existing.reason = reason or existing.reason
    existing.channel = channel or existing.channel
    if lead_source:
        existing.lead_source = lead_source
    if getattr(existing, "status", "PENDING").upper() != "PENDING":
        existing.status = "PENDING"
        existing.last_result = None


def _ensure_terminal_status(session: CallSession) -> None:
    """Force terminal status into the allowed set when the stage is complete."""
    if session.stage == "COMPLETED" and session.status not in ALLOWED_TERMINAL_STATUSES:
        session.status = "ABANDONED"


def _handoff_to_human(
    session: CallSession,
    business_id: str,
    language_code: str,
    *,
    reason: str,
) -> ConversationResult:
    """Escalate to manual follow-up with a deterministic terminal state."""
    _enqueue_callback_followup(session, business_id, reason=reason)
    session.stage = "COMPLETED"
    session.status = "PENDING_FOLLOWUP"
    reply = conversation_text(language_code, "handoff_base")
    if session.is_emergency:
        reply += conversation_text(language_code, "handoff_emergency_append")
    _ensure_terminal_status(session)
    return ConversationResult(reply_text=reply, new_state=_session_state(session))


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
        classified_intent: str | None = None
        intent_low_confidence = False
        history: list[str] = []
        conv = conversations_repo.get_by_session(session.id)
        if conv and getattr(conv, "messages", None):
            history = [
                m.text
                for m in conv.messages[-4:]
                if getattr(m, "role", "") == "user" and getattr(m, "text", None)
            ]
        if normalized:
            try:
                intent_meta = await classify_intent_with_metadata(
                    normalized, business_id, history=history
                )
                classified_intent = intent_meta["intent"]
                session.intent = classified_intent
                session.intent_confidence = intent_meta.get("confidence")
                logger.debug(
                    "intent_classified",
                    extra={
                        "business_id": business_id,
                        "intent": session.intent,
                        "confidence": session.intent_confidence,
                        "provider": intent_meta.get("provider"),
                    },
                )
            except Exception:
                session.intent = session.intent or None
                session.intent_confidence = getattr(session, "intent_confidence", None)
        threshold = _intent_threshold_for_business(business_id)
        intent_confidence = getattr(session, "intent_confidence", None)
        if intent_confidence is not None and intent_confidence < threshold:
            intent_low_confidence = True
            session.intent = None
        normalized_intent_label = _normalize_intent_label(session.intent)
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

        # Incorporate user confirmation when pending.
        if getattr(session, "emergency_confirmation_pending", False) and normalized:
            norm_simple = normalized.strip().lower()
            if norm_simple in AFFIRMATIVE:
                session.is_emergency = True
                session.emergency_confidence = max(session.emergency_confidence, 0.95)
                session.emergency_reasons.append("user_confirmed")
                session.emergency_confirmation_pending = False
                normalized = ""
                lower = ""
            elif norm_simple in NEGATIVE:
                session.emergency_confirmation_pending = False
                session.emergency_confidence = min(session.emergency_confidence, 0.3)
                normalized = ""
                lower = ""
            else:
                reason_text = (
                    session.emergency_reasons[0]
                    if getattr(session, "emergency_reasons", None)
                    else "details provided"
                )
                prompt = conversation_text(
                    language_code, "emergency_confirm", reason=reason_text
                )
                return ConversationResult(
                    reply_text=prompt, new_state=_session_state(session)
                )

        # Score emergency signals deterministically.
        emergency_conf, reasons = _score_emergency_signal(
            normalized,
            normalized_intent_label,
            session.intent_confidence,
            emergency_keywords,
            getattr(session, "emergency_confidence", 0.0),
        )
        if reasons:
            existing = getattr(session, "emergency_reasons", [])
            merged = existing + [r for r in reasons if r not in existing]
            session.emergency_reasons = merged
        session.emergency_confidence = max(
            getattr(session, "emergency_confidence", 0.0), emergency_conf
        )
        if session.emergency_confidence >= 0.8:
            session.is_emergency = True
        elif (
            session.emergency_confidence >= 0.5
            and not session.is_emergency
            and not getattr(session, "emergency_confirmation_pending", False)
            and normalized
        ):
            session.emergency_confirmation_pending = True
            reason_text = (
                session.emergency_reasons[0]
                if getattr(session, "emergency_reasons", None)
                else "details provided"
            )
            prompt = conversation_text(
                language_code, "emergency_confirm", reason=reason_text
            )
            return ConversationResult(
                reply_text=prompt, new_state=_session_state(session)
            )

        guardrail_action_stages = {"ASK_SCHEDULE", "CONFIRM_SLOT"}
        if normalized and session.stage != "COMPLETED":
            if (
                intent_low_confidence
                and session.stage in guardrail_action_stages
                and classified_intent not in {"other", "greeting"}
            ):
                return _handoff_to_human(
                    session, business_id, language_code, reason="LOW_CONFIDENCE"
                )
            if normalized_intent_label in {"cancel", "reschedule", "faq"}:
                return _handoff_to_human(
                    session,
                    business_id,
                    language_code,
                    reason=(normalized_intent_label or "FALLBACK").upper(),
                )
            if (
                normalized_intent_label == "fallback"
                and session.stage in guardrail_action_stages
            ):
                return _handoff_to_human(
                    session,
                    business_id,
                    language_code,
                    reason="FALLBACK",
                )

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
                if is_returning_customer:
                    name_part = (
                        f" {returning_customer_name}" if returning_customer_name else ""
                    )
                    reply = conversation_text(
                        language_code,
                        "greeting_returning",
                        name_part=name_part,
                        business_name=business_name,
                        vertical=vertical,
                    )
                else:
                    reply = conversation_text(
                        language_code,
                        "greeting_new",
                        business_name=business_name,
                        vertical=vertical,
                    )
                session.stage = "ASK_NAME"
                return ConversationResult(
                    reply_text=reply, new_state=_session_state(session)
                )

            # If the caller says something on the greeting turn, treat it as a name.
            parsed_name = parse_name(normalized) or normalized
            session.caller_name = parsed_name
            session.stage = "ASK_ADDRESS"
            reply = conversation_text(
                language_code, "ask_address_after_greeting", name=parsed_name
            )
            return ConversationResult(
                reply_text=reply, new_state=_session_state(session)
            )

        # ASK_NAME: capture caller name.
        if session.stage == "ASK_NAME":
            if not normalized:
                reply = conversation_text(language_code, "ask_name_missing")
                return ConversationResult(
                    reply_text=reply, new_state=_session_state(session)
                )

            parsed_name = parse_name(normalized) or normalized
            session.caller_name = parsed_name
            session.stage = "ASK_ADDRESS"
            # Test suite looks for this phrase.
            reply = conversation_text(language_code, "ask_address_after_name")
            return ConversationResult(
                reply_text=reply, new_state=_session_state(session)
            )

        # ASK_ADDRESS: collect or confirm address.
        if session.stage == "ASK_ADDRESS":
            if not normalized and returning_customer_address:
                # Offer to reuse known address.
                session.address = returning_customer_address
                session.stage = "CONFIRM_ADDRESS"
                reply = conversation_text(
                    language_code,
                    "offer_existing_address",
                    address=returning_customer_address,
                )
                return ConversationResult(
                    reply_text=reply, new_state=_session_state(session)
                )

            if not normalized:
                reply = conversation_text(language_code, "ask_address_full")
                return ConversationResult(
                    reply_text=reply, new_state=_session_state(session)
                )

            # Treat any non-empty answer as an address.
            parsed_address = parse_address(normalized) or normalized
            session.address = parsed_address
            session.stage = "ASK_PROBLEM"
            if language_code == "es":
                reply = conversation_text(
                    language_code, "ask_problem", vertical="plomería"
                )
            else:
                # Test suite looks for this phrase.
                reply = conversation_text(
                    language_code, "ask_problem", vertical=vertical
                )
            return ConversationResult(
                reply_text=reply, new_state=_session_state(session)
            )

        # CONFIRM_ADDRESS: confirm or replace stored address.
        if session.stage == "CONFIRM_ADDRESS":
            if "no" in lower:
                session.address = None
                session.stage = "ASK_ADDRESS"
                reply = conversation_text(language_code, "ask_address_after_name")
                return ConversationResult(
                    reply_text=reply, new_state=_session_state(session)
                )

            # Any non-negative answer confirms the stored address.
            session.stage = "ASK_PROBLEM"
            if language_code == "es":
                reply = conversation_text(
                    language_code, "ask_problem", vertical="plomería"
                )
            else:
                reply = conversation_text(
                    language_code, "ask_problem", vertical=vertical
                )
            return ConversationResult(
                reply_text=reply, new_state=_session_state(session)
            )

        # ASK_PROBLEM: capture problem summary and move to scheduling.
        if session.stage == "ASK_PROBLEM":
            if not normalized:
                vertical_for_prompt = "plomería" if language_code == "es" else vertical
                reply = conversation_text(
                    language_code, "ask_problem_missing", vertical=vertical_for_prompt
                )
                return ConversationResult(
                    reply_text=reply, new_state=_session_state(session)
                )

            session.problem_summary = normalized
            session.stage = "ASK_SCHEDULE"
            if session.is_emergency:
                reply_prefix = conversation_text(
                    language_code, "schedule_prefix_emergency"
                )
            else:
                # Test suite checks for this phrase.
                reply_prefix = conversation_text(
                    language_code, "schedule_prefix_standard"
                )
            reply = reply_prefix + conversation_text(language_code, "schedule_question")
            return ConversationResult(
                reply_text=reply, new_state=_session_state(session)
            )

        # ASK_SCHEDULE: search for slots or mark for follow-up.
        if session.stage == "ASK_SCHEDULE":
            if "no" in lower:
                reply = conversation_text(
                    language_code,
                    "schedule_decline",
                    business_name=business_name,
                )
                session.stage = "COMPLETED"
                session.status = "PENDING_FOLLOWUP"
                return ConversationResult(
                    reply_text=reply, new_state=_session_state(session)
                )
            if not session.address:
                session.stage = "ASK_ADDRESS"
                reply = conversation_text(language_code, "schedule_need_address")
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
                reply = conversation_text(language_code, "schedule_no_slot")
                session.stage = "COMPLETED"
                session.status = "PENDING_FOLLOWUP"
                return ConversationResult(
                    reply_text=reply, new_state=_session_state(session)
                )

            session.stage = "CONFIRM_SLOT"
            session.requested_time = slot.start.isoformat()
            when_str = slot.start.strftime("%A at %I:%M %p UTC")
            # Test suite looks for this phrase.
            reply = conversation_text(language_code, "schedule_propose", when=when_str)
            return ConversationResult(
                reply_text=reply,
                new_state=_session_state(session, pending_slot=slot),
            )

        # CONFIRM_SLOT: finalize appointment or mark for follow-up.
        if session.stage == "CONFIRM_SLOT":
            if "no" in lower:
                reply = conversation_text(language_code, "confirm_slot_decline")
                session.stage = "COMPLETED"
                session.status = "PENDING_FOLLOWUP"
                return ConversationResult(
                    reply_text=reply, new_state=_session_state(session)
                )

            if not session.address:
                return _handoff_to_human(
                    session,
                    business_id,
                    language_code,
                    reason="MISSING_ADDRESS",
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
                    reply = conversation_text(language_code, "confirm_slot_unable")
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
            quoted_value: int | None = None
            quote_status: str | None = None
            if quoted_min is not None and quoted_max is not None:
                quoted_value = int(round((quoted_min + quoted_max) / 2.0))
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

            # Notify owner with dedupe + fallback when configured.
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
            from .owner_notifications import notify_owner_with_fallback

            subject = (
                "Emergency booking"
                if session.is_emergency
                else "New appointment booked"
            )
            await notify_owner_with_fallback(
                business_id=business_id,
                message=owner_body,
                subject=subject,
                dedupe_key=f"appt_{appointment.id}",
            )

            # Send confirmation to customer if we have a phone number and they have not opted out.
            if session.caller_phone and not getattr(customer, "sms_opt_out", False):
                when_str = slot.start.strftime("%a %b %d at %I:%M %p UTC")
                customer_body = conversation_text(
                    language_code,
                    "customer_sms_confirm",
                    business_name=business_name,
                    when=when_str,
                )
                await sms_service.notify_customer(
                    session.caller_phone,
                    customer_body,
                    business_id=business_id,
                )
                customer_email = getattr(customer, "email", None)
                if customer_email:
                    # Best-effort email confirmation using the configured provider (Gmail/SendGrid/stub).
                    email_subject = f"Appointment confirmed with {business_name}"
                    email_body = customer_body
                    try:
                        await email_service.send_email(
                            to=customer_email,
                            subject=email_subject,
                            body=email_body,
                            business_id=business_id,
                        )
                    except Exception:
                        logger.warning(
                            "customer_email_confirmation_failed",
                            exc_info=True,
                            extra={"business_id": business_id},
                        )

            session.stage = "COMPLETED"
            session.status = "SCHEDULED"
            # Test suite checks for this phrase.
            reply = conversation_text(language_code, "completed_standard")
            if session.is_emergency:
                reply += conversation_text(language_code, "completed_emergency_append")
            return ConversationResult(
                reply_text=reply, new_state=_session_state(session)
            )

        # Fallback for completed or unknown stages.
        session.stage = "COMPLETED"
        _ensure_terminal_status(session)
        reply = conversation_text(language_code, "completed_fallback")
        return ConversationResult(reply_text=reply, new_state=_session_state(session))


conversation_manager = ConversationManager()
