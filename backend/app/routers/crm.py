from __future__ import annotations

from datetime import datetime
import json
from typing import Any, Dict, List

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from ..config import get_settings
from ..deps import ensure_business_active, require_owner_dashboard_auth
from ..repositories import appointments_repo, conversations_repo, customers_repo
from ..business_config import get_calendar_id_for_business
from ..services.calendar import TimeSlot, calendar_service


router = APIRouter(dependencies=[Depends(require_owner_dashboard_auth)])


class CustomerCreateRequest(BaseModel):
    name: str
    phone: str
    email: str | None = None
    address: str | None = None
    tags: list[str] | None = None


class CustomerResponse(BaseModel):
    id: str
    name: str
    phone: str
    email: str | None = None
    address: str | None = None
    tags: list[str] = []


class AppointmentCreateRequest(BaseModel):
    customer_id: str
    start_time: datetime
    end_time: datetime
    service_type: str | None = None
    is_emergency: bool = False
    description: str | None = None
    lead_source: str | None = None
    estimated_value: float | None = None
    job_stage: str | None = None
    tags: list[str] | None = None
    technician_id: str | None = None
    quoted_value: float | None = None
    quote_status: str | None = None


class AppointmentResponse(BaseModel):
    id: str
    customer_id: str
    start_time: datetime
    end_time: datetime
    service_type: str | None = None
    description: str | None = None
    is_emergency: bool
    status: str
    lead_source: str | None = None
    estimated_value: float | None = None
    job_stage: str | None = None
    calendar_event_id: str | None = None
    tags: list[str] = []
    technician_id: str | None = None
    quoted_value: float | None = None
    quote_status: str | None = None


class AppointmentUpdateRequest(BaseModel):
    start_time: datetime | None = None
    end_time: datetime | None = None
    service_type: str | None = None
    description: str | None = None
    is_emergency: bool | None = None
    status: str | None = None
    lead_source: str | None = None
    estimated_value: float | None = None
    job_stage: str | None = None
    tags: list[str] | None = None
    technician_id: str | None = None
    quoted_value: float | None = None
    quote_status: str | None = None


class AppointmentSlotResponse(BaseModel):
    start_time: datetime
    end_time: datetime


class ConversationMessageResponse(BaseModel):
    role: str
    text: str
    timestamp: datetime


class ConversationSummaryResponse(BaseModel):
    id: str
    channel: str
    customer_id: str | None = None
    session_id: str | None = None
    created_at: datetime
    message_count: int
    flagged_for_review: bool = False
    tags: list[str] = []
    outcome: str | None = None
    service_type: str | None = None
    has_appointments: bool = False


class ConversationDetailResponse(BaseModel):
    id: str
    channel: str
    customer_id: str | None = None
    session_id: str | None = None
    created_at: datetime
    flagged_for_review: bool = False
    tags: list[str] = []
    outcome: str | None = None
    notes: str | None = None
    messages: list[ConversationMessageResponse]
    service_type: str | None = None
    has_appointments: bool = False
    qa_suggestions: "ConversationQaSuggestion | None" = None


class ConversationQaSuggestion(BaseModel):
    likely_outcome: str | None = None  # e.g. "booked", "lost", "price_shopper"
    followup_needed: bool | None = None
    emergency_handled_ok: str | None = None  # "yes", "no", "unsure", "not_emergency"
    source: str = "heuristic"  # "heuristic" or "llm"


class ConversationQAUpdate(BaseModel):
    flagged_for_review: bool | None = None
    tags: list[str] | None = None
    outcome: str | None = None
    notes: str | None = None


class TimelineItem(BaseModel):
    type: str  # "appointment" or "conversation"
    id: str
    timestamp: datetime
    title: str
    status: str | None = None
    channel: str | None = None


def _normalize_outcome_label(text: str) -> str | None:
    value = (text or "").strip().lower()
    if not value:
        return None
    if any(token in value for token in ("booked", "scheduled", "confirmed")):
        return "booked"
    if any(token in value for token in ("cancel", "cancelled", "no show", "lost")):
        return "lost"
    if any(token in value for token in ("quote", "estimate", "bid", "shopping", "shopper")):
        return "price_shopper"
    return value


