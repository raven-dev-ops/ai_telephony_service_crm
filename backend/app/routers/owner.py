from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
import os

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from ..deps import ensure_business_active, require_owner_dashboard_auth
from ..repositories import appointments_repo, conversations_repo, customers_repo
from ..services.stt_tts import speech_service
from ..db import SQLALCHEMY_AVAILABLE, SessionLocal
from ..db_models import (
    AppointmentDB,
    Business,
    ConversationDB,
    ConversationMessageDB,
    CustomerDB,
    TechnicianDB,
)
from ..metrics import metrics
from ..services.geo_utils import derive_neighborhood_label


router = APIRouter(dependencies=[Depends(require_owner_dashboard_auth)])


class OwnerAppointmentItem(BaseModel):
    id: str
    customer_name: str
    start_time: datetime
    end_time: datetime
    is_emergency: bool


class OwnerScheduleResponse(BaseModel):
    reply_text: str
    appointments: list[OwnerAppointmentItem]


@router.get("/schedule/tomorrow", response_model=OwnerScheduleResponse)
def tomorrow_schedule(business_id: str = Depends(ensure_business_active)) -> OwnerScheduleResponse:
    """Summarize tomorrow's appointments in a voice-friendly way."""
    now = datetime.now(UTC)
    tomorrow = now.date() + timedelta(days=1)

    items: list[OwnerAppointmentItem] = []
    for appt in appointments_repo.list_for_business(business_id):
        status = getattr(appt, "status", "SCHEDULED").upper()
        if status not in {"SCHEDULED", "CONFIRMED"}:
            continue
        if appt.start_time.date() != tomorrow:
            continue
        customer = customers_repo.get(appt.customer_id)
        customer_name = customer.name if customer else "Customer"
        items.append(
            OwnerAppointmentItem(
                id=appt.id,
                customer_name=customer_name,
                start_time=appt.start_time,
                end_time=appt.end_time,
                is_emergency=appt.is_emergency,
            )
        )

    business_name: str | None = None
    if SQLALCHEMY_AVAILABLE and SessionLocal is not None:
        session_db = SessionLocal()
        try:
            row = session_db.get(Business, business_id)
        finally:
            session_db.close()
        if row is not None and getattr(row, "name", None):
            business_name = row.name

    if not items:
        if business_name:
            reply_text = f"Tomorrow you have no appointments scheduled for {business_name}."
        else:
            reply_text = "Tomorrow you have no appointments scheduled."
        return OwnerScheduleResponse(reply_text=reply_text, appointments=[])

    # Sort by start time for a consistent summary.
    items.sort(key=lambda i: i.start_time)
    count = len(items)
    first = items[0]
    time_str = first.start_time.strftime("%I:%M %p").lstrip("0")
    summary_prefix = f"Tomorrow you have {count} appointment{'s' if count > 1 else ''}"
    if business_name:
        summary_prefix = f"{summary_prefix} for {business_name}"
    summary = (
        f"{summary_prefix}. "
        f"Your first appointment starts at {time_str} with {first.customer_name}."
    )
    return OwnerScheduleResponse(reply_text=summary, appointments=items)


class OwnerScheduleAudioResponse(BaseModel):
    reply_text: str
    audio: str


@router.get("/schedule/tomorrow/audio", response_model=OwnerScheduleAudioResponse)
async def tomorrow_schedule_audio(
    business_id: str = Depends(ensure_business_active),
) -> OwnerScheduleAudioResponse:
    """Return tomorrow's schedule plus synthesized audio for the owner."""
    base = tomorrow_schedule(business_id=business_id)
    audio = await speech_service.synthesize(base.reply_text)
    return OwnerScheduleAudioResponse(reply_text=base.reply_text, audio=audio)


class OwnerTodaySummaryResponse(BaseModel):
    reply_text: str
    total_appointments: int
    emergency_appointments: int
    standard_appointments: int


@router.get("/summary/today", response_model=OwnerTodaySummaryResponse)
def today_summary(
    business_id: str = Depends(ensure_business_active),
) -> OwnerTodaySummaryResponse:
    """Summarize today's appointments by emergency vs standard for the owner."""
    now = datetime.now(UTC)
    today = now.date()

    total = 0
    emergency = 0
    for appt in appointments_repo.list_for_business(business_id):
        status = getattr(appt, "status", "SCHEDULED").upper()
        if status not in {"SCHEDULED", "CONFIRMED"}:
            continue
        if appt.start_time.date() != today:
            continue
        total += 1
        if appt.is_emergency:
            emergency += 1
    standard = total - emergency

    if total == 0:
        reply_text = "Today you have no appointments scheduled."
    else:
        reply_text = (
            f"Today you have {total} appointment{'s' if total != 1 else ''}: "
            f"{emergency} emergency and {standard} standard."
        )

    return OwnerTodaySummaryResponse(
        reply_text=reply_text,
        total_appointments=total,
        emergency_appointments=emergency,
        standard_appointments=standard,
    )


class OwnerTodaySummaryAudioResponse(BaseModel):
    reply_text: str
    audio: str


@router.get("/summary/today/audio", response_model=OwnerTodaySummaryAudioResponse)
async def today_summary_audio(
    business_id: str = Depends(ensure_business_active),
) -> OwnerTodaySummaryAudioResponse:
    """Return today's summary plus synthesized audio for the owner."""
    base = today_summary(business_id=business_id)
    audio = await speech_service.synthesize(base.reply_text)
    return OwnerTodaySummaryAudioResponse(reply_text=base.reply_text, audio=audio)


class OwnerBusinessResponse(BaseModel):
    id: str
    name: str
    max_jobs_per_day: int | None = None
    reserve_mornings_for_emergencies: bool | None = None
    travel_buffer_minutes: int | None = None
    language_code: str | None = None


class OwnerTechnician(BaseModel):
    id: str
    name: str
    color: str | None = None
    is_active: bool


@router.get("/business", response_model=OwnerBusinessResponse)
def get_owner_business(
    business_id: str = Depends(ensure_business_active),
) -> OwnerBusinessResponse:
    """Return basic business information for the current tenant.

    This is used by the owner dashboard to show which tenant's data is
    currently in view.
    """
    name = "Default Business"
    max_jobs_per_day: int | None = None
    reserve_mornings_for_emergencies: bool | None = None
    travel_buffer_minutes: int | None = None
    language_code: str | None = None
    if SQLALCHEMY_AVAILABLE and SessionLocal is not None:
        session_db = SessionLocal()
        try:
            row = session_db.get(Business, business_id)
        finally:
            session_db.close()
        if row is not None and getattr(row, "name", None):
            name = row.name
            max_jobs_per_day = getattr(row, "max_jobs_per_day", None)
            reserve_mornings_for_emergencies = getattr(
                row, "reserve_mornings_for_emergencies", None
            )
            travel_buffer_minutes = getattr(row, "travel_buffer_minutes", None)
            language_code = getattr(row, "language_code", None)
    return OwnerBusinessResponse(
        id=business_id,
        name=name,
        max_jobs_per_day=max_jobs_per_day,
        reserve_mornings_for_emergencies=reserve_mornings_for_emergencies,
        travel_buffer_minutes=travel_buffer_minutes,
        language_code=language_code,
    )


class OwnerEnvironmentResponse(BaseModel):
    environment: str


@router.get("/environment", response_model=OwnerEnvironmentResponse)
def get_owner_environment() -> OwnerEnvironmentResponse:
    """Return a simple environment label for the owner dashboard.

    This reads the ENVIRONMENT environment variable (defaulting to "dev") so
    the UI can display whether it is pointed at dev, staging, or production.
    """
    env = os.getenv("ENVIRONMENT", "dev")
    return OwnerEnvironmentResponse(environment=env)


