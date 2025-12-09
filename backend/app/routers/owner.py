from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
import io
import os

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, EmailStr, Field

from ..deps import ensure_business_active, require_dashboard_role
from ..repositories import appointments_repo, conversations_repo, customers_repo
from ..db import SQLALCHEMY_AVAILABLE, SessionLocal
from ..db_models import (
    AppointmentDB,
    BusinessDB,
    ConversationDB,
    ConversationMessageDB,
    CustomerDB,
    TechnicianDB,
)
from ..metrics import metrics
from ..services import twilio_provision
from ..services.sms import sms_service
from ..services.stt_tts import speech_service
from ..services.geo_utils import derive_neighborhood_label, geocode_address
from ..services.zip_enrichment import fetch_zip_income
from ..business_config import get_voice_for_business


router = APIRouter(
    dependencies=[
        Depends(require_dashboard_role(["admin", "owner", "staff", "viewer"]))
    ]
)


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
def tomorrow_schedule(
    business_id: str = Depends(ensure_business_active),
) -> OwnerScheduleResponse:
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
            row = session_db.get(BusinessDB, business_id)
        finally:
            session_db.close()
        if row is not None and getattr(row, "name", None):
            business_name = row.name

    if not items:
        if business_name:
            reply_text = (
                f"Tomorrow you have no appointments scheduled for {business_name}."
            )
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
    voice = get_voice_for_business(business_id)
    audio = await speech_service.synthesize(base.reply_text, voice=voice)
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
    voice = get_voice_for_business(business_id)
    audio = await speech_service.synthesize(base.reply_text, voice=voice)
    return OwnerTodaySummaryAudioResponse(reply_text=base.reply_text, audio=audio)