def _build_heuristic_qa_suggestions(
    conv: Any,
    service_type: str | None,
    has_appointments: bool,
) -> ConversationQaSuggestion:
    outcome_raw = getattr(conv, "outcome", None) or ""
    outcome_label = _normalize_outcome_label(outcome_raw) or None
    tags = getattr(conv, "tags", []) or []
    tags_lower = [str(t or "").strip().lower() for t in tags if str(t or "").strip()]

    # Simple emergency detection from tags/outcome text.
    combined = (outcome_raw + " " + " ".join(tags_lower)).lower()
    is_emergency_related = "emergency" in combined

    # Derive a default likely outcome.
    likely_outcome = outcome_label
    if likely_outcome is None and has_appointments:
        likely_outcome = "booked"

    # Follow-up needed: likely when no appointment exists and we have no clear "lost".
    followup_needed: bool | None
    if not has_appointments and likely_outcome != "lost":
        followup_needed = True
    elif likely_outcome == "lost":
        followup_needed = False
    else:
        followup_needed = None

    # Emergency handling hint.
    if is_emergency_related:
        if has_appointments and likely_outcome != "lost":
            emergency_handled_ok = "yes"
        elif likely_outcome == "lost" or not has_appointments:
            emergency_handled_ok = "no"
        else:
            emergency_handled_ok = "unsure"
    else:
        emergency_handled_ok = "not_emergency"

    return ConversationQaSuggestion(
        likely_outcome=likely_outcome,
        followup_needed=followup_needed,
        emergency_handled_ok=emergency_handled_ok,
        source="heuristic",
    )


async def _maybe_llm_enrich_qa_suggestions(
    conv: Any,
    suggestion: ConversationQaSuggestion,
    service_type: str | None,
    has_appointments: bool,
) -> ConversationQaSuggestion:
    """Optionally refine QA suggestions using an LLM when configured.

    This runs outside of any telephony/webhook paths and is used only for
    owner QA review flows. When the OpenAI configuration is missing or any
    error occurs, the original heuristic suggestions are returned.
    """
    settings = get_settings()
    speech_cfg = settings.speech
    api_key = getattr(speech_cfg, "openai_api_key", None)
    provider = getattr(speech_cfg, "provider", "stub")
    if provider != "openai" or not api_key:
        return suggestion

    # Build a compact transcript with the most recent messages.
    messages = getattr(conv, "messages", []) or []
    lines: List[str] = []
    # Limit to the last 10 turns to control prompt size.
    for m in messages[-10:]:
        role = getattr(m, "role", "user")
        text = getattr(m, "text", "")
        prefix = "Caller" if role == "user" else "Assistant"
        lines.append(f"{prefix}: {text}")

    meta_bits = []
    if service_type:
        meta_bits.append(f"service_type={service_type}")
    if has_appointments:
        meta_bits.append("has_appointments=true")
    if getattr(conv, "flagged_for_review", False):
        meta_bits.append("flagged_for_review=true")
    raw_outcome = getattr(conv, "outcome", None) or ""
    if raw_outcome:
        meta_bits.append(f"outcome_field={raw_outcome}")

    meta = "; ".join(meta_bits) if meta_bits else "none"
    transcript = "\n".join(lines) if lines else "(no transcript messages available)"

    system_prompt = (
        "You are a QA assistant for a home services scheduling assistant. "
        "Given a short transcript and minimal metadata, infer three things:\n"
        "- likely_outcome: one of 'booked', 'lost', 'price_shopper', or a short free-text label if none fit.\n"
        "- followup_needed: true or false.\n"
        "- emergency_handled_ok: one of 'yes', 'no', 'unsure', or 'not_emergency'.\n"
        "Respond with a single JSON object only, no extra commentary."
    )
    user_prompt = (
        f"Metadata: {meta}\n\n"
        f"Transcript:\n{transcript}\n\n"
        "Return JSON with keys: likely_outcome, followup_needed, emergency_handled_ok."
    )

    url = speech_cfg.openai_api_base.rstrip("/") + "/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload: Dict[str, Any] = {
        "model": getattr(speech_cfg, "openai_tts_model", "gpt-4o-mini"),
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.2,
    }

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
    except Exception:
        # On any network or parsing error, keep heuristic suggestion.
        return suggestion

    content = (
        data.get("choices", [{}])[0]
        .get("message", {})
        .get("content", "")
        .strip()
    )
    if not content:
        return suggestion

    parsed: Dict[str, Any]
    try:
        parsed = json.loads(content)
    except Exception:
        # Attempt to salvage JSON object if the model wrapped it in prose.
        start = content.find("{")
        end = content.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return suggestion
        try:
            parsed = json.loads(content[start : end + 1])
        except Exception:
            return suggestion

    # Merge LLM hints with heuristics, preferring explicit LLM values when they
    # are present and well-formed.
    llm_outcome_raw = parsed.get("likely_outcome")
    if isinstance(llm_outcome_raw, str) and llm_outcome_raw.strip():
        llm_outcome = _normalize_outcome_label(llm_outcome_raw)
        if llm_outcome:
            suggestion.likely_outcome = llm_outcome

    llm_followup = parsed.get("followup_needed")
    if isinstance(llm_followup, bool):
        suggestion.followup_needed = llm_followup
    elif isinstance(llm_followup, str):
        val = llm_followup.strip().lower()
        if val in {"true", "yes"}:
            suggestion.followup_needed = True
        elif val in {"false", "no"}:
            suggestion.followup_needed = False

    llm_emergency = parsed.get("emergency_handled_ok")
    if isinstance(llm_emergency, str) and llm_emergency.strip():
        suggestion.emergency_handled_ok = llm_emergency.strip().lower()

    suggestion.source = "llm"
    return suggestion