class TenantDataDeleteResponse(BaseModel):
    business_id: str
    customers_deleted: int
    appointments_deleted: int
    conversations_deleted: int
    conversation_messages_deleted: int


@router.delete("/tenant-data", response_model=TenantDataDeleteResponse)
def delete_tenant_data(
    business_id: str = Depends(ensure_business_active),
    confirm: str = Query(
        ...,
        description='Confirmation phrase; must be "DELETE" to proceed.',
    ),
) -> TenantDataDeleteResponse:
    """Delete all customer-facing data for the current tenant.

    This is an owner-scoped, destructive operation intended for compliance
    and data-governance scenarios. It leaves the Business row itself in
    place but removes customers, appointments, conversations, and messages
    for the tenant.
    """
    if confirm != "DELETE":
        raise HTTPException(
            status_code=400,
            detail='Confirmation phrase mismatch; expected "DELETE".',
        )

    if not SQLALCHEMY_AVAILABLE or SessionLocal is None:
        raise HTTPException(
            status_code=503,
            detail="Database support is not available for tenant data deletion.",
        )

    session = SessionLocal()
    try:
        # Determine conversation IDs first so we can delete messages safely.
        conv_ids = [
            row.id
            for row in session.query(ConversationDB.id)
            .filter(ConversationDB.business_id == business_id)
            .all()
        ]

        conversation_messages_deleted = 0
        if conv_ids:
            conversation_messages_deleted = (
                session.query(ConversationMessageDB)
                .filter(ConversationMessageDB.conversation_id.in_(conv_ids))
                .delete(synchronize_session=False)
            )

        conversations_deleted = (
            session.query(ConversationDB)
            .filter(ConversationDB.business_id == business_id)
            .delete(synchronize_session=False)
        )
        appointments_deleted = (
            session.query(AppointmentDB)
            .filter(AppointmentDB.business_id == business_id)
            .delete(synchronize_session=False)
        )
        customers_deleted = (
            session.query(CustomerDB)
            .filter(CustomerDB.business_id == business_id)
            .delete(synchronize_session=False)
        )
        session.commit()
    finally:
        session.close()

    return TenantDataDeleteResponse(
        business_id=business_id,
        customers_deleted=int(customers_deleted),
        appointments_deleted=int(appointments_deleted),
        conversations_deleted=int(conversations_deleted),
        conversation_messages_deleted=int(conversation_messages_deleted),
    )


class OwnerRescheduleItem(BaseModel):
    id: str
    customer_name: str
    start_time: datetime
    end_time: datetime
    status: str


class OwnerReschedulesResponse(BaseModel):
    reply_text: str
    reschedules: list[OwnerRescheduleItem]


@router.get("/reschedules", response_model=OwnerReschedulesResponse)
def list_reschedules(
    business_id: str = Depends(ensure_business_active),
) -> OwnerReschedulesResponse:
    """Return appointments marked as pending reschedule for this tenant."""
    items: list[OwnerRescheduleItem] = []
    for appt in appointments_repo.list_for_business(business_id):
        status = getattr(appt, "status", "SCHEDULED").upper()
        if status != "PENDING_RESCHEDULE":
            continue
        customer = customers_repo.get(appt.customer_id)
        customer_name = customer.name if customer else "Customer"
        items.append(
            OwnerRescheduleItem(
                id=appt.id,
                customer_name=customer_name,
                start_time=appt.start_time,
                end_time=appt.end_time,
                status=status,
            )
        )

    if not items:
        reply_text = "You have no appointments marked for rescheduling."
    else:
        count = len(items)
        reply_text = (
            f"You have {count} appointment{'s' if count != 1 else ''} marked for rescheduling."
        )

    # Sort by start time for convenience.
    items.sort(key=lambda i: i.start_time)
    return OwnerReschedulesResponse(reply_text=reply_text, reschedules=items)


class OwnerSmsMetricsResponse(BaseModel):
    owner_messages: int
    customer_messages: int
    total_messages: int
    confirmations_via_sms: int = 0
    cancellations_via_sms: int = 0
    reschedules_via_sms: int = 0
    opt_out_events: int = 0
    opt_in_events: int = 0
    confirmation_share_via_sms: float | None = None
    cancellation_share_via_sms: float | None = None


@router.get("/sms-metrics", response_model=OwnerSmsMetricsResponse)
def owner_sms_metrics(
    business_id: str = Depends(ensure_business_active),
) -> OwnerSmsMetricsResponse:
    """Return SMS usage metrics for the current tenant."""
    per = metrics.sms_by_business.get(business_id)
    owner = per.sms_sent_owner if per else 0
    customer = per.sms_sent_customer if per else 0
    total = per.sms_sent_total if per else 0
    confirmations = per.sms_confirmations_via_sms if per else 0
    cancellations = per.sms_cancellations_via_sms if per else 0
    reschedules = per.sms_reschedules_via_sms if per else 0
    opt_outs = per.sms_opt_out_events if per else 0
    opt_ins = per.sms_opt_in_events if per else 0

    # Approximate shares by comparing SMS-driven confirmations/cancellations
    # to all confirmed/cancelled appointments for this tenant.
    total_confirmed = 0
    total_cancelled = 0
    for appt in appointments_repo.list_for_business(business_id):
        status = (getattr(appt, "status", "SCHEDULED") or "").upper()
        if status == "CONFIRMED":
            total_confirmed += 1
        elif status == "CANCELLED":
            total_cancelled += 1
    confirmation_share = (
        float(confirmations) / float(total_confirmed)
        if total_confirmed > 0 and confirmations > 0
        else None
    )
    cancellation_share = (
        float(cancellations) / float(total_cancelled)
        if total_cancelled > 0 and cancellations > 0
        else None
    )
    return OwnerSmsMetricsResponse(
        owner_messages=owner,
        customer_messages=customer,
        total_messages=total,
        confirmations_via_sms=confirmations,
        cancellations_via_sms=cancellations,
        reschedules_via_sms=reschedules,
        opt_out_events=opt_outs,
        opt_in_events=opt_ins,
        confirmation_share_via_sms=confirmation_share,
        cancellation_share_via_sms=cancellation_share,
    )


class OwnerServiceMixResponse(BaseModel):
    total_appointments_30d: int
    emergency_appointments_30d: int
    service_type_counts_30d: dict[str, int]
    emergency_service_type_counts_30d: dict[str, int]


class OwnerPipelineStage(BaseModel):
    stage: str
    count: int
    estimated_value_total: float


class OwnerPipelineResponse(BaseModel):
    stages: list[OwnerPipelineStage]
    total_estimated_value: float


class OwnerQuoteStage(BaseModel):
    stage: str
    count: int
    estimated_value_total: float
    quoted_value_total: float


class OwnerQuoteStatusBucket(BaseModel):
    status: str
    count: int
    quoted_value_total: float


class OwnerQuotesResponse(BaseModel):
    total_quotes: int
    total_quote_value: float
    total_quoted_value: float
    quote_customers: int
    quote_customers_converted: int
    stages: list[OwnerQuoteStage]
    by_status: list[OwnerQuoteStatusBucket]


@router.get("/service-mix", response_model=OwnerServiceMixResponse)
def owner_service_mix(
    business_id: str = Depends(ensure_business_active),
    days: int = Query(30, ge=1, le=90),
) -> OwnerServiceMixResponse:
    """Summarize service mix for the last N days for this tenant.

    This focuses on which service types are driving work and emergencies so
    the owner can see where demand is concentrated.
    """
    now = datetime.now(UTC)
    window = now - timedelta(days=days)

    total = 0
    emergency = 0
    service_counts: dict[str, int] = {}
    emergency_counts: dict[str, int] = {}

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
        is_emergency = bool(getattr(appt, "is_emergency", False))
        if is_emergency:
            emergency += 1

        service_type = getattr(appt, "service_type", None) or "unspecified"
        service_counts[service_type] = service_counts.get(service_type, 0) + 1
        if is_emergency:
            emergency_counts[service_type] = emergency_counts.get(service_type, 0) + 1

    return OwnerServiceMixResponse(
        total_appointments_30d=total,
        emergency_appointments_30d=emergency,
        service_type_counts_30d=service_counts,
        emergency_service_type_counts_30d=emergency_counts,
    )