class OwnerBusinessResponse(BaseModel):
    id: str
    name: str
    owner_name: str | None = None
    owner_email: str | None = None
    owner_profile_image_url: str | None = None
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
    owner_name: str | None = None
    owner_email: str | None = None
    owner_profile_image_url: str | None = None
    max_jobs_per_day: int | None = None
    reserve_mornings_for_emergencies: bool | None = None
    travel_buffer_minutes: int | None = None
    language_code: str | None = None
    if SQLALCHEMY_AVAILABLE and SessionLocal is not None:
        session_db = SessionLocal()
        try:
            row = session_db.get(BusinessDB, business_id)
        finally:
            session_db.close()
        if row is not None and getattr(row, "name", None):
            name = row.name
            owner_name = getattr(row, "owner_name", None)
            owner_email = getattr(row, "owner_email", None)
            owner_profile_image_url = getattr(row, "owner_profile_image_url", None)
            max_jobs_per_day = getattr(row, "max_jobs_per_day", None)
            reserve_mornings_for_emergencies = getattr(
                row, "reserve_mornings_for_emergencies", None
            )
            travel_buffer_minutes = getattr(row, "travel_buffer_minutes", None)
            language_code = getattr(row, "language_code", None)
    return OwnerBusinessResponse(
        id=business_id,
        name=name,
        owner_name=owner_name,
        owner_email=owner_email,
        owner_profile_image_url=owner_profile_image_url,
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
        reply_text = f"You have {count} appointment{'s' if count != 1 else ''} marked for rescheduling."

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
        stage = (
            getattr(appt, "job_stage", None) or "Unspecified"
        ).strip() or "Unspecified"
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
    return "quote" in text or "estimate" in text or "proposal" in text or "lead" in text


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


class OwnerCalendarDaySummary(BaseModel):
    date: date
    total_appointments: int
    tag_counts: dict[str, int]
    service_type_counts: dict[str, int]
    estimated_value_total: float
    estimated_value_average: float | None = None
    new_customers: int


class OwnerCalendarWindowResponse(BaseModel):
    start_date: date
    end_date: date
    days: list[OwnerCalendarDaySummary]


def _compute_first_appointment_dates(business_id: str) -> dict[str, date]:
    """Return first-seen appointment date per customer for this tenant."""
    first_by_customer: dict[str, datetime] = {}
    for appt in appointments_repo.list_for_business(business_id):
        customer_id = getattr(appt, "customer_id", None)
        start_time = getattr(appt, "start_time", None)
        if not customer_id or not start_time:
            continue
        existing = first_by_customer.get(customer_id)
        if existing is None or start_time < existing:
            first_by_customer[customer_id] = start_time
    return {cid: dt.date() for cid, dt in first_by_customer.items()}


def _classify_calendar_tags(
    appt,
    is_new_client: bool,
) -> set[str]:
    """Assign high-level calendar tags to an appointment.

    Tags are used for the 90-day calendar heatmap:
    - emergency
    - routine (non-emergency work)
    - maintenance (service_type hints)
    - service (all qualifying work)
    - new_client (first appointment for a customer)
    """
    tags: set[str] = set()
    is_emergency = bool(getattr(appt, "is_emergency", False))
    service_type_raw = getattr(appt, "service_type", None) or ""
    service_type = str(service_type_raw).lower()

    if is_emergency:
        tags.add("emergency")
    else:
        tags.add("routine")

    if (
        "maint" in service_type
        or "tune" in service_type
        or "inspection" in service_type
    ):
        tags.add("maintenance")

    # All scheduled work is still tagged as generic service.
    tags.add("service")

    if is_new_client:
        tags.add("new_client")

    return tags


def _compute_calendar_window(
    business_id: str,
    start_date: date,
    end_date: date,
) -> OwnerCalendarWindowResponse:
    """Aggregate appointments into per-day calendar buckets for a window."""
    if end_date < start_date:
        start_date, end_date = end_date, start_date

    first_dates = _compute_first_appointment_dates(business_id)

    buckets: dict[date, dict[str, object]] = {}
    for appt in appointments_repo.list_for_business(business_id):
        start_time = getattr(appt, "start_time", None)
        if not start_time:
            continue
        appt_day = start_time.date()
        if appt_day < start_date or appt_day > end_date:
            continue
        status = getattr(appt, "status", "SCHEDULED").upper()
        if status not in {"SCHEDULED", "CONFIRMED", "COMPLETED"}:
            continue

        customer_id = getattr(appt, "customer_id", None)
        is_new_client = False
        if customer_id:
            first_date = first_dates.get(customer_id)
            if first_date is not None and first_date == appt_day:
                is_new_client = True

        tags = _classify_calendar_tags(appt, is_new_client=is_new_client)

        bucket = buckets.setdefault(
            appt_day,
            {
                "total": 0,
                "tags": {},  # type: ignore[dict-item]
                "services": {},  # type: ignore[dict-item]
                "value_total": 0.0,
                "value_count": 0,
                "new_customers": 0,
            },
        )
        bucket["total"] = int(bucket["total"]) + 1  # type: ignore[index]
        tag_counts: dict[str, int] = bucket["tags"]  # type: ignore[assignment]
        for tag in tags:
            tag_counts[tag] = tag_counts.get(tag, 0) + 1

        service_type_name = getattr(appt, "service_type", None) or "unspecified"
        services: dict[str, int] = bucket["services"]  # type: ignore[assignment]
        services[service_type_name] = services.get(service_type_name, 0) + 1

        est_value_raw = getattr(appt, "estimated_value", None)
        if est_value_raw is not None:
            value = float(est_value_raw)
            bucket["value_total"] = float(bucket["value_total"]) + value  # type: ignore[index]
            bucket["value_count"] = int(bucket["value_count"]) + 1  # type: ignore[index]

        if is_new_client:
            bucket["new_customers"] = int(bucket["new_customers"]) + 1  # type: ignore[index]

    days: list[OwnerCalendarDaySummary] = []
    span_days = (end_date - start_date).days + 1
    for offset in range(span_days):
        day = start_date + timedelta(days=offset)
        bucket = buckets.get(
            day,
            {
                "total": 0,
                "tags": {},
                "services": {},
                "value_total": 0.0,
                "value_count": 0,
                "new_customers": 0,
            },
        )
        total = int(bucket["total"])  # type: ignore[index]
        value_total = float(bucket["value_total"])  # type: ignore[index]
        value_count = int(bucket["value_count"])  # type: ignore[index]
        avg_value = value_total / float(value_count) if value_count > 0 else None
        tag_counts = dict(bucket["tags"])  # type: ignore[arg-type]
        service_type_counts = dict(bucket["services"])  # type: ignore[arg-type]
        new_customers = int(bucket["new_customers"])  # type: ignore[index]
        days.append(
            OwnerCalendarDaySummary(
                date=day,
                total_appointments=total,
                tag_counts=tag_counts,
                service_type_counts=service_type_counts,
                estimated_value_total=value_total,
                estimated_value_average=avg_value,
                new_customers=new_customers,
            )
        )

    return OwnerCalendarWindowResponse(
        start_date=start_date,
        end_date=end_date,
        days=days,
    )


def _business_onboarding_profile_from_row(
    row: BusinessDB, business_id: str
) -> OwnerOnboardingProfile:
    business_name = getattr(row, "name", "Default Business")
    vertical = getattr(row, "vertical", None)
    owner_name = getattr(row, "owner_name", None)
    owner_email = getattr(row, "owner_email", None)
    owner_phone = getattr(row, "owner_phone", None)
    owner_profile_image_url = getattr(row, "owner_profile_image_url", None)
    service_tier = getattr(row, "service_tier", None)
    tts_voice = getattr(row, "tts_voice", None)
    twilio_phone_number = getattr(row, "twilio_phone_number", None)
    terms_accepted = bool(getattr(row, "terms_accepted_at", None))
    privacy_accepted = bool(getattr(row, "privacy_accepted_at", None))
    onboarding_step = getattr(row, "onboarding_step", None)
    onboarding_completed = bool(getattr(row, "onboarding_completed", False))
    subscription_status = getattr(row, "subscription_status", None)
    subscription_current_period_end = getattr(
        row, "subscription_current_period_end", None
    )

    def _status(attr: str) -> bool:
        raw = (getattr(row, attr, None) or "").lower()
        return raw == "connected"

    integrations: list[OwnerIntegrationStatus] = [
        OwnerIntegrationStatus(
            provider="linkedin",
            connected=_status("integration_linkedin_status"),
            label="LinkedIn Company",
        ),
        OwnerIntegrationStatus(
            provider="gmail",
            connected=_status("integration_gmail_status"),
            label="Gmail / Google Workspace",
        ),
        OwnerIntegrationStatus(
            provider="gcalendar",
            connected=_status("integration_gcalendar_status"),
            label="Google Calendar",
        ),
        OwnerIntegrationStatus(
            provider="openai",
            connected=_status("integration_openai_status"),
            label="OpenAI account",
        ),
        OwnerIntegrationStatus(
            provider="twilio",
            connected=_status("integration_twilio_status"),
            label="Twilio account",
        ),
        OwnerIntegrationStatus(
            provider="quickbooks",
            connected=_status("integration_qbo_status"),
            label="QuickBooks Online",
        ),
    ]

    return OwnerOnboardingProfile(
        business_id=business_id,
        business_name=business_name,
        vertical=vertical,
        owner_name=owner_name,
        owner_email=owner_email,
        owner_phone=owner_phone,
        owner_profile_image_url=owner_profile_image_url,
        service_tier=service_tier,
        terms_accepted=terms_accepted,
        privacy_accepted=privacy_accepted,
        tts_voice=tts_voice,
        twilio_phone_number=twilio_phone_number,
        onboarding_step=onboarding_step,
        onboarding_completed=onboarding_completed,
        subscription_status=subscription_status,
        subscription_current_period_end=subscription_current_period_end,
        integrations=integrations,
    )


@router.get("/calendar/90d", response_model=OwnerCalendarWindowResponse)
def owner_calendar_90d(
    business_id: str = Depends(ensure_business_active),
) -> OwnerCalendarWindowResponse:
    """Return a 90-day calendar view for the owner dashboard.

    The window starts today (inclusive) and extends 89 days into the
    future. Each day is annotated with high-level tags such as routine,
    emergency, maintenance, service, and new_client.
    """
    today = datetime.now(UTC).date()
    end = today + timedelta(days=89)
    return _compute_calendar_window(business_id, start_date=today, end_date=end)


class OwnerIntegrationStatus(BaseModel):
    provider: str
    connected: bool
    label: str | None = None


class OwnerOnboardingProfile(BaseModel):
    business_id: str
    business_name: str
    vertical: str | None = None
    owner_name: str | None = None
    owner_email: str | None = None
    owner_phone: str | None = None
    owner_profile_image_url: str | None = None
    service_tier: str | None = None
    terms_accepted: bool
    privacy_accepted: bool
    tts_voice: str | None = None
    twilio_phone_number: str | None = None
    onboarding_step: str | None = None
    onboarding_completed: bool = False
    subscription_status: str | None = None
    subscription_current_period_end: datetime | None = None
    integrations: list[OwnerIntegrationStatus]


@router.get("/onboarding/profile", response_model=OwnerOnboardingProfile)
def owner_onboarding_profile(
    business_id: str = Depends(ensure_business_active),
) -> OwnerOnboardingProfile:
    """Return the current onboarding/profile state for this tenant."""
    if not SQLALCHEMY_AVAILABLE or SessionLocal is None:
        # Fall back to a minimal profile when database support is unavailable.
        return OwnerOnboardingProfile(
            business_id=business_id,
            business_name="Default Business",
            vertical=None,
            owner_name=None,
            owner_email=None,
            owner_phone=None,
            owner_profile_image_url=None,
            service_tier=None,
            terms_accepted=False,
            privacy_accepted=False,
            tts_voice=get_voice_for_business(business_id),
            twilio_phone_number=None,
            onboarding_step=None,
            onboarding_completed=False,
            subscription_status=None,
            subscription_current_period_end=None,
            integrations=[
                OwnerIntegrationStatus(
                    provider="linkedin", connected=False, label="LinkedIn Company"
                ),
                OwnerIntegrationStatus(
                    provider="gmail", connected=False, label="Gmail / Google Workspace"
                ),
                OwnerIntegrationStatus(
                    provider="gcalendar", connected=False, label="Google Calendar"
                ),
                OwnerIntegrationStatus(
                    provider="openai", connected=False, label="OpenAI account"
                ),
                OwnerIntegrationStatus(
                    provider="twilio", connected=False, label="Twilio account"
                ),
                OwnerIntegrationStatus(
                    provider="quickbooks", connected=False, label="QuickBooks Online"
                ),
            ],
        )

    session = SessionLocal()
    try:
        row = session.get(BusinessDB, business_id)
        if row is None:
            raise HTTPException(
                status_code=404,
                detail="Business not found",
            )
        return _business_onboarding_profile_from_row(row, business_id)
    finally:
        session.close()


class OwnerOnboardingUpdateRequest(BaseModel):
    owner_name: str | None = None
    owner_email: EmailStr | None = None
    owner_phone: str | None = None
    owner_profile_image_url: str | None = None
    service_tier: str | None = Field(
        default=None,
        description="Selected service tier (e.g. '20', '100', '200').",
    )
    accept_terms: bool | None = None
    accept_privacy: bool | None = None
    tts_voice: str | None = None
    onboarding_step: str | None = Field(
        default=None,
        description="Current onboarding step (e.g. 'profile', 'data', 'ai', 'complete').",
    )
    onboarding_completed: bool | None = Field(
        default=None, description="Mark onboarding as completed for this tenant."
    )


@router.patch("/onboarding/profile", response_model=OwnerOnboardingProfile)
def owner_onboarding_update(
    payload: OwnerOnboardingUpdateRequest,
    business_id: str = Depends(ensure_business_active),
) -> OwnerOnboardingProfile:
    """Update onboarding/profile fields for this tenant."""
    if not SQLALCHEMY_AVAILABLE or SessionLocal is None:
        raise HTTPException(
            status_code=503,
            detail="Database support is not available for onboarding updates.",
        )

    if payload.service_tier is not None and payload.service_tier not in {
        "20",
        "100",
        "200",
    }:
        raise HTTPException(
            status_code=400,
            detail="Invalid service tier; expected one of '20', '100', or '200'.",
        )

    session = SessionLocal()
    try:
        row = session.get(BusinessDB, business_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Business not found")

        if payload.owner_name is not None:
            row.owner_name = payload.owner_name
        if payload.owner_email is not None:
            row.owner_email = str(payload.owner_email)
        if payload.owner_phone is not None:
            row.owner_phone = payload.owner_phone
        if payload.owner_profile_image_url is not None:
            row.owner_profile_image_url = payload.owner_profile_image_url
        if payload.service_tier is not None:
            row.service_tier = payload.service_tier
        if payload.tts_voice is not None:
            row.tts_voice = payload.tts_voice
        if payload.onboarding_step is not None:
            row.onboarding_step = payload.onboarding_step
        if payload.onboarding_completed is not None:
            row.onboarding_completed = bool(payload.onboarding_completed)

        now = datetime.now(UTC)
        if payload.accept_terms:
            row.terms_accepted_at = getattr(row, "terms_accepted_at", None) or now
        if payload.accept_privacy:
            row.privacy_accepted_at = getattr(row, "privacy_accepted_at", None) or now

        session.add(row)
        session.commit()
        session.refresh(row)
        return _business_onboarding_profile_from_row(row, business_id)
    finally:
        session.close()


class OwnerIntegrationsUpdateRequest(BaseModel):
    linkedin_connected: bool | None = None
    gmail_connected: bool | None = None
    gcalendar_connected: bool | None = None
    openai_connected: bool | None = None
    twilio_connected: bool | None = None
    quickbooks_connected: bool | None = None


class TwilioProvisionRequest(BaseModel):
    phone_number: str | None = Field(
        default=None,
        description="Attach an existing Twilio/Hosted SMS number instead of buying one.",
    )
    purchase_new: bool = Field(
        default=False,
        description="Attempt to purchase a new toll-free number via Twilio if no phone_number is provided.",
    )
    friendly_name: str | None = Field(
        default=None, description="Optional FriendlyName for the Twilio number."
    )
    webhook_base_url: str | None = Field(
        default=None,
        description="Base URL for your deployed API (e.g., https://api.example.com).",
    )
    voice_webhook_url: str | None = Field(
        default=None,
        description="Override VoiceUrl instead of using webhook_base_url + /twilio/voice",
    )
    sms_webhook_url: str | None = Field(
        default=None,
        description="Override SmsUrl instead of using webhook_base_url + /twilio/sms",
    )
    status_callback_url: str | None = Field(
        default=None,
        description="Override StatusCallback instead of using webhook_base_url + /twilio/status-callback",
    )


class TwilioProvisionResponse(BaseModel):
    status: str
    phone_number: str | None = None
    message: str


class OwnerQboPendingItem(BaseModel):
    appointment_id: str
    customer_name: str | None = None
    service_type: str | None = None
    quote_status: str | None = None
    quoted_value: float | None = None
    start_time: datetime | None = None


class OwnerQboPendingResponse(BaseModel):
    items: list[OwnerQboPendingItem]
    total: int


class OwnerQboSummaryResponse(BaseModel):
    connected: bool
    realm_id: str | None = None
    token_expires_at: datetime | None = None
    last_sync_at: datetime | None = None
    pending_count: int


class OwnerQboNotifyRequest(BaseModel):
    send_sms: bool = True
    send_email: bool = False


class OwnerQboNotifyResponse(BaseModel):
    sms_sent: bool
    email_sent: bool
    message: str


@router.patch("/onboarding/integrations", response_model=OwnerOnboardingProfile)
def owner_onboarding_integrations(
    payload: OwnerIntegrationsUpdateRequest,
    business_id: str = Depends(ensure_business_active),
) -> OwnerOnboardingProfile:
    """Update high-level integration connection flags for this tenant."""
    if not SQLALCHEMY_AVAILABLE or SessionLocal is None:
        raise HTTPException(
            status_code=503,
            detail="Database support is not available for onboarding updates.",
        )

    session = SessionLocal()
    try:
        row = session.get(BusinessDB, business_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Business not found")

        def _set_status(flag: bool | None, attr: str) -> None:
            if flag is None:
                return
            setattr(row, attr, "connected" if flag else "disconnected")

        _set_status(payload.linkedin_connected, "integration_linkedin_status")
        _set_status(payload.gmail_connected, "integration_gmail_status")
        _set_status(payload.gcalendar_connected, "integration_gcalendar_status")
        _set_status(payload.openai_connected, "integration_openai_status")
        _set_status(payload.twilio_connected, "integration_twilio_status")
        _set_status(payload.quickbooks_connected, "integration_qbo_status")

        session.add(row)
        session.commit()
        session.refresh(row)
        return _business_onboarding_profile_from_row(row, business_id)
    finally:
        session.close()


@router.post("/twilio/provision", response_model=TwilioProvisionResponse)
async def owner_twilio_provision(
    payload: TwilioProvisionRequest,
    business_id: str = Depends(ensure_business_active),
) -> TwilioProvisionResponse:
    """Attach an existing Twilio number or purchase a toll-free number for this tenant."""
    result = await twilio_provision.provision_toll_free_number(
        business_id=business_id,
        phone_number=payload.phone_number,
        purchase_new=payload.purchase_new,
        friendly_name=payload.friendly_name,
        webhook_base_url=payload.webhook_base_url,
        voice_webhook_url=payload.voice_webhook_url,
        sms_webhook_url=payload.sms_webhook_url,
        status_callback_url=payload.status_callback_url,
    )
    return TwilioProvisionResponse(
        status=result.status,
        phone_number=result.phone_number,
        message=result.message,
    )


@router.get("/calendar/report.pdf")
def owner_calendar_report_pdf(
    business_id: str = Depends(ensure_business_active),
    day: date = Query(..., description="Calendar day to export as a PDF report."),
) -> Response:
    """Export a single day's calendar summary as a small PDF report.

    This reuses the same aggregation as the 90-day calendar view and is
    intended to be downloaded directly from the owner dashboard.
    """
    window = _compute_calendar_window(business_id, start_date=day, end_date=day)
    day_summary = window.days[0] if window.days else None

    if day_summary is None or day_summary.total_appointments == 0:
        # Return a simple "empty" PDF so the UX is consistent.
        title = f"Schedule report for {day.isoformat()}"
        pdf_bytes = _render_simple_calendar_pdf(
            title=title,
            overall_total=0,
            tag_counts={},
            service_type_counts={},
            estimated_value_total=0.0,
            new_customers=0,
        )
    else:
        title = f"Schedule report for {day_summary.date.isoformat()}"
        pdf_bytes = _render_simple_calendar_pdf(
            title=title,
            overall_total=day_summary.total_appointments,
            tag_counts=day_summary.tag_counts,
            service_type_counts=day_summary.service_type_counts,
            estimated_value_total=day_summary.estimated_value_total,
            new_customers=day_summary.new_customers,
        )

    filename = f"schedule_{day.isoformat()}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _pending_qbo_items(business_id: str) -> list[OwnerQboPendingItem]:
    """Return appointments that look pending for QBO insertion."""
    items: list[OwnerQboPendingItem] = []
    for appt in appointments_repo.list_for_business(business_id):
        status = (getattr(appt, "quote_status", None) or "").upper()
        if status not in {"", "PENDING", "PENDING_QBO", "READY_FOR_QBO", "DRAFT"}:
            continue
        cust = customers_repo.get(appt.customer_id) if appt.customer_id else None
        quoted_val = getattr(appt, "quoted_value", None)
        items.append(
            OwnerQboPendingItem(
                appointment_id=appt.id,
                customer_name=cust.name if cust else None,
                service_type=getattr(appt, "service_type", None),
                quote_status=status or None,
                quoted_value=float(quoted_val) if quoted_val is not None else None,
                start_time=getattr(appt, "start_time", None),
            )
        )
    return items


@router.get("/qbo/pending", response_model=OwnerQboPendingResponse)
def owner_qbo_pending(
    business_id: str = Depends(ensure_business_active),
) -> OwnerQboPendingResponse:
    """List appointments pending insertion/update into QuickBooks."""
    items = _pending_qbo_items(business_id)
    return OwnerQboPendingResponse(items=items, total=len(items))


@router.get("/qbo/summary", response_model=OwnerQboSummaryResponse)
def owner_qbo_summary(
    business_id: str = Depends(ensure_business_active),
) -> OwnerQboSummaryResponse:
    """Return QBO link status plus cached counts for pending inserts."""
    connected = False
    realm_id = None
    token_expires_at = None
    if SQLALCHEMY_AVAILABLE and SessionLocal is not None:
        session = SessionLocal()
        try:
            row = session.get(BusinessDB, business_id)
            if row is not None:
                connected = getattr(row, "integration_qbo_status", "") == "connected"
                realm_id = getattr(row, "qbo_realm_id", None)
                token_expires_at = getattr(row, "qbo_token_expires_at", None)
        finally:
            session.close()
    pending = _pending_qbo_items(business_id)
    return OwnerQboSummaryResponse(
        connected=connected,
        realm_id=realm_id,
        token_expires_at=token_expires_at,
        last_sync_at=None,
        pending_count=len(pending),
    )


@router.post("/qbo/notify", response_model=OwnerQboNotifyResponse)
async def owner_qbo_notify(
    payload: OwnerQboNotifyRequest,
    business_id: str = Depends(ensure_business_active),
) -> OwnerQboNotifyResponse:
    """Notify the owner about pending QBO insertions via SMS/email (email stubbed)."""
    business_name = "your business"
    owner_name = None
    if SQLALCHEMY_AVAILABLE and SessionLocal is not None:
        session = SessionLocal()
        try:
            row = session.get(BusinessDB, business_id)
            if row is not None:
                business_name = getattr(row, "name", business_name)
                owner_name = getattr(row, "owner_name", None)
        finally:
            session.close()

    pending = _pending_qbo_items(business_id)
    pending_count = len(pending)
    sms_sent = False
    email_sent = False  # email delivery is not implemented; stubbed for now.

    greeting = f"Hi {owner_name}," if owner_name else "Hi,"
    summary_line = f"You have {pending_count} QuickBooks items pending approval."
    flavor = f" for {business_name}" if business_name else ""
    body = f"{greeting} {summary_line}{flavor}."

    if payload.send_sms:
        await sms_service.notify_owner(body, business_id=business_id)
        sms_sent = True

    # Email path would go here in a real deployment.
    return OwnerQboNotifyResponse(
        sms_sent=sms_sent,
        email_sent=email_sent,
        message=body,
    )


def _render_simple_calendar_pdf(
    title: str,
    overall_total: int,
    tag_counts: dict[str, int],
    service_type_counts: dict[str, int],
    estimated_value_total: float,
    new_customers: int,
) -> bytes:
    """Render a compact calendar summary PDF using reportlab when available.

    If reportlab is not installed, this falls back to a plain-text PDF-like
    payload so the owner still receives something downloadable.
    """
    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.pdfgen import canvas
    except Exception:
        # Minimal fallback: emit a very small pseudo-PDF as bytes so the
        # download still works, even if it is not richly formatted.
        buffer = io.StringIO()
        buffer.write(title + "\n\n")
        buffer.write(f"Total appointments: {overall_total}\n")
        buffer.write(f"New customers: {new_customers}\n")
        buffer.write(f"Estimated value total: {estimated_value_total:.2f}\n")
        if tag_counts:
            buffer.write("\nBy tag:\n")
            for tag, count in sorted(tag_counts.items()):
                buffer.write(f"- {tag}: {count}\n")
        if service_type_counts:
            buffer.write("\nBy service type:\n")
            for svc, count in sorted(service_type_counts.items()):
                buffer.write(f"- {svc}: {count}\n")
        return buffer.getvalue().encode("utf-8")

    from reportlab.lib.units import inch

    mem_buffer = io.BytesIO()
    c = canvas.Canvas(mem_buffer, pagesize=letter)
    width, height = letter

    y = height - 1 * inch
    c.setFont("Helvetica-Bold", 14)
    c.drawString(1 * inch, y, title)
    y -= 0.4 * inch

    c.setFont("Helvetica", 10)
    c.drawString(1 * inch, y, f"Total appointments: {overall_total}")
    y -= 0.2 * inch
    c.drawString(1 * inch, y, f"New customers: {new_customers}")
    y -= 0.2 * inch
    c.drawString(
        1 * inch,
        y,
        f"Estimated value total: ${estimated_value_total:,.2f}",
    )
    y -= 0.4 * inch

    if tag_counts:
        c.setFont("Helvetica-Bold", 11)
        c.drawString(1 * inch, y, "By tag")
        y -= 0.25 * inch
        c.setFont("Helvetica", 10)
        for tag, count in sorted(tag_counts.items()):
            c.drawString(1.1 * inch, y, f"- {tag}: {count}")
            y -= 0.18 * inch
            if y < 1 * inch:
                c.showPage()
                y = height - 1 * inch
                c.setFont("Helvetica", 10)

    if service_type_counts:
        if y < 1.4 * inch:
            c.showPage()
            y = height - 1 * inch
        c.setFont("Helvetica-Bold", 11)
        c.drawString(1 * inch, y, "By service type")
        y -= 0.25 * inch
        c.setFont("Helvetica", 10)
        for svc, count in sorted(service_type_counts.items()):
            c.drawString(1.1 * inch, y, f"- {svc}: {count}")
            y -= 0.18 * inch
            if y < 1 * inch:
                c.showPage()
                y = height - 1 * inch
                c.setFont("Helvetica", 10)

    c.showPage()
    c.save()
    mem_buffer.seek(0)
    return mem_buffer.getvalue()


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
    median_household_income: int | None = None


class OwnerNeighborhoodResponse(BaseModel):
    window_days: int
    items: list[OwnerNeighborhoodItem]


class OwnerGeoMarker(BaseModel):
    lat: float
    lng: float
    label: str
    address: str | None = None
    service_type: str | None = None
    is_emergency: bool = False
    appointment_id: str | None = None


class OwnerGeoMarkersResponse(BaseModel):
    window_days: int
    markers: list[OwnerGeoMarker]


class OwnerServiceMetricsItem(BaseModel):
    service_type: str
    appointments: int
    estimated_value_total: float
    estimated_value_average: float
    scheduled_minutes_min: float | None = None
    scheduled_minutes_max: float | None = None
    scheduled_minutes_average: float | None = None


class OwnerServiceMetricsResponse(BaseModel):
    window_days: int
    items: list[OwnerServiceMetricsItem]


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

        source = (
            getattr(appt, "lead_source", None) or "unspecified"
        ).strip() or "unspecified"
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
        appts_sorted = sorted(appts, key=lambda a: getattr(a, "start_time", now))
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
    zip_income_cache: dict[str, int | None] = {}
    for label, agg in buckets.items():
        customers = len(agg["customers"])  # type: ignore[index]
        appts = int(agg["appointments"])
        emergencies = int(agg["emergencies"])
        value = float(agg["value"])
        median_income: int | None = None
        if len(label) == 5 and label.isdigit():
            cached = zip_income_cache.get(label)
            if cached is None and label not in zip_income_cache:
                profile = fetch_zip_income(label)
                cached = profile.median_household_income
                zip_income_cache[label] = cached
            median_income = cached
        items.append(
            OwnerNeighborhoodItem(
                label=label,
                customers=customers,
                appointments=appts,
                emergency_appointments=emergencies,
                estimated_value_total=value,
                median_household_income=median_income,
            )
        )
    # Sort by total value descending.
    items.sort(key=lambda i: i.estimated_value_total, reverse=True)

    return OwnerNeighborhoodResponse(window_days=days, items=items)


@router.get("/geo/markers", response_model=OwnerGeoMarkersResponse)
def owner_geo_markers(
    business_id: str = Depends(ensure_business_active),
    days: int = Query(30, ge=1, le=365),
) -> OwnerGeoMarkersResponse:
    """Return geocoded appointment markers for map visualization."""
    now = datetime.now(UTC)
    window_start = now - timedelta(days=days)
    markers: list[OwnerGeoMarker] = []
    geo_cache: dict[str, tuple[float, float] | None] = {}

    for appt in appointments_repo.list_for_business(business_id):
        if getattr(appt, "start_time", None) and appt.start_time < window_start:
            continue
        customer = customers_repo.get(appt.customer_id)
        address = customer.address if customer else None
        if not address:
            continue
        if address in geo_cache:
            coords = geo_cache[address]
        else:
            coords = geocode_address(address)
            geo_cache[address] = coords
        if not coords:
            continue
        lat, lng = coords
        markers.append(
            OwnerGeoMarker(
                lat=lat,
                lng=lng,
                label=derive_neighborhood_label(address),
                address=address,
                service_type=getattr(appt, "service_type", None),
                is_emergency=bool(getattr(appt, "is_emergency", False)),
                appointment_id=appt.id,
            )
        )
        if len(markers) >= 200:
            break

    return OwnerGeoMarkersResponse(window_days=days, markers=markers)


@router.get("/service-metrics", response_model=OwnerServiceMetricsResponse)
def owner_service_metrics(
    business_id: str = Depends(ensure_business_active),
    days: int = Query(90, ge=7, le=365),
) -> OwnerServiceMetricsResponse:
    """Summarize per-service price and time metrics for the owner.

    For each `service_type`, this aggregates:
    - appointment count
    - total and average estimated_value
    - min/avg/max scheduled duration in minutes (from start_time/end_time)
    """
    now = datetime.now(UTC)
    window_start = now - timedelta(days=days)

    buckets: dict[str, dict[str, float]] = {}
    for appt in appointments_repo.list_for_business(business_id):
        start_time = getattr(appt, "start_time", None)
        end_time = getattr(appt, "end_time", None)
        if not start_time or not end_time:
            continue
        if start_time < window_start or start_time > now:
            continue
        status = getattr(appt, "status", "SCHEDULED").upper()
        if status not in {"SCHEDULED", "CONFIRMED", "COMPLETED"}:
            continue
        service_type = getattr(appt, "service_type", None)
        if not service_type:
            continue

        # Scheduled duration in minutes.
        duration_minutes = (end_time - start_time).total_seconds() / 60.0
        if duration_minutes <= 0:
            continue

        est_value_raw = getattr(appt, "estimated_value", None)
        est_value = float(est_value_raw) if est_value_raw is not None else 0.0

        bucket = buckets.setdefault(
            service_type,
            {
                "appointments": 0.0,
                "est_total": 0.0,
                "dur_sum": 0.0,
                "dur_min": float("inf"),
                "dur_max": 0.0,
            },
        )
        bucket["appointments"] += 1.0
        bucket["est_total"] += est_value
        bucket["dur_sum"] += duration_minutes
        if duration_minutes < bucket["dur_min"]:
            bucket["dur_min"] = duration_minutes
        if duration_minutes > bucket["dur_max"]:
            bucket["dur_max"] = duration_minutes

    items: list[OwnerServiceMetricsItem] = []
    for svc, agg in buckets.items():
        appts = int(agg["appointments"])
        est_total = float(agg["est_total"])
        avg_est = est_total / appts if appts > 0 else 0.0
        if appts > 0:
            avg_dur = agg["dur_sum"] / appts
            min_dur = agg["dur_min"] if agg["dur_min"] != float("inf") else None
            max_dur = agg["dur_max"] if agg["dur_max"] > 0 else None
        else:
            avg_dur = None
            min_dur = None
            max_dur = None
        items.append(
            OwnerServiceMetricsItem(
                service_type=svc,
                appointments=appts,
                estimated_value_total=est_total,
                estimated_value_average=avg_est,
                scheduled_minutes_min=min_dur,
                scheduled_minutes_max=max_dur,
                scheduled_minutes_average=avg_dur,
            )
        )

    # Sort by total estimated value descending.
    items.sort(key=lambda i: i.estimated_value_total, reverse=True)
    return OwnerServiceMetricsResponse(window_days=days, items=items)


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
            per_channel_minutes.get(channel, 0.0) / float(booked) if booked > 0 else 0.0
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
        float(overall_booked) / float(overall_leads) if overall_leads > 0 else 0.0
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
    campaigns_raw = getattr(metrics, "retention_by_business", {}).get(business_id, {})
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