@router.post("/customers", response_model=CustomerResponse)
def create_or_update_customer(
    payload: CustomerCreateRequest,
    business_id: str = Depends(ensure_business_active),
) -> CustomerResponse:
    customer = customers_repo.upsert(
        name=payload.name,
        phone=payload.phone,
        email=payload.email,
        address=payload.address,
        business_id=business_id,
        tags=payload.tags,
    )
    return CustomerResponse(
        id=customer.id,
        name=customer.name,
        phone=customer.phone,
        email=customer.email,
        address=customer.address,
    )


@router.get("/customers", response_model=list[CustomerResponse])
def list_customers(
    business_id: str = Depends(ensure_business_active),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> list[CustomerResponse]:
    customers = customers_repo.list_for_business(business_id)
    slice_start = offset
    slice_end = offset + limit
    window = customers[slice_start:slice_end]
    return [
        CustomerResponse(
            id=c.id,
            name=c.name,
            phone=c.phone,
            email=c.email,
            address=c.address,
            tags=getattr(c, "tags", []) or [],
        )
        for c in window
    ]


@router.get("/customers/search", response_model=list[CustomerResponse])
def search_customers(
    q: str = Query(..., min_length=1),
    business_id: str = Depends(ensure_business_active),
) -> list[CustomerResponse]:
    """Search customers for this tenant by name or phone.

    - Name: case-insensitive substring match.
    - Phone: exact match on the stored phone string.
    """
    query = q.strip().lower()
    results: list[CustomerResponse] = []
    for c in customers_repo.list_for_business(business_id):
        name_match = query in (c.name or "").lower()
        phone_match = c.phone == q
        if not (name_match or phone_match):
            continue
        results.append(
            CustomerResponse(
                id=c.id,
                name=c.name,
                phone=c.phone,
                email=c.email,
                address=c.address,
                tags=getattr(c, "tags", []) or [],
            )
        )
    return results


@router.post("/appointments", response_model=AppointmentResponse)
def create_appointment(
    payload: AppointmentCreateRequest,
    business_id: str = Depends(ensure_business_active),
) -> AppointmentResponse:
    customer = customers_repo.get(payload.customer_id)
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    appt = appointments_repo.create(
        customer_id=payload.customer_id,
        start_time=payload.start_time,
        end_time=payload.end_time,
        service_type=payload.service_type,
        is_emergency=payload.is_emergency,
        description=payload.description,
        lead_source=payload.lead_source,
        estimated_value=int(payload.estimated_value) if payload.estimated_value is not None else None,
        job_stage=payload.job_stage,
        business_id=business_id,
        tags=payload.tags,
        technician_id=payload.technician_id,
        quoted_value=int(payload.quoted_value) if payload.quoted_value is not None else None,
        quote_status=payload.quote_status,
    )
    return AppointmentResponse(
        id=appt.id,
        customer_id=appt.customer_id,
        start_time=appt.start_time,
        end_time=appt.end_time,
        service_type=appt.service_type,
        description=appt.description,
        is_emergency=appt.is_emergency,
        status=appt.status,
        lead_source=appt.lead_source,
        estimated_value=appt.estimated_value,
        job_stage=appt.job_stage,
        calendar_event_id=appt.calendar_event_id,
        tags=getattr(appt, "tags", []) or [],
        technician_id=getattr(appt, "technician_id", None),
        quoted_value=getattr(appt, "quoted_value", None),
        quote_status=getattr(appt, "quote_status", None),
    )


@router.get("/customers/{customer_id}/appointments", response_model=list[AppointmentResponse])
def list_customer_appointments(customer_id: str) -> list[AppointmentResponse]:
    customer = customers_repo.get(customer_id)
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    appts = appointments_repo.list_for_customer(customer_id)
    return [
        AppointmentResponse(
            id=a.id,
            customer_id=a.customer_id,
            start_time=a.start_time,
            end_time=a.end_time,
            service_type=a.service_type,
            description=a.description,
            is_emergency=a.is_emergency,
            status=a.status,
            lead_source=getattr(a, "lead_source", None),
            estimated_value=getattr(a, "estimated_value", None),
            job_stage=getattr(a, "job_stage", None),
            tags=getattr(a, "tags", []) or [],
            technician_id=getattr(a, "technician_id", None),
            quoted_value=getattr(a, "quoted_value", None),
            quote_status=getattr(a, "quote_status", None),
        )
        for a in appts
    ]


@router.get("/appointments", response_model=list[AppointmentResponse])
def list_appointments(
    business_id: str = Depends(ensure_business_active),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    start_time_from: datetime | None = Query(default=None),
    start_time_to: datetime | None = Query(default=None),
    status: str | None = Query(default=None),
    service_type: str | None = Query(default=None),
    is_emergency: bool | None = Query(default=None),
    tag: str | None = Query(default=None),
) -> list[AppointmentResponse]:
    appts = appointments_repo.list_for_business(business_id)
    if (
        start_time_from is not None
        or start_time_to is not None
        or status is not None
        or service_type is not None
        or is_emergency is not None
        or tag is not None
    ):
        filtered: list = []
        for a in appts:
            start = getattr(a, "start_time", None)
            if start_time_from is not None and (start is None or start < start_time_from):
                continue
            if start_time_to is not None and (start is None or start > start_time_to):
                continue
            if status is not None:
                current_status = getattr(a, "status", "")
                if current_status.upper() != status.upper():
                    continue
            if service_type is not None:
                current_service = getattr(a, "service_type", None) or ""
                if current_service.lower() != service_type.lower():
                    continue
            if is_emergency is not None:
                current_emergency = bool(getattr(a, "is_emergency", False))
                if current_emergency is not is_emergency:
                    continue
            if tag is not None:
                tags = getattr(a, "tags", []) or []
                if tag not in tags:
                    continue
            filtered.append(a)
        appts = filtered
    slice_start = offset
    slice_end = offset + limit
    window = appts[slice_start:slice_end]
    return [
        AppointmentResponse(
            id=a.id,
            customer_id=a.customer_id,
            start_time=a.start_time,
            end_time=a.end_time,
            service_type=a.service_type,
            description=a.description,
            is_emergency=a.is_emergency,
            status=a.status,
            lead_source=getattr(a, "lead_source", None),
            estimated_value=getattr(a, "estimated_value", None),
            job_stage=getattr(a, "job_stage", None),
            calendar_event_id=a.calendar_event_id,
            tags=getattr(a, "tags", []) or [],
            technician_id=getattr(a, "technician_id", None),
            quoted_value=getattr(a, "quoted_value", None),
            quote_status=getattr(a, "quote_status", None),
        )
        for a in window
    ]


@router.patch("/appointments/{appointment_id}", response_model=AppointmentResponse)
async def update_appointment(
    appointment_id: str,
    payload: AppointmentUpdateRequest,
    business_id: str = Depends(ensure_business_active),
) -> AppointmentResponse:
    appt = appointments_repo.get(appointment_id)
    if not appt or appt.business_id != business_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Appointment not found")

    new_start = payload.start_time or appt.start_time
    new_end = payload.end_time or appt.end_time

    # Update calendar event if we have one.
    if appt.calendar_event_id:
        slot = TimeSlot(start=new_start, end=new_end)
        calendar_id = get_calendar_id_for_business(business_id)
        await calendar_service.update_event(
            event_id=appt.calendar_event_id,
            slot=slot,
            summary=None,
            description=payload.description or appt.description,
            calendar_id=calendar_id,
        )

    updated = appointments_repo.update(
        appointment_id,
        start_time=new_start,
        end_time=new_end,
        service_type=payload.service_type if payload.service_type is not None else appt.service_type,
        description=payload.description if payload.description is not None else appt.description,
        is_emergency=payload.is_emergency if payload.is_emergency is not None else appt.is_emergency,
        status=payload.status if payload.status is not None else appt.status,
        lead_source=payload.lead_source if payload.lead_source is not None else appt.lead_source,
        estimated_value=int(payload.estimated_value) if payload.estimated_value is not None else appt.estimated_value,
        job_stage=payload.job_stage if payload.job_stage is not None else appt.job_stage,
        tags=payload.tags if payload.tags is not None else getattr(appt, "tags", None),
        technician_id=payload.technician_id if payload.technician_id is not None else getattr(appt, "technician_id", None),
        quoted_value=int(payload.quoted_value) if payload.quoted_value is not None else getattr(appt, "quoted_value", None),
        quote_status=payload.quote_status if payload.quote_status is not None else getattr(appt, "quote_status", None),
    )
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Appointment not found")

    return AppointmentResponse(
        id=updated.id,
        customer_id=updated.customer_id,
        start_time=updated.start_time,
        end_time=updated.end_time,
        service_type=updated.service_type,
        description=updated.description,
        is_emergency=updated.is_emergency,
        status=updated.status,
        lead_source=updated.lead_source,
        estimated_value=updated.estimated_value,
        job_stage=updated.job_stage,
        calendar_event_id=updated.calendar_event_id,
        tags=getattr(updated, "tags", []) or [],
        technician_id=getattr(updated, "technician_id", None),
        quoted_value=getattr(updated, "quoted_value", None),
        quote_status=getattr(updated, "quote_status", None),
    )


@router.post(
    "/appointments/{appointment_id}/propose-slots",
    response_model=list[AppointmentSlotResponse],
)
async def propose_appointment_slots(
    appointment_id: str,
    business_id: str = Depends(ensure_business_active),
) -> list[AppointmentSlotResponse]:
    """Return one or more candidate slots for rescheduling an appointment.

    This endpoint is intended for owner/dashboard flows that need to offer a
    new time for an existing appointment while keeping the same duration.
    """
    appt = appointments_repo.get(appointment_id)
    if not appt or appt.business_id != business_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Appointment not found",
        )

    start_time = getattr(appt, "start_time", None)
    end_time = getattr(appt, "end_time", None)
    if not start_time or not end_time:
        return []

    duration_td = end_time - start_time
    duration_minutes = max(int(duration_td.total_seconds() // 60), 15)

    calendar_id = get_calendar_id_for_business(business_id)
    slots = await calendar_service.find_slots(
        duration_minutes=duration_minutes,
        calendar_id=calendar_id,
        business_id=business_id,
        is_emergency=getattr(appt, "is_emergency", False),
        technician_id=getattr(appt, "technician_id", None),
    )
    if not slots:
        return []

    # For now, return a single candidate slot; the response shape allows
    # extension to multiple options in the future.
    first = slots[0]
    return [
        AppointmentSlotResponse(
            start_time=first.start,
            end_time=first.end,
        )
    ]


@router.delete("/appointments/{appointment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_appointment(
    appointment_id: str,
    business_id: str = Depends(ensure_business_active),
) -> None:
    appt = appointments_repo.get(appointment_id)
    if not appt or appt.business_id != business_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Appointment not found")

    if appt.calendar_event_id:
        calendar_id = get_calendar_id_for_business(business_id)
        await calendar_service.delete_event(
            event_id=appt.calendar_event_id,
            calendar_id=calendar_id,
        )

    appointments_repo.update(appointment_id, status="CANCELLED")


@router.get("/conversations", response_model=list[ConversationSummaryResponse])
def list_conversations(business_id: str = Depends(ensure_business_active)) -> list[ConversationSummaryResponse]:
    conversations = conversations_repo.list_for_business(business_id)
    # Best-effort mapping of conversations to a service type and whether the
    # customer has any active appointments in this business.
    svc_by_customer: dict[str, str | None] = {}
    has_appt_by_customer: dict[str, bool] = {}
    for c in conversations:
        if c.customer_id and c.customer_id not in svc_by_customer:
            appts = appointments_repo.list_for_customer(c.customer_id)
            # Filter appointments to this business.
            appts = [
                a for a in appts if getattr(a, "business_id", business_id) == business_id
            ]
            appts.sort(key=lambda a: a.start_time, reverse=True)
            svc_by_customer[c.customer_id] = appts[0].service_type if appts else None
            has_appt_by_customer[c.customer_id] = any(
                getattr(a, "status", "SCHEDULED").upper() in {"SCHEDULED", "CONFIRMED"}
                for a in appts
            )

    return [
        ConversationSummaryResponse(
            id=c.id,
            channel=c.channel,
            customer_id=c.customer_id,
            session_id=c.session_id,
            created_at=c.created_at,
            message_count=len(c.messages),
            flagged_for_review=getattr(c, "flagged_for_review", False),
            tags=getattr(c, "tags", []) or [],
            outcome=getattr(c, "outcome", None),
            service_type=(
                svc_by_customer.get(c.customer_id) if c.customer_id else None
            ),
            has_appointments=(
                has_appt_by_customer.get(c.customer_id, False)
                if c.customer_id
                else False
            ),
        )
        for c in conversations
    ]


@router.get("/customers/{customer_id}/conversations", response_model=list[ConversationSummaryResponse])
def list_customer_conversations(
    customer_id: str,
    business_id: str = Depends(ensure_business_active),
) -> list[ConversationSummaryResponse]:
    conversations = [
        c for c in conversations_repo.list_for_business(business_id) if c.customer_id == customer_id
    ]
    # Treat any active appointment for this customer in this business as
    # a "has_appointments" indicator.
    appts = [
        a
        for a in appointments_repo.list_for_customer(customer_id)
        if getattr(a, "business_id", business_id) == business_id
    ]
    has_appt = any(
        getattr(a, "status", "SCHEDULED").upper() in {"SCHEDULED", "CONFIRMED"}
        for a in appts
    )
    return [
        ConversationSummaryResponse(
            id=c.id,
            channel=c.channel,
            customer_id=c.customer_id,
            session_id=c.session_id,
            created_at=c.created_at,
            message_count=len(c.messages),
            service_type=None,
            has_appointments=has_appt,
        )
        for c in conversations
    ]


@router.get("/customers/{customer_id}/timeline", response_model=list[TimelineItem])
def customer_timeline(
    customer_id: str,
    business_id: str = Depends(ensure_business_active),
) -> list[TimelineItem]:
    """Return a simple interleaved timeline of appointments and conversations for a customer."""
    customer = customers_repo.get(customer_id)
    if not customer or getattr(customer, "business_id", business_id) != business_id:
        raise HTTPException(status_code=404, detail="Customer not found")

    items: list[TimelineItem] = []

    # Appointments for this customer in this business.
    for a in appointments_repo.list_for_customer(customer_id):
        if getattr(a, "business_id", business_id) != business_id:
            continue
        ts = getattr(a, "start_time", None) or getattr(a, "created_at", None)
        if not ts:
            continue
        title = a.service_type or "Appointment"
        status = getattr(a, "status", None)
        items.append(
            TimelineItem(
                type="appointment",
                id=a.id,
                timestamp=ts,
                title=title,
                status=status,
                channel=None,
            )
        )

    # Conversations for this customer in this business.
    for c in conversations_repo.list_for_business(business_id):
        if c.customer_id != customer_id:
            continue
        ts = getattr(c, "created_at", None)
        if not ts:
            continue
        title = c.channel.capitalize()
        status = None
        items.append(
            TimelineItem(
                type="conversation",
                id=c.id,
                timestamp=ts,
                title=title,
                status=status,
                channel=c.channel,
            )
        )

    # Sort by timestamp ascending.
    items.sort(key=lambda i: i.timestamp)
    return items


@router.get("/conversations/{conversation_id}", response_model=ConversationDetailResponse)
async def get_conversation(conversation_id: str) -> ConversationDetailResponse:
    conv = conversations_repo.get(conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    # Best-effort resolution of related service type and whether the customer
    # has any active appointments.
    service_type: str | None = None
    has_appointments = False
    if conv.customer_id:
        appts = appointments_repo.list_for_customer(conv.customer_id)
        appts.sort(key=lambda a: a.start_time, reverse=True)
        if appts:
            service_type = appts[0].service_type
            has_appointments = any(
                getattr(a, "status", "SCHEDULED").upper()
                in {"SCHEDULED", "CONFIRMED"}
                for a in appts
            )

    heuristic = _build_heuristic_qa_suggestions(conv, service_type, has_appointments)
    qa_suggestions = await _maybe_llm_enrich_qa_suggestions(
        conv, heuristic, service_type, has_appointments
    )

    return ConversationDetailResponse(
        id=conv.id,
        channel=conv.channel,
        customer_id=conv.customer_id,
        session_id=conv.session_id,
        created_at=conv.created_at,
        flagged_for_review=getattr(conv, "flagged_for_review", False),
        tags=getattr(conv, "tags", []) or [],
        outcome=getattr(conv, "outcome", None),
        notes=getattr(conv, "notes", None),
        messages=[
            ConversationMessageResponse(
                role=m.role,
                text=m.text,
                timestamp=m.timestamp,
            )
            for m in conv.messages
        ],
        service_type=service_type,
        has_appointments=has_appointments,
        qa_suggestions=qa_suggestions,
    )


@router.patch("/conversations/{conversation_id}/qa", response_model=ConversationDetailResponse)
async def update_conversation_qa(
    conversation_id: str, payload: ConversationQAUpdate
) -> ConversationDetailResponse:
    conv = conversations_repo.get(conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    if payload.flagged_for_review is not None:
        conv.flagged_for_review = payload.flagged_for_review  # type: ignore[attr-defined]
    if payload.tags is not None:
        conv.tags = payload.tags  # type: ignore[attr-defined]
    if payload.outcome is not None:
        conv.outcome = payload.outcome  # type: ignore[attr-defined]
    if payload.notes is not None:
        conv.notes = payload.notes  # type: ignore[attr-defined]

    return await get_conversation(conversation_id)