@router.get("/pipeline", response_model=OwnerPipelineResponse)
def owner_pipeline(
    business_id: str = Depends(ensure_business_active),
    days: int = Query(30, ge=1, le=180),
) -> OwnerPipelineResponse:
    """Summarize simple sales pipeline by appointment job_stage.

    This looks at appointments in the last N days for the current tenant and
    aggregates counts and estimated value by job_stage.
    """
    now = datetime.now(UTC)
    window = now - timedelta(days=days)

    # Bucket by stage; treat missing stages as "Unspecified".
    buckets: dict[str, dict[str, float]] = {}
    total_estimated = 0.0

    for appt in appointments_repo.list_for_business(business_id):
        start_time = getattr(appt, "start_time", None)
        if not start_time:
            continue
        if start_time < window or start_time > now:
            continue
        stage = (getattr(appt, "job_stage", None) or "Unspecified").strip() or "Unspecified"
        est_value = getattr(appt, "estimated_value", None)
        value = float(est_value) if est_value is not None else 0.0
        bucket = buckets.setdefault(stage, {"count": 0.0, "value": 0.0})
        bucket["count"] += 1
        bucket["value"] += value
        total_estimated += value

    stages: list[OwnerPipelineStage] = []
    for stage, agg in buckets.items():
        stages.append(
            OwnerPipelineStage(
                stage=stage,
                count=int(agg["count"]),
                estimated_value_total=agg["value"],
            )
        )

    # Sort by a simple heuristic: high value first.
    stages.sort(key=lambda s: s.estimated_value_total, reverse=True)

    return OwnerPipelineResponse(
        stages=stages,
        total_estimated_value=total_estimated,
    )


def _is_quote_stage(stage: str) -> bool:
    text = stage.lower()
    return (
        "quote" in text
        or "estimate" in text
        or "proposal" in text
        or "lead" in text
    )


def _is_booked_or_completed_stage(stage: str) -> bool:
    text = stage.lower()
    return (
        "booked" in text
        or "scheduled" in text
        or "complete" in text
        or "completed" in text
    )


@router.get("/quotes", response_model=OwnerQuotesResponse)
def owner_quotes(
    business_id: str = Depends(ensure_business_active),
    days: int = Query(30, ge=1, le=365),
) -> OwnerQuotesResponse:
    """Summarize quote volume and value for this tenant.

    This focuses on appointments whose job_stage looks like a lead/quote/
    estimate and reports total value plus a simple conversion indicator
    based on later booked/completed stages for the same customer.
    """
    now = datetime.now(UTC)
    window = now - timedelta(days=days)

    # Consider only recent, active appointments.
    relevant = []
    for appt in appointments_repo.list_for_business(business_id):
        start_time = getattr(appt, "start_time", None)
        if not start_time or start_time < window or start_time > now:
            continue
        status = getattr(appt, "status", "SCHEDULED").upper()
        if status not in {"SCHEDULED", "CONFIRMED"}:
            continue
        relevant.append(appt)

    # Aggregate per quote stage and track which customers have quotes and
    # booked/completed work in the same window.
    stage_buckets: dict[str, dict[str, float]] = {}
    status_buckets: dict[str, dict[str, float]] = {}
    quote_customers: set[str] = set()
    converted_customers: set[str] = set()

    for appt in relevant:
        raw_stage = getattr(appt, "job_stage", None) or "Unspecified"
        stage = raw_stage.strip() or "Unspecified"
        est_value = getattr(appt, "estimated_value", None)
        est_booked_value = float(est_value) if est_value is not None else 0.0
        quoted_val = getattr(appt, "quoted_value", None)
        q_value = float(quoted_val) if quoted_val is not None else 0.0
        customer_id = getattr(appt, "customer_id", None)

        if _is_quote_stage(stage):
            bucket = stage_buckets.setdefault(
                stage, {"count": 0.0, "est_value": 0.0, "quoted_value": 0.0}
            )
            bucket["count"] += 1
            bucket["est_value"] += est_booked_value
            bucket["quoted_value"] += q_value or est_booked_value
            # Quote-status funnel: treat missing status as a basic "QUOTED" state.
            raw_status = getattr(appt, "quote_status", None) or "QUOTED"
            status_norm = raw_status.strip().upper() or "QUOTED"
            sb = status_buckets.setdefault(
                status_norm, {"count": 0.0, "quoted_value": 0.0}
            )
            sb["count"] += 1
            sb["quoted_value"] += q_value or est_booked_value
            if customer_id:
                quote_customers.add(customer_id)

    # Conversion: customers who have both quote-stage and booked/completed
    # appointments in the same window.
    for appt in relevant:
        customer_id = getattr(appt, "customer_id", None)
        if not customer_id or customer_id not in quote_customers:
            continue
        raw_stage = getattr(appt, "job_stage", None) or "Unspecified"
        stage = raw_stage.strip() or "Unspecified"
        if _is_booked_or_completed_stage(stage):
            converted_customers.add(customer_id)

    stages: list[OwnerQuoteStage] = []
    total_quotes = 0
    total_value = 0.0
    total_quoted_value = 0.0
    for stage, agg in stage_buckets.items():
        count = int(agg["count"])
        est_total = agg.get("est_value", 0.0)
        quoted_total = agg.get("quoted_value", est_total)
        total_quotes += count
        total_value += est_total
        total_quoted_value += quoted_total
        stages.append(
            OwnerQuoteStage(
                stage=stage,
                count=count,
                estimated_value_total=est_total,
                quoted_value_total=quoted_total,
            )
        )

    # Order quote stages by total value, descending.
    stages.sort(key=lambda s: s.estimated_value_total, reverse=True)

    by_status: list[OwnerQuoteStatusBucket] = []
    for status, agg in status_buckets.items():
        by_status.append(
            OwnerQuoteStatusBucket(
                status=status,
                count=int(agg.get("count", 0.0)),
                quoted_value_total=agg.get("quoted_value", 0.0),
            )
        )
    by_status.sort(key=lambda s: s.quoted_value_total, reverse=True)

    return OwnerQuotesResponse(
        total_quotes=total_quotes,
        total_quote_value=total_value,
        total_quoted_value=total_quoted_value,
        quote_customers=len(quote_customers),
        quote_customers_converted=len(converted_customers),
        stages=stages,
        by_status=by_status,
    )


class OwnerTwilioMetricsResponse(BaseModel):
    voice_requests: int
    voice_errors: int
    sms_requests: int
    sms_errors: int


@router.get("/twilio-metrics", response_model=OwnerTwilioMetricsResponse)
def owner_twilio_metrics(
    business_id: str = Depends(ensure_business_active),
    ) -> OwnerTwilioMetricsResponse:
    """Return Twilio webhook metrics for the current tenant.

    This is a tenant-scoped view over the same in-process metrics that power
    the admin Twilio health endpoint, so owners can see whether their own
    voice/SMS webhooks are healthy.
    """
    per = metrics.twilio_by_business.get(business_id)
    voice_requests = per.voice_requests if per else 0
    voice_errors = per.voice_errors if per else 0
    sms_requests = per.sms_requests if per else 0
    sms_errors = per.sms_errors if per else 0
    return OwnerTwilioMetricsResponse(
        voice_requests=voice_requests,
        voice_errors=voice_errors,
        sms_requests=sms_requests,
        sms_errors=sms_errors,
    )


class OwnerDailyWorkloadItem(BaseModel):
    date: date
    total_appointments: int
    emergency_appointments: int
    standard_appointments: int


class OwnerWorkloadNextResponse(BaseModel):
    days: int
    items: list[OwnerDailyWorkloadItem]


@router.get("/workload/next", response_model=OwnerWorkloadNextResponse)
def owner_workload_next(
    business_id: str = Depends(ensure_business_active),
    days: int = Query(7, ge=1, le=30),
) -> OwnerWorkloadNextResponse:
    """Summarize workload over the next N days for this tenant.

    This aggregates SCHEDULED/CONFIRMED appointments per calendar day,
    broken out by emergency vs standard, starting from today.
    """
    now = datetime.now(UTC)
    start_date = now.date()
    end_date = start_date + timedelta(days=days - 1)

    totals: dict[date, dict[str, int]] = {}
    for appt in appointments_repo.list_for_business(business_id):
        status = getattr(appt, "status", "SCHEDULED").upper()
        if status not in {"SCHEDULED", "CONFIRMED"}:
            continue
        appt_date = getattr(appt, "start_time", None)
        if not appt_date:
            continue
        appt_day = appt_date.date()
        if appt_day < start_date or appt_day > end_date:
            continue
        bucket = totals.setdefault(
            appt_day,
            {"total": 0, "emergency": 0},
        )
        bucket["total"] += 1
        if bool(getattr(appt, "is_emergency", False)):
            bucket["emergency"] += 1

    items: list[OwnerDailyWorkloadItem] = []
    for offset in range(days):
        day = start_date + timedelta(days=offset)
        bucket = totals.get(day, {"total": 0, "emergency": 0})
        total = bucket["total"]
        emergency = bucket["emergency"]
        standard = total - emergency
        items.append(
            OwnerDailyWorkloadItem(
                date=day,
                total_appointments=total,
                emergency_appointments=emergency,
                standard_appointments=standard,
            )
        )

    return OwnerWorkloadNextResponse(days=days, items=items)


class OwnerLeadSourceItem(BaseModel):
    lead_source: str
    appointments: int
    estimated_value_total: float


class OwnerLeadSourceResponse(BaseModel):
    items: list[OwnerLeadSourceItem]
    total_appointments: int
    total_estimated_value: float


class OwnerServiceTypeEconomicsItem(BaseModel):
    service_type: str
    appointments: int
    estimated_value_total: float
    average_ticket: float


class OwnerServiceTypeEconomicsResponse(BaseModel):
    window_days: int
    items: list[OwnerServiceTypeEconomicsItem]


class OwnerCohortBucket(BaseModel):
    cohort_label: str
    customers: int
    repeat_customers: int
    repeat_rate: float


class OwnerEconomicsSummary(BaseModel):
    total_appointments: int
    total_estimated_value: float
    emergency_appointments: int
    emergency_estimated_value: float
    average_ticket: float
    average_ticket_emergency: float
    average_ticket_standard: float
    repeat_customer_share: float
    new_customer_share: float
    repeat_appointments: int
    new_appointments: int
    repeat_estimated_value: float
    new_estimated_value: float


class OwnerCustomerAnalyticsResponse(BaseModel):
    window_days: int
    total_customers: int
    repeat_customers: int
    cohorts: list[OwnerCohortBucket]
    economics: OwnerEconomicsSummary


class OwnerNeighborhoodItem(BaseModel):
    label: str
    customers: int
    appointments: int
    emergency_appointments: int
    estimated_value_total: float


class OwnerNeighborhoodResponse(BaseModel):
    window_days: int
    items: list[OwnerNeighborhoodItem]


class OwnerTimeToBookBucket(BaseModel):
    channel: str
    samples: int
    average_minutes: float


class OwnerTimeToBookResponse(BaseModel):
    window_days: int
    overall_samples: int
    overall_average_minutes: float
    by_channel: list[OwnerTimeToBookBucket]


class OwnerConversionFunnelChannel(BaseModel):
    channel: str
    leads: int
    booked_appointments: int
    conversion_rate: float
    average_time_to_book_minutes: float


class OwnerConversionFunnelResponse(BaseModel):
    window_days: int
    overall_leads: int
    overall_booked: int
    overall_conversion_rate: float
    channels: list[OwnerConversionFunnelChannel]


class OwnerDataCompletenessResponse(BaseModel):
    window_days: int
    total_customers: int
    customers_with_email: int
    customers_with_address: int
    customers_complete: int
    total_appointments: int
    appointments_with_service_type: int
    appointments_with_estimated_value: int
    appointments_with_lead_source: int
    appointments_complete: int
    customer_completeness_score: float
    appointment_completeness_score: float


class OwnerCallbackItem(BaseModel):
    phone: str
    first_seen: datetime
    last_seen: datetime
    attempts: int
    channel: str
    lead_source: str | None = None
    status: str
    last_result: str | None = None
    reason: str | None = None


class OwnerCallbackQueueResponse(BaseModel):
    items: list[OwnerCallbackItem]


class OwnerCallbackLeadSourceSummary(BaseModel):
    lead_source: str
    total: int
    pending: int
    completed: int
    unreachable: int


class OwnerCallbackSummaryResponse(BaseModel):
    total_callbacks: int
    pending: int
    completed: int
    unreachable: int
    lead_sources: list[OwnerCallbackLeadSourceSummary]
    missed_callbacks: int
    partial_intake_callbacks: int


@router.get("/lead-sources", response_model=OwnerLeadSourceResponse)
def owner_lead_sources(
    business_id: str = Depends(ensure_business_active),
    days: int = Query(30, ge=1, le=365),
    ) -> OwnerLeadSourceResponse:
    """Summarize appointment volume and value by lead_source for this tenant."""
    now = datetime.now(UTC)
    window = now - timedelta(days=days)

    buckets: dict[str, dict[str, float]] = {}
    total_count = 0
    total_value = 0.0

    for appt in appointments_repo.list_for_business(business_id):
        start_time = getattr(appt, "start_time", None)
        if not start_time or start_time < window or start_time > now:
            continue
        status = getattr(appt, "status", "SCHEDULED").upper()
        if status not in {"SCHEDULED", "CONFIRMED"}:
            continue

        source = (getattr(appt, "lead_source", None) or "unspecified").strip() or "unspecified"
        est_value = getattr(appt, "estimated_value", None)
        value = float(est_value) if est_value is not None else 0.0
        bucket = buckets.setdefault(source, {"count": 0.0, "value": 0.0})
        bucket["count"] += 1
        bucket["value"] += value
        total_count += 1
        total_value += value

    items: list[OwnerLeadSourceItem] = []
    for source, agg in buckets.items():
        items.append(
            OwnerLeadSourceItem(
                lead_source=source,
                appointments=int(agg["count"]),
                estimated_value_total=agg["value"],
            )
        )

    # Sort by estimated value descending.
    items.sort(key=lambda i: i.estimated_value_total, reverse=True)

    return OwnerLeadSourceResponse(
        items=items,
        total_appointments=total_count,
        total_estimated_value=total_value,
    )


@router.get("/service-economics", response_model=OwnerServiceTypeEconomicsResponse)
def owner_service_economics(
    business_id: str = Depends(ensure_business_active),
    days: int = Query(30, ge=1, le=365),
) -> OwnerServiceTypeEconomicsResponse:
    """Summarize appointment value and average ticket by service type.

    Only SCHEDULED/CONFIRMED appointments in the last N days are included.
    """
    now = datetime.now(UTC)
    window = now - timedelta(days=days)

    buckets: dict[str, dict[str, float]] = {}
    for appt in appointments_repo.list_for_business(business_id):
        start_time = getattr(appt, "start_time", None)
        if not start_time or start_time < window or start_time > now:
            continue
        status = getattr(appt, "status", "SCHEDULED").upper()
        if status not in {"SCHEDULED", "CONFIRMED"}:
            continue
        svc = getattr(appt, "service_type", None) or "unspecified"
        est_value = getattr(appt, "estimated_value", None)
        value = float(est_value) if est_value is not None else 0.0
        bucket = buckets.setdefault(
            svc,
            {
                "count": 0.0,
                "value": 0.0,
            },
        )
        bucket["count"] += 1.0
        bucket["value"] += value

    items: list[OwnerServiceTypeEconomicsItem] = []
    for svc, agg in buckets.items():
        count = int(agg["count"])
        total_val = agg["value"]
        avg = total_val / count if count > 0 else 0.0
        items.append(
            OwnerServiceTypeEconomicsItem(
                service_type=svc,
                appointments=count,
                estimated_value_total=total_val,
                average_ticket=avg,
            )
        )
    # Sort by total value descending.
    items.sort(key=lambda i: i.estimated_value_total, reverse=True)

    return OwnerServiceTypeEconomicsResponse(window_days=days, items=items)


@router.get("/customers/analytics", response_model=OwnerCustomerAnalyticsResponse)
def owner_customer_analytics(
    business_id: str = Depends(ensure_business_active),
    days: int = Query(365, ge=30, le=3650),
) -> OwnerCustomerAnalyticsResponse:
    """Return simple cohort, repeat-customer, and economics analytics.

    - Cohorts are grouped by the year-month of a customer's first appointment
      within the analysis window.
    - Repeat customers are those with 2+ appointments in the window.
    - Economics uses estimated_value when present and treats missing values
      as zero for averages.
    """
    now = datetime.now(UTC)
    window_start = now - timedelta(days=days)

    # Collect appointments in the window per customer.
    per_customer: dict[str, list] = {}
    for appt in appointments_repo.list_for_business(business_id):
        start_time = getattr(appt, "start_time", None)
        if not start_time or start_time < window_start or start_time > now:
            continue
        customer_id = getattr(appt, "customer_id", None)
        if not customer_id:
            continue
        per_customer.setdefault(customer_id, []).append(appt)

    total_customers = len(per_customer)
    repeat_customers = 0

    # Cohorts keyed by "YYYY-MM".
    cohorts_raw: dict[str, dict[str, float]] = {}

    # Economics aggregates.
    total_appointments = 0
    total_estimated_value = 0.0
    emergency_appointments = 0
    emergency_estimated_value = 0.0

    repeat_appt_count = 0
    new_appt_count = 0
    repeat_appt_value = 0.0
    new_appt_value = 0.0

    for customer_id, appts in per_customer.items():
        appts_sorted = sorted(
            appts, key=lambda a: getattr(a, "start_time", now)
        )
        if len(appts_sorted) >= 2:
            repeat_customers += 1

        first = appts_sorted[0]
        first_time = getattr(first, "start_time", window_start)
        label = f"{first_time.year:04d}-{first_time.month:02d}"
        bucket = cohorts_raw.setdefault(
            label,
            {"customers": 0.0, "repeat_customers": 0.0},
        )
        bucket["customers"] += 1.0
        if len(appts_sorted) >= 2:
            bucket["repeat_customers"] += 1.0

        for appt in appts_sorted:
            total_appointments += 1
            est_value = getattr(appt, "estimated_value", None)
            value = float(est_value) if est_value is not None else 0.0
            total_estimated_value += value

            is_emergency = bool(getattr(appt, "is_emergency", False))
            if is_emergency:
                emergency_appointments += 1
                emergency_estimated_value += value

            if len(appts_sorted) >= 2:
                repeat_appt_count += 1
                repeat_appt_value += value
            else:
                new_appt_count += 1
                new_appt_value += value

    cohorts: list[OwnerCohortBucket] = []
    for label, agg in cohorts_raw.items():
        customers_count = int(agg["customers"])
        repeat_count = int(agg["repeat_customers"])
        rate = (repeat_count / customers_count) if customers_count > 0 else 0.0
        cohorts.append(
            OwnerCohortBucket(
                cohort_label=label,
                customers=customers_count,
                repeat_customers=repeat_count,
                repeat_rate=rate,
            )
        )
    cohorts.sort(key=lambda c: c.cohort_label, reverse=True)

    avg_ticket = (
        total_estimated_value / total_appointments if total_appointments > 0 else 0.0
    )
    standard_appointments = total_appointments - emergency_appointments
    standard_value = total_estimated_value - emergency_estimated_value
    avg_emergency = (
        emergency_estimated_value / emergency_appointments
        if emergency_appointments > 0
        else 0.0
    )
    avg_standard = (
        standard_value / standard_appointments if standard_appointments > 0 else 0.0
    )

    total_appts_for_share = repeat_appt_count + new_appt_count
    repeat_share = (
        repeat_appt_count / total_appts_for_share if total_appts_for_share > 0 else 0.0
    )
    new_share = (
        new_appt_count / total_appts_for_share if total_appts_for_share > 0 else 0.0
    )

    economics = OwnerEconomicsSummary(
        total_appointments=total_appointments,
        total_estimated_value=total_estimated_value,
        emergency_appointments=emergency_appointments,
        emergency_estimated_value=emergency_estimated_value,
        average_ticket=avg_ticket,
        average_ticket_emergency=avg_emergency,
        average_ticket_standard=avg_standard,
        repeat_customer_share=repeat_share,
        new_customer_share=new_share,
        repeat_appointments=repeat_appt_count,
        new_appointments=new_appt_count,
        repeat_estimated_value=repeat_appt_value,
        new_estimated_value=new_appt_value,
    )

    return OwnerCustomerAnalyticsResponse(
        window_days=days,
        total_customers=total_customers,
        repeat_customers=repeat_customers,
        cohorts=cohorts,
        economics=economics,
    )


@router.get("/neighborhoods", response_model=OwnerNeighborhoodResponse)
def owner_neighborhoods(
    business_id: str = Depends(ensure_business_active),
    days: int = Query(90, ge=7, le=365),
) -> OwnerNeighborhoodResponse:
    """Summarize appointment volume/value by coarse neighborhood label.

    Neighborhood labels are derived from customer addresses and are intended
    only for aggregated "heatmap" style analytics, not precise geolocation.
    """
    now = datetime.now(UTC)
    window_start = now - timedelta(days=days)

    buckets: dict[str, dict[str, float]] = {}
    for appt in appointments_repo.list_for_business(business_id):
        start_time = getattr(appt, "start_time", None)
        if not start_time or start_time < window_start or start_time > now:
            continue
        status = getattr(appt, "status", "SCHEDULED").upper()
        if status not in {"SCHEDULED", "CONFIRMED"}:
            continue
        customer_id = getattr(appt, "customer_id", None)
        if not customer_id:
            continue
        customer = customers_repo.get(customer_id)
        if not customer:
            continue
        addr = getattr(customer, "address", None)
        label = derive_neighborhood_label(addr)

        est_value = getattr(appt, "estimated_value", None)
        value = float(est_value) if est_value is not None else 0.0
        is_emergency = bool(getattr(appt, "is_emergency", False))

        bucket = buckets.setdefault(
            label,
            {
                "customers": set(),  # type: ignore[dict-item]
                "appointments": 0.0,
                "emergencies": 0.0,
                "value": 0.0,
            },
        )
        bucket["customers"].add(customer_id)  # type: ignore[index]
        bucket["appointments"] += 1.0
        if is_emergency:
            bucket["emergencies"] += 1.0
        bucket["value"] += value

    items: list[OwnerNeighborhoodItem] = []
    for label, agg in buckets.items():
        customers = len(agg["customers"])  # type: ignore[index]
        appts = int(agg["appointments"])
        emergencies = int(agg["emergencies"])
        value = float(agg["value"])
        items.append(
            OwnerNeighborhoodItem(
                label=label,
                customers=customers,
                appointments=appts,
                emergency_appointments=emergencies,
                estimated_value_total=value,
            )
        )
    # Sort by total value descending.
    items.sort(key=lambda i: i.estimated_value_total, reverse=True)

    return OwnerNeighborhoodResponse(window_days=days, items=items)


@router.get("/time-to-book", response_model=OwnerTimeToBookResponse)
def owner_time_to_book(
    business_id: str = Depends(ensure_business_active),
    days: int = Query(90, ge=7, le=365),
) -> OwnerTimeToBookResponse:
    """Estimate time-to-book from initial contact to first appointment.

    For each customer with at least one conversation and one appointment in
    the window, this computes the time between the earliest conversation and
    the first subsequent SCHEDULED/CONFIRMED appointment. Results are grouped
    by initial channel.
    """
    now = datetime.now(UTC)
    window_start = now - timedelta(days=days)

    # Earliest conversation per customer in the window, keyed by customer and
    # channel.
    first_contact: dict[str, tuple[datetime, str]] = {}
    for conv in conversations_repo.list_for_business(business_id):
        created_at = getattr(conv, "created_at", None)
        if not created_at:
            continue
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        if created_at < window_start or created_at > now:
            continue
        customer_id = getattr(conv, "customer_id", None)
        if not customer_id:
            continue
        # Only keep the earliest conversation seen for this customer.
        existing = first_contact.get(customer_id)
        if existing is None or created_at < existing[0]:
            first_contact[customer_id] = (created_at, conv.channel)

    if not first_contact:
        return OwnerTimeToBookResponse(
            window_days=days,
            overall_samples=0,
            overall_average_minutes=0.0,
            by_channel=[],
        )

    overall_samples = 0
    overall_minutes = 0.0
    per_channel: dict[str, dict[str, float]] = {}

    for customer_id, (first_ts, channel) in first_contact.items():
        appts = [
            a
            for a in appointments_repo.list_for_customer(customer_id)
            if getattr(a, "business_id", business_id) == business_id
        ]
        # Consider only appointments after the initial contact.
        candidates = []
        for appt in appts:
            start_time = getattr(appt, "start_time", None)
            if not start_time:
                continue
            if start_time <= first_ts or start_time > now:
                continue
            status = getattr(appt, "status", "SCHEDULED").upper()
            if status not in {"SCHEDULED", "CONFIRMED"}:
                continue
            candidates.append(appt)
        if not candidates:
            continue
        candidates.sort(key=lambda a: a.start_time)
        first_appt = candidates[0]
        delta = first_appt.start_time - first_ts
        minutes = max(delta.total_seconds() / 60.0, 0.0)

        overall_samples += 1
        overall_minutes += minutes

        bucket = per_channel.setdefault(
            channel,
            {
                "samples": 0.0,
                "minutes": 0.0,
            },
        )
        bucket["samples"] += 1.0
        bucket["minutes"] += minutes

    by_channel: list[OwnerTimeToBookBucket] = []
    for channel, agg in per_channel.items():
        samples = int(agg["samples"])
        avg = agg["minutes"] / samples if samples > 0 else 0.0
        by_channel.append(
            OwnerTimeToBookBucket(
                channel=channel,
                samples=samples,
                average_minutes=avg,
            )
        )
    by_channel.sort(key=lambda b: b.average_minutes)

    overall_avg = overall_minutes / overall_samples if overall_samples > 0 else 0.0

    return OwnerTimeToBookResponse(
        window_days=days,
        overall_samples=overall_samples,
        overall_average_minutes=overall_avg,
        by_channel=by_channel,
    )


@router.get("/conversion-funnel", response_model=OwnerConversionFunnelResponse)
def owner_conversion_funnel(
    business_id: str = Depends(ensure_business_active),
    days: int = Query(90, ge=7, le=365),
) -> OwnerConversionFunnelResponse:
    now = datetime.now(UTC)
    window_start = now - timedelta(days=days)

    first_contact: dict[str, tuple[datetime, str]] = {}
    for conv in conversations_repo.list_for_business(business_id):
        created_at = getattr(conv, "created_at", None)
        if not created_at:
            continue
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        if created_at < window_start or created_at > now:
            continue
        customer_id = getattr(conv, "customer_id", None)
        if not customer_id:
            continue
        existing = first_contact.get(customer_id)
        if existing is None or created_at < existing[0]:
            first_contact[customer_id] = (created_at, conv.channel)

    if not first_contact:
        return OwnerConversionFunnelResponse(
            window_days=days,
            overall_leads=0,
            overall_booked=0,
            overall_conversion_rate=0.0,
            channels=[],
        )

    per_channel_leads: dict[str, int] = {}
    per_channel_booked: dict[str, int] = {}
    per_channel_minutes: dict[str, float] = {}

    overall_leads = 0
    overall_booked = 0
    overall_minutes = 0.0

    for customer_id, (first_ts, channel) in first_contact.items():
        overall_leads += 1
        per_channel_leads[channel] = per_channel_leads.get(channel, 0) + 1

        appts = [
            a
            for a in appointments_repo.list_for_customer(customer_id)
            if getattr(a, "business_id", business_id) == business_id
        ]
        candidates = []
        for appt in appts:
            start_time = getattr(appt, "start_time", None)
            if not start_time:
                continue
            if start_time <= first_ts or start_time > now:
                continue
            status = getattr(appt, "status", "SCHEDULED").upper()
            if status not in {"SCHEDULED", "CONFIRMED"}:
                continue
            candidates.append(appt)
        if not candidates:
            continue
        candidates.sort(key=lambda a: a.start_time)
        first_appt = candidates[0]
        delta = first_appt.start_time - first_ts
        minutes = max(delta.total_seconds() / 60.0, 0.0)

        overall_booked += 1
        overall_minutes += minutes

        per_channel_booked[channel] = per_channel_booked.get(channel, 0) + 1
        per_channel_minutes[channel] = per_channel_minutes.get(channel, 0.0) + minutes

    channels: list[OwnerConversionFunnelChannel] = []
    for channel, leads in per_channel_leads.items():
        booked = per_channel_booked.get(channel, 0)
        conv_rate = float(booked) / float(leads) if leads > 0 else 0.0
        avg_minutes = (
            per_channel_minutes.get(channel, 0.0) / float(booked)
            if booked > 0
            else 0.0
        )
        channels.append(
            OwnerConversionFunnelChannel(
                channel=channel,
                leads=leads,
                booked_appointments=booked,
                conversion_rate=conv_rate,
                average_time_to_book_minutes=avg_minutes,
            )
        )
    channels.sort(key=lambda c: c.channel)

    overall_conversion = (
        float(overall_booked) / float(overall_leads)
        if overall_leads > 0
        else 0.0
    )

    return OwnerConversionFunnelResponse(
        window_days=days,
        overall_leads=overall_leads,
        overall_booked=overall_booked,
        overall_conversion_rate=overall_conversion,
        channels=channels,
    )


@router.get("/data-completeness", response_model=OwnerDataCompletenessResponse)
def owner_data_completeness(
    business_id: str = Depends(ensure_business_active),
    days: int = Query(365, ge=1, le=3650),
) -> OwnerDataCompletenessResponse:
    """Summarize basic CRM data completeness for this tenant.

    Customer completeness looks at email and address fields for all
    customers in the tenant. Appointment completeness looks at service
    type, estimated_value, and lead_source for appointments in the
    requested window.
    """
    now = datetime.now(UTC)
    window_start = now - timedelta(days=days)

    total_customers = 0
    customers_with_email = 0
    customers_with_address = 0
    customers_complete = 0
    for cust in customers_repo.list_for_business(business_id):
        total_customers += 1
        email = (getattr(cust, "email", None) or "").strip()
        address = (getattr(cust, "address", None) or "").strip()
        has_email = bool(email)
        has_address = bool(address)
        if has_email:
            customers_with_email += 1
        if has_address:
            customers_with_address += 1
        if has_email and has_address:
            customers_complete += 1

    total_appointments = 0
    appts_with_service_type = 0
    appts_with_estimated_value = 0
    appts_with_lead_source = 0
    appts_complete = 0

    for appt in appointments_repo.list_for_business(business_id):
        start_time = getattr(appt, "start_time", None)
        if not start_time or start_time < window_start or start_time > now:
            continue
        total_appointments += 1
        service_type = (getattr(appt, "service_type", None) or "").strip()
        lead_source = (getattr(appt, "lead_source", None) or "").strip()
        est_value = getattr(appt, "estimated_value", None)

        has_service_type = bool(service_type)
        has_lead_source = bool(lead_source)
        has_estimated_value = est_value is not None

        if has_service_type:
            appts_with_service_type += 1
        if has_estimated_value:
            appts_with_estimated_value += 1
        if has_lead_source:
            appts_with_lead_source += 1
        if has_service_type and has_lead_source and has_estimated_value:
            appts_complete += 1

    customer_score = (
        float(customers_complete) / float(total_customers)
        if total_customers > 0
        else 0.0
    )
    appointment_score = (
        float(appts_complete) / float(total_appointments)
        if total_appointments > 0
        else 0.0
    )

    return OwnerDataCompletenessResponse(
        window_days=days,
        total_customers=total_customers,
        customers_with_email=customers_with_email,
        customers_with_address=customers_with_address,
        customers_complete=customers_complete,
        total_appointments=total_appointments,
        appointments_with_service_type=appts_with_service_type,
        appointments_with_estimated_value=appts_with_estimated_value,
        appointments_with_lead_source=appts_with_lead_source,
        appointments_complete=appts_complete,
        customer_completeness_score=customer_score,
        appointment_completeness_score=appointment_score,
    )


@router.get("/callbacks", response_model=OwnerCallbackQueueResponse)
def owner_callbacks(
    business_id: str = Depends(ensure_business_active),
) -> OwnerCallbackQueueResponse:
    """Return a simple per-tenant callback queue derived from missed calls."""
    queue = getattr(metrics, "callbacks_by_business", {}).get(business_id, {}) or {}
    items: list[OwnerCallbackItem] = []
    for phone, item in queue.items():
        status = (getattr(item, "status", "PENDING") or "PENDING").upper()
        if status != "PENDING":
            # The queue view focuses on pending callbacks; resolved items are
            # available via the summary endpoint.
            continue
        items.append(
            OwnerCallbackItem(
                phone=item.phone,
                first_seen=item.first_seen,
                last_seen=item.last_seen,
                attempts=item.count,
                channel=item.channel,
                lead_source=item.lead_source,
                status=status,
                last_result=getattr(item, "last_result", None),
                reason=getattr(item, "reason", None),
            )
        )
    items.sort(key=lambda i: i.last_seen, reverse=True)
    return OwnerCallbackQueueResponse(items=items)


@router.delete("/callbacks/{phone}", status_code=204)
def clear_owner_callback(
    phone: str,
    business_id: str = Depends(ensure_business_active),
) -> None:
    """Remove a phone number from the callback queue for this tenant."""
    queue = getattr(metrics, "callbacks_by_business", {}).get(business_id)
    if queue and phone in queue:
        queue.pop(phone, None)


class OwnerCallbackUpdateRequest(BaseModel):
    status: str
    result: str | None = None


@router.patch("/callbacks/{phone}", response_model=OwnerCallbackItem)
def update_owner_callback(
    phone: str,
    payload: OwnerCallbackUpdateRequest,
    business_id: str = Depends(ensure_business_active),
) -> OwnerCallbackItem:
    """Update the resolution status for a callback queue item."""
    queue = getattr(metrics, "callbacks_by_business", {}).get(business_id, {})
    item = queue.get(phone)
    if item is None:
        raise HTTPException(
            status_code=404,
            detail="Callback item not found",
        )

    status_upper = (payload.status or "PENDING").upper()
    if status_upper not in {"PENDING", "COMPLETED", "UNREACHABLE"}:
        raise HTTPException(
            status_code=400,
            detail="Invalid callback status",
        )

    item.status = status_upper
    if payload.result is not None:
        item.last_result = payload.result
    elif status_upper == "COMPLETED":
        item.last_result = "completed"
    elif status_upper == "UNREACHABLE":
        item.last_result = "unreachable"

    return OwnerCallbackItem(
        phone=item.phone,
        first_seen=item.first_seen,
        last_seen=item.last_seen,
        attempts=item.count,
        channel=item.channel,
        lead_source=item.lead_source,
        status=item.status,
        last_result=item.last_result,
    )


@router.get("/callbacks/summary", response_model=OwnerCallbackSummaryResponse)
def owner_callbacks_summary(
    business_id: str = Depends(ensure_business_active),
) -> OwnerCallbackSummaryResponse:
    """Return aggregated callback funnel metrics for this tenant."""
    queue = getattr(metrics, "callbacks_by_business", {}).get(business_id, {}) or {}
    total_callbacks = len(queue)
    pending = 0
    completed = 0
    unreachable = 0
    missed_callbacks = 0
    partial_intake_callbacks = 0

    per_source: dict[str, dict[str, int]] = {}

    for item in queue.values():
        status = (getattr(item, "status", "PENDING") or "PENDING").upper()
        reason = (getattr(item, "reason", "MISSED_CALL") or "MISSED_CALL").upper()
        if status == "COMPLETED":
            completed += 1
        elif status == "UNREACHABLE":
            unreachable += 1
        else:
            pending += 1

        if reason == "PARTIAL_INTAKE":
            partial_intake_callbacks += 1
        else:
            missed_callbacks += 1

        src = (item.lead_source or "unspecified").strip() or "unspecified"
        bucket = per_source.setdefault(
            src,
            {"total": 0, "pending": 0, "completed": 0, "unreachable": 0},
        )
        bucket["total"] += 1
        if status == "COMPLETED":
            bucket["completed"] += 1
        elif status == "UNREACHABLE":
            bucket["unreachable"] += 1
        else:
            bucket["pending"] += 1

    lead_sources: list[OwnerCallbackLeadSourceSummary] = []
    for src, agg in per_source.items():
        lead_sources.append(
            OwnerCallbackLeadSourceSummary(
                lead_source=src,
                total=agg["total"],
                pending=agg["pending"],
                completed=agg["completed"],
                unreachable=agg["unreachable"],
            )
        )
    lead_sources.sort(key=lambda i: i.total, reverse=True)

    return OwnerCallbackSummaryResponse(
        total_callbacks=total_callbacks,
        pending=pending,
        completed=completed,
        unreachable=unreachable,
        lead_sources=lead_sources,
        missed_callbacks=missed_callbacks,
        partial_intake_callbacks=partial_intake_callbacks,
    )


@router.get("/technicians", response_model=list[OwnerTechnician])
def owner_technicians(
    business_id: str = Depends(ensure_business_active),
) -> list[OwnerTechnician]:
    """List technicians for the current tenant (owner view)."""
    if not (SQLALCHEMY_AVAILABLE and SessionLocal is not None):
        return []
    session_db = SessionLocal()
    try:
        rows = (
            session_db.query(TechnicianDB)
            .filter(TechnicianDB.business_id == business_id)
            .order_by(TechnicianDB.created_at.asc())
            .all()
        )
        technicians: list[OwnerTechnician] = []
        for row in rows:
            technicians.append(
                OwnerTechnician(
                    id=row.id,
                    name=row.name,
                    color=getattr(row, "color", None),
                    is_active=bool(getattr(row, "is_active", True)),
                )
            )
        return technicians
    finally:
        session_db.close()


class OwnerTagSegmentItem(BaseModel):
    tag: str
    customers: int
    appointments: int
    emergency_appointments: int
    estimated_value_total: float


class OwnerSegmentsResponse(BaseModel):
    items: list[OwnerTagSegmentItem]


@router.get("/segments", response_model=OwnerSegmentsResponse)
def owner_segments(
    business_id: str = Depends(ensure_business_active),
) -> OwnerSegmentsResponse:
    """Summarize basic segments based on customer and appointment tags."""
    # Customer tag counts.
    customer_counts: dict[str, int] = {}
    for cust in customers_repo.list_for_business(business_id):
        tags = getattr(cust, "tags", []) or []
        for tag in tags:
            t = str(tag or "").strip()
            if not t:
                continue
            customer_counts[t] = customer_counts.get(t, 0) + 1

    # Appointment tag counts and value.
    appt_counts: dict[str, int] = {}
    appt_emergency_counts: dict[str, int] = {}
    value_totals: dict[str, float] = {}
    for appt in appointments_repo.list_for_business(business_id):
        tags = getattr(appt, "tags", []) or []
        if not tags:
            continue
        is_emergency = bool(getattr(appt, "is_emergency", False))
        est_value = getattr(appt, "estimated_value", None)
        value = float(est_value) if est_value is not None else 0.0
        for tag in tags:
            t = str(tag or "").strip()
            if not t:
                continue
            appt_counts[t] = appt_counts.get(t, 0) + 1
            if is_emergency:
                appt_emergency_counts[t] = appt_emergency_counts.get(t, 0) + 1
            value_totals[t] = value_totals.get(t, 0.0) + value

    all_tags = set(customer_counts.keys()) | set(appt_counts.keys())
    items: list[OwnerTagSegmentItem] = []
    for tag in sorted(all_tags):
        items.append(
            OwnerTagSegmentItem(
                tag=tag,
                customers=customer_counts.get(tag, 0),
                appointments=appt_counts.get(tag, 0),
                emergency_appointments=appt_emergency_counts.get(tag, 0),
                estimated_value_total=value_totals.get(tag, 0.0),
            )
        )

    return OwnerSegmentsResponse(items=items)


class OwnerFollowupSummaryResponse(BaseModel):
    window_days: int
    followups_sent: int
    recent_leads_without_appointments: int
    recent_leads_with_appointments: int
    retention_messages_sent: int


class OwnerRetentionCampaignItem(BaseModel):
    campaign_type: str
    messages_sent: int


class OwnerRetentionSummaryResponse(BaseModel):
    total_messages_sent: int
    campaigns: list[OwnerRetentionCampaignItem]


@router.get("/followups", response_model=OwnerFollowupSummaryResponse)
def owner_followup_summary(
    business_id: str = Depends(ensure_business_active),
    days: int = Query(7, ge=1, le=30),
) -> OwnerFollowupSummaryResponse:
    """Summarize recent lead follow-ups and conversions for this tenant.

    - followups_sent is taken from in-memory SMS metrics for this tenant.
    - recent_leads_* are derived from conversations and appointments in the
      last N days and approximate how many recent leads have booked.
    """
    now = datetime.now(UTC)
    window = now - timedelta(days=days)

    from ..repositories import conversations_repo  # local import to avoid cycles

    candidate_customers: set[str] = set()
    for conv in conversations_repo.list_for_business(business_id):
        if not conv.customer_id:
            continue
        created_at = getattr(conv, "created_at", now)
        if created_at < window or created_at > now:
            continue
        candidate_customers.add(conv.customer_id)

    recent_with_appt = 0
    recent_without_appt = 0
    for customer_id in candidate_customers:
        appts = appointments_repo.list_for_customer(customer_id)
        has_active = any(
            getattr(a, "business_id", business_id) == business_id
            and getattr(a, "status", "SCHEDULED").upper() in {"SCHEDULED", "CONFIRMED"}
            for a in appts
        )
        if has_active:
            recent_with_appt += 1
        else:
            recent_without_appt += 1

    per = metrics.sms_by_business.get(business_id)
    followups_sent = per.lead_followups_sent if per else 0
    retention_sent = per.retention_messages_sent if per else 0

    return OwnerFollowupSummaryResponse(
        window_days=days,
        followups_sent=followups_sent,
        recent_leads_without_appointments=recent_without_appt,
        recent_leads_with_appointments=recent_with_appt,
        retention_messages_sent=retention_sent,
    )


@router.get("/retention", response_model=OwnerRetentionSummaryResponse)
def owner_retention_summary(
    business_id: str = Depends(ensure_business_active),
) -> OwnerRetentionSummaryResponse:
    """Summarize retention SMS campaigns for this tenant."""
    per_sms = metrics.sms_by_business.get(business_id)
    total = per_sms.retention_messages_sent if per_sms else 0
    campaigns_raw = getattr(metrics, "retention_by_business", {}).get(
        business_id, {}
    )
    campaigns: list[OwnerRetentionCampaignItem] = []
    for ctype, count in campaigns_raw.items():
        campaigns.append(
            OwnerRetentionCampaignItem(
                campaign_type=str(ctype),
                messages_sent=int(count),
            )
        )
    campaigns.sort(key=lambda c: c.messages_sent, reverse=True)
    return OwnerRetentionSummaryResponse(
        total_messages_sent=total,
        campaigns=campaigns,
    )


class OwnerConversationItem(BaseModel):
    id: str
    channel: str
    created_at: datetime
    customer_name: str | None = None
    flagged_for_review: bool = False
    is_emergency_related: bool = False
    outcome: str | None = None
    tags: list[str] = []
    has_active_appointment: bool = False


class OwnerConversationReviewResponse(BaseModel):
    total_conversations: int
    flagged_conversations: int
    emergency_conversations: int
    items: list[OwnerConversationItem]


@router.get("/conversations/review", response_model=OwnerConversationReviewResponse)
def owner_conversations_review(
    business_id: str = Depends(ensure_business_active),
) -> OwnerConversationReviewResponse:
    """Return flagged and emergency-tagged conversations for this tenant.

    This is intended for light QA/quality review by the owner and surfaces
    only conversations that have been explicitly flagged or marked as
    emergency-related via tags or outcome text.
    """
    conversations = conversations_repo.list_for_business(business_id)
    total = len(conversations)
    flagged = 0
    emergency = 0
    items: list[OwnerConversationItem] = []

    for conv in conversations:
        is_flagged = bool(getattr(conv, "flagged_for_review", False))
        tags = getattr(conv, "tags", []) or []
        outcome = getattr(conv, "outcome", None)
        combined = ((" ".join(tags) + " " + (outcome or ""))).lower()
        is_emergency = "emergency" in combined

        if is_flagged:
            flagged += 1
        if is_emergency:
            emergency += 1

        # Only include conversations that are either flagged or emergency-tagged.
        if not (is_flagged or is_emergency):
            continue

        customer_name: str | None = None
        has_active_appt = False
        if conv.customer_id:
            customer = customers_repo.get(conv.customer_id)
            if customer:
                customer_name = customer.name
                # Check for any active appointments for this customer in this tenant.
                appts = appointments_repo.list_for_customer(conv.customer_id)
                has_active_appt = any(
                    getattr(a, "business_id", business_id) == business_id
                    and getattr(a, "status", "SCHEDULED").upper()
                    in {"SCHEDULED", "CONFIRMED"}
                    for a in appts
                )

        items.append(
            OwnerConversationItem(
                id=conv.id,
                channel=conv.channel,
                created_at=conv.created_at,
                customer_name=customer_name,
                flagged_for_review=is_flagged,
                is_emergency_related=is_emergency,
                outcome=outcome,
                tags=tags,
                has_active_appointment=has_active_appt,
            )
        )

    return OwnerConversationReviewResponse(
        total_conversations=total,
        flagged_conversations=flagged,
        emergency_conversations=emergency,
        items=items,
    )
