from __future__ import annotations

import csv
import io
import os
import secrets
from datetime import datetime, UTC, timedelta
import json

from fastapi import APIRouter, Depends, HTTPException, Response, status, Body
from pydantic import BaseModel, Field

from ..config import get_settings
from ..db import SQLALCHEMY_AVAILABLE, SessionLocal
from ..db_models import AuditEventDB, BusinessDB, RetentionPurgeLogDB, TechnicianDB
from ..deps import require_admin_auth
from ..metrics import metrics
from ..repositories import appointments_repo, conversations_repo, customers_repo
from ..services.gcp_storage import get_gcs_health
from ..services.stt_tts import speech_service
from ..services.retention_purge import PurgeResult, run_retention_purge


router = APIRouter(dependencies=[Depends(require_admin_auth)])


class BusinessCreateRequest(BaseModel):
    id: str | None = None
    name: str
    calendar_id: str | None = None


class BusinessUpdateRequest(BaseModel):
    name: str | None = None
    owner_name: str | None = None
    owner_email: str | None = None
    owner_profile_image_url: str | None = None
    calendar_id: str | None = None
    status: str | None = None
    vertical: str | None = None
    owner_phone: str | None = None
    emergency_keywords: str | None = None
    default_reminder_hours: int | None = None
    service_duration_config: str | None = None
    open_hour: int | None = Field(default=None, ge=0, le=23)
    close_hour: int | None = Field(default=None, ge=0, le=23)
    closed_days: str | None = None
    appointment_retention_days: int | None = Field(default=None, ge=1, le=3650)
    conversation_retention_days: int | None = Field(default=None, ge=1, le=3650)
    language_code: str | None = None
    max_jobs_per_day: int | None = Field(default=None, ge=1, le=1000)
    reserve_mornings_for_emergencies: bool | None = None
    travel_buffer_minutes: int | None = Field(default=None, ge=0, le=240)
    twilio_missed_statuses: str | None = None
    retention_enabled: bool | None = None
    retention_sms_template: str | None = None
    service_tier: str | None = None
    tts_voice: str | None = None
    intent_threshold: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Minimum confidence (0-1) required to accept an intent; stored per tenant.",
    )


class BusinessResponse(BaseModel):
    id: str
    name: str
    owner_name: str | None = None
    owner_email: str | None = None
    owner_profile_image_url: str | None = None
    vertical: str | None = None
    api_key: str | None = None
    api_key_last_used_at: datetime | None = None
    api_key_last_rotated_at: datetime | None = None
    widget_token: str | None = None
    widget_token_last_used_at: datetime | None = None
    widget_token_last_rotated_at: datetime | None = None
    widget_token_expires_at: datetime | None = None
    calendar_id: str | None = None
    status: str
    owner_phone: str | None = None
    zip_code: str | None = None
    median_household_income: int | None = None
    emergency_keywords: str | None = None
    default_reminder_hours: int | None = None
    service_duration_config: str | None = None
    created_at: datetime
    open_hour: int | None = None
    close_hour: int | None = None
    closed_days: str | None = None
    appointment_retention_days: int | None = None
    conversation_retention_days: int | None = None
    language_code: str | None = None
    max_jobs_per_day: int | None = None
    reserve_mornings_for_emergencies: bool | None = None
    travel_buffer_minutes: int | None = None
    twilio_missed_statuses: str | None = None
    retention_enabled: bool | None = None
    retention_sms_template: str | None = None
    service_tier: str | None = None
    tts_voice: str | None = None
    intent_threshold: float | None = None


class BusinessUsageResponse(BusinessResponse):
    total_customers: int
    sms_opt_out_customers: int
    total_appointments: int
    emergency_appointments: int
    appointments_last_7_days: int
    appointments_last_30_days: int
    emergencies_last_7_days: int
    emergencies_last_30_days: int
    sms_owner_messages: int
    sms_customer_messages: int
    sms_total_messages: int
    total_conversations: int
    flagged_conversations: int
    emergency_conversations: int
    service_type_counts: dict[str, int] = Field(default_factory=dict)
    emergency_service_type_counts: dict[str, int] = Field(default_factory=dict)
    pending_reschedules: int = 0
    twilio_voice_requests: int | None = None
    twilio_voice_errors: int | None = None
    twilio_sms_requests: int | None = None
    twilio_sms_errors: int | None = None
    twilio_error_rate: float | None = None
    last_activity_at: datetime | None = None


class WidgetTokenRotateRequest(BaseModel):
    expires_in_minutes: int | None = Field(
        default=None,
        ge=0,
        le=60 * 24 * 30,
        description="Optional TTL for the widget token; when 0 or null, the token does not expire.",
    )


class TokenRotationResponse(BaseModel):
    token: str
    token_type: str
    rotated_at: datetime
    expires_at: datetime | None = None
    last_used_at: datetime | None = None


class BusinessTokenUsage(BaseModel):
    business_id: str
    api_key_last_used_at: datetime | None = None
    api_key_last_rotated_at: datetime | None = None
    widget_token_last_used_at: datetime | None = None
    widget_token_last_rotated_at: datetime | None = None
    widget_token_expires_at: datetime | None = None


class TokenUsageResponse(BaseModel):
    admin_token_last_used_at: datetime | None = None
    admin_token_last_rotated_at: datetime | None = None
    owner_token_last_used_at: datetime | None = None
    owner_token_last_rotated_at: datetime | None = None
    business_tokens: list[BusinessTokenUsage] = Field(default_factory=list)


class TwilioConfigStatus(BaseModel):
    provider: str
    from_number_set: bool
    owner_number_set: bool
    account_sid_set: bool
    auth_token_set: bool
    verify_signatures: bool


class TwilioBusinessHealth(BaseModel):
    business_id: str
    voice_requests: int
    sms_requests: int
    voice_errors: int
    sms_errors: int


class TwilioHealthResponse(BaseModel):
    config: TwilioConfigStatus
    twilio_voice_requests: int
    twilio_voice_errors: int
    twilio_sms_requests: int
    twilio_sms_errors: int
    per_business: list[TwilioBusinessHealth]


class StripeConfigStatus(BaseModel):
    use_stub: bool
    api_key_set: bool
    publishable_key_set: bool
    webhook_secret_set: bool
    price_basic_set: bool
    price_growth_set: bool
    price_scale_set: bool
    verify_signatures: bool


class StripeHealthResponse(BaseModel):
    config: StripeConfigStatus
    subscription_activations: int
    subscription_failures: int
    billing_webhook_failures: int
    subscriptions_by_status: dict[str, int]
    customers_with_stripe_id: int
    businesses_with_subscription: int


class GcpStorageHealthResponse(BaseModel):
    configured: bool
    project_id: str | None
    bucket_name: str | None
    library_available: bool
    can_connect: bool
    error: str | None = None


class SpeechHealthResponse(BaseModel):
    provider: str
    healthy: bool
    reason: str | None = None
    detail: str | None = None
    last_error: str | None = None
    used_fallback: bool | None = None
    circuit_open: bool | None = None


class AdminEnvironmentResponse(BaseModel):
    environment: str


class RetentionPruneResponse(BaseModel):
    appointments_deleted: int
    conversations_deleted: int
    conversation_messages_deleted: int
    log_id: int | None = None


class RetentionPurgeLogResponse(BaseModel):
    id: int
    created_at: datetime
    actor_type: str
    trigger: str
    appointments_deleted: int
    conversations_deleted: int
    conversation_messages_deleted: int


class TechnicianCreateRequest(BaseModel):
    name: str
    color: str | None = None


class TechnicianUpdateRequest(BaseModel):
    name: str | None = None
    color: str | None = None
    is_active: bool | None = None


class TechnicianResponse(BaseModel):
    id: str
    business_id: str
    name: str
    color: str | None = None
    is_active: bool
    created_at: datetime


class GovernanceTenantSummary(BaseModel):
    id: str
    name: str
    status: str
    language_code: str | None = None
    appointment_retention_days: int | None = None
    conversation_retention_days: int | None = None
    max_jobs_per_day: int | None = None
    reserve_mornings_for_emergencies: bool | None = None
    travel_buffer_minutes: int | None = None
    twilio_missed_statuses: str | None = None


class GovernanceSummaryResponse(BaseModel):
    multi_tenant_mode: bool
    business_count: int
    require_business_api_key: bool
    verify_twilio_signatures: bool
    tenants: list[GovernanceTenantSummary]


class AuditEvent(BaseModel):
    id: int
    created_at: datetime
    actor_type: str
    business_id: str | None = None
    path: str
    method: str
    status_code: int


def _get_db_session():
    """Return a database session or raise a 503 HTTPException.

    All admin/tenant endpoints rely on database support; centralising this
    check keeps behaviour consistent while avoiding repeated boilerplate.
    """
    if not SQLALCHEMY_AVAILABLE:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database support is not available",
        )
    if SessionLocal is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database session factory is not available",
        )
    return SessionLocal()


def _business_to_response(row: BusinessDB) -> BusinessResponse:
    created_at = getattr(row, "created_at", datetime.now(UTC)).replace(tzinfo=UTC)
    raw_intent = getattr(row, "intent_threshold", None)
    intent_threshold: float | None = None
    try:
        if raw_intent is not None:
            intent_threshold = float(raw_intent)
            if intent_threshold > 1:
                intent_threshold = intent_threshold / 100.0
    except Exception:
        intent_threshold = None
    return BusinessResponse(
        id=row.id,
        name=row.name,
        owner_name=getattr(row, "owner_name", None),
        owner_email=getattr(row, "owner_email", None),
        owner_profile_image_url=getattr(row, "owner_profile_image_url", None),
        vertical=getattr(row, "vertical", None),
        api_key=row.api_key,
        api_key_last_used_at=getattr(row, "api_key_last_used_at", None),
        api_key_last_rotated_at=getattr(row, "api_key_last_rotated_at", None),
        widget_token=getattr(row, "widget_token", None),
        widget_token_last_used_at=getattr(row, "widget_token_last_used_at", None),
        widget_token_last_rotated_at=getattr(row, "widget_token_last_rotated_at", None),
        widget_token_expires_at=getattr(row, "widget_token_expires_at", None),
        calendar_id=getattr(row, "calendar_id", None),
        status=getattr(row, "status", "ACTIVE"),
        owner_phone=getattr(row, "owner_phone", None),
        zip_code=getattr(row, "zip_code", None),
        median_household_income=getattr(row, "median_household_income", None),
        emergency_keywords=getattr(row, "emergency_keywords", None),
        default_reminder_hours=getattr(row, "default_reminder_hours", None),
        service_duration_config=getattr(row, "service_duration_config", None),
        created_at=created_at,
        open_hour=getattr(row, "open_hour", None),
        close_hour=getattr(row, "close_hour", None),
        closed_days=getattr(row, "closed_days", None),
        appointment_retention_days=getattr(row, "appointment_retention_days", None),
        conversation_retention_days=getattr(row, "conversation_retention_days", None),
        language_code=getattr(row, "language_code", None),
        max_jobs_per_day=getattr(row, "max_jobs_per_day", None),
        reserve_mornings_for_emergencies=getattr(
            row, "reserve_mornings_for_emergencies", None
        ),
        travel_buffer_minutes=getattr(row, "travel_buffer_minutes", None),
        twilio_missed_statuses=getattr(row, "twilio_missed_statuses", None),
        retention_enabled=getattr(row, "retention_enabled", None),
        retention_sms_template=getattr(row, "retention_sms_template", None),
        service_tier=getattr(row, "service_tier", None),
        tts_voice=getattr(row, "tts_voice", None),
        intent_threshold=intent_threshold,
    )


def _technician_to_response(row: TechnicianDB) -> TechnicianResponse:
    created_at = getattr(row, "created_at", datetime.now(UTC)).replace(tzinfo=UTC)
    return TechnicianResponse(
        id=row.id,
        business_id=row.business_id,
        name=row.name,
        color=getattr(row, "color", None),
        is_active=bool(getattr(row, "is_active", True)),
        created_at=created_at,
    )


@router.get("/businesses", response_model=list[BusinessResponse])
def list_businesses() -> list[BusinessResponse]:
    session = _get_db_session()
    try:
        rows = session.query(BusinessDB).all()
        return [_business_to_response(b) for b in rows]
    finally:
        session.close()


@router.post(
    "/businesses", response_model=BusinessResponse, status_code=status.HTTP_201_CREATED
)
def create_business(payload: BusinessCreateRequest) -> BusinessResponse:
    business_id = payload.id or secrets.token_hex(8)
    session = _get_db_session()
    try:
        if session.get(BusinessDB, business_id):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Business with this ID already exists",
            )
        api_key = secrets.token_hex(16)
        widget_token = secrets.token_hex(16)
        now = datetime.now(UTC)
        settings = get_settings()
        calendar_id = payload.calendar_id or settings.calendar.calendar_id
        default_widget_ttl = getattr(settings, "widget_token_ttl_minutes", None)
        widget_expires_at = (
            now + timedelta(minutes=int(default_widget_ttl))
            if default_widget_ttl and int(default_widget_ttl) > 0
            else None
        )
        row = BusinessDB(  # type: ignore[arg-type]
            id=business_id,
            name=payload.name,
            vertical=getattr(settings, "default_vertical", "plumbing"),
            api_key=api_key,
            api_key_last_rotated_at=now,
            widget_token=widget_token,
            widget_token_last_rotated_at=now,
            widget_token_expires_at=widget_expires_at,
            calendar_id=calendar_id,
            status="ACTIVE",
            owner_phone=None,
            emergency_keywords=None,
            default_reminder_hours=None,
            service_duration_config=None,
            open_hour=None,
            close_hour=None,
            closed_days=None,
            appointment_retention_days=None,
            conversation_retention_days=None,
            language_code=getattr(settings, "default_language_code", "en"),
            max_jobs_per_day=None,
            reserve_mornings_for_emergencies=False,
            travel_buffer_minutes=None,
            twilio_missed_statuses=None,
            retention_enabled=True,
            retention_sms_template=None,
            created_at=now,
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return _business_to_response(row)
    finally:
        session.close()


@router.patch("/businesses/{business_id}", response_model=BusinessResponse)
def update_business(
    business_id: str, payload: BusinessUpdateRequest
) -> BusinessResponse:
    """Update mutable fields for a business (name, calendar_id)."""
    session = _get_db_session()
    try:
        row = session.get(BusinessDB, business_id)
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Business not found",
            )
        if payload.name is not None:
            row.name = payload.name
        if payload.owner_name is not None:
            row.owner_name = payload.owner_name
        if payload.owner_email is not None:
            row.owner_email = payload.owner_email
        if payload.owner_profile_image_url is not None:
            row.owner_profile_image_url = payload.owner_profile_image_url
        if payload.vertical is not None:
            row.vertical = payload.vertical
        if payload.calendar_id is not None:
            row.calendar_id = payload.calendar_id
        if payload.status is not None:
            row.status = payload.status
        if payload.owner_phone is not None:
            row.owner_phone = payload.owner_phone
        if payload.emergency_keywords is not None:
            row.emergency_keywords = payload.emergency_keywords
        if payload.default_reminder_hours is not None:
            row.default_reminder_hours = payload.default_reminder_hours
        if payload.service_duration_config is not None:
            row.service_duration_config = payload.service_duration_config
        if payload.appointment_retention_days is not None:
            row.appointment_retention_days = payload.appointment_retention_days
        if payload.conversation_retention_days is not None:
            row.conversation_retention_days = payload.conversation_retention_days
        if payload.language_code is not None:
            row.language_code = payload.language_code
        if payload.open_hour is not None:
            row.open_hour = payload.open_hour
        if payload.close_hour is not None:
            row.close_hour = payload.close_hour
        if payload.closed_days is not None:
            row.closed_days = payload.closed_days
        if payload.max_jobs_per_day is not None:
            row.max_jobs_per_day = payload.max_jobs_per_day
        if payload.reserve_mornings_for_emergencies is not None:
            row.reserve_mornings_for_emergencies = (
                payload.reserve_mornings_for_emergencies
            )
        if payload.travel_buffer_minutes is not None:
            row.travel_buffer_minutes = payload.travel_buffer_minutes
        if payload.twilio_missed_statuses is not None:
            row.twilio_missed_statuses = payload.twilio_missed_statuses
        if payload.retention_enabled is not None:
            row.retention_enabled = payload.retention_enabled
        if payload.retention_sms_template is not None:
            row.retention_sms_template = payload.retention_sms_template
        if payload.service_tier is not None:
            row.service_tier = payload.service_tier
        if payload.tts_voice is not None:
            row.tts_voice = payload.tts_voice
        if payload.intent_threshold is not None:
            # Store as integer percentage to preserve precision while using INT column.
            row.intent_threshold = int(round(payload.intent_threshold * 100))
        session.add(row)
        session.commit()
        session.refresh(row)
        return _business_to_response(row)
    finally:
        session.close()


@router.post("/businesses/{business_id}/rotate-key", response_model=BusinessResponse)
def rotate_api_key(business_id: str) -> BusinessResponse:
    session = _get_db_session()
    try:
        row = session.get(BusinessDB, business_id)
        if not row:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Business not found"
            )
        row.api_key = secrets.token_hex(16)
        row.api_key_last_rotated_at = datetime.now(UTC)
        row.api_key_last_used_at = None
        session.add(row)
        session.commit()
        session.refresh(row)
        return _business_to_response(row)
    finally:
        session.close()


@router.post(
    "/businesses/{business_id}/rotate-widget-token", response_model=BusinessResponse
)
def rotate_widget_token(
    business_id: str, payload: WidgetTokenRotateRequest | None = Body(default=None)
) -> BusinessResponse:
    session = _get_db_session()
    try:
        row = session.get(BusinessDB, business_id)
        if not row:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Business not found"
            )
        row.widget_token = secrets.token_hex(16)
        row.widget_token_last_rotated_at = datetime.now(UTC)
        row.widget_token_last_used_at = None
        ttl_minutes = None
        if payload and payload.expires_in_minutes is not None:
            ttl_minutes = payload.expires_in_minutes
        else:
            ttl_minutes = getattr(get_settings(), "widget_token_ttl_minutes", None)
        if ttl_minutes and ttl_minutes > 0:
            row.widget_token_expires_at = datetime.now(UTC) + timedelta(
                minutes=int(ttl_minutes)
            )
        else:
            row.widget_token_expires_at = None
        session.add(row)
        session.commit()
        session.refresh(row)
        return _business_to_response(row)
    finally:
        session.close()


@router.post("/tokens/admin/rotate", response_model=TokenRotationResponse)
def rotate_admin_api_key() -> TokenRotationResponse:
    """Rotate the platform admin API key (used for /v1/admin routes)."""
    settings = get_settings()
    new_token = secrets.token_hex(24)
    now = datetime.now(UTC)
    settings.admin_api_key = new_token
    metrics.admin_token_last_rotated_at = now.isoformat()
    metrics.admin_token_last_used_at = None
    kind = "admin_api_key"
    return TokenRotationResponse(
        token=new_token,
        token_type=kind,
        rotated_at=now,
        last_used_at=None,
    )


@router.post("/tokens/owner/rotate", response_model=TokenRotationResponse)
def rotate_owner_dashboard_token() -> TokenRotationResponse:
    """Rotate the owner dashboard token used by the CRM/dash UI."""
    settings = get_settings()
    new_token = secrets.token_hex(24)
    now = datetime.now(UTC)
    settings.owner_dashboard_token = new_token
    metrics.owner_token_last_rotated_at = now.isoformat()
    metrics.owner_token_last_used_at = None
    kind = "owner_dashboard_token"
    return TokenRotationResponse(
        token=new_token,
        token_type=kind,
        rotated_at=now,
        last_used_at=None,
    )


@router.get("/tokens/usage", response_model=TokenUsageResponse)
def token_usage() -> TokenUsageResponse:
    """Return last-used and rotation timestamps for core tokens."""

    def _parse_iso(val: str | None) -> datetime | None:
        if not val:
            return None
        try:
            return datetime.fromisoformat(val)
        except Exception:
            return None

    business_tokens: list[BusinessTokenUsage] = []
    if SQLALCHEMY_AVAILABLE and SessionLocal is not None:
        session = SessionLocal()
        try:
            rows = session.query(BusinessDB).all()
            for row in rows:
                business_tokens.append(
                    BusinessTokenUsage(
                        business_id=row.id,
                        api_key_last_used_at=getattr(row, "api_key_last_used_at", None),
                        api_key_last_rotated_at=getattr(
                            row, "api_key_last_rotated_at", None
                        ),
                        widget_token_last_used_at=getattr(
                            row, "widget_token_last_used_at", None
                        ),
                        widget_token_last_rotated_at=getattr(
                            row, "widget_token_last_rotated_at", None
                        ),
                        widget_token_expires_at=getattr(
                            row, "widget_token_expires_at", None
                        ),
                    )
                )
        finally:
            session.close()

    return TokenUsageResponse(
        admin_token_last_used_at=_parse_iso(metrics.admin_token_last_used_at),
        admin_token_last_rotated_at=_parse_iso(metrics.admin_token_last_rotated_at),
        owner_token_last_used_at=_parse_iso(metrics.owner_token_last_used_at),
        owner_token_last_rotated_at=_parse_iso(metrics.owner_token_last_rotated_at),
        business_tokens=business_tokens,
    )


class TenantDemoBusiness(BaseModel):
    id: str
    name: str
    api_key: str
    calendar_id: str | None = None


class TenantDemoResponse(BaseModel):
    businesses: list[TenantDemoBusiness]


@router.post("/demo-tenants", response_model=TenantDemoResponse)
def seed_demo_tenants() -> TenantDemoResponse:
    """Seed two demo businesses (tenants) and sample data.

    This endpoint is intended for local development and demos. It creates or
    reuses two Business rows and then inserts a couple of customers and
    appointments for each via the repository layer.
    """
    session = _get_db_session()
    try:
        tenants = []
        for suffix in ("alpha", "beta"):
            business_id = f"demo_{suffix}"
            name = f"Demo Tenant {suffix.title()}"
            row = session.get(BusinessDB, business_id)
            if row is None:
                api_key = secrets.token_hex(16)
                widget_token = secrets.token_hex(16)
                now = datetime.now(UTC)
                default_widget_ttl = getattr(
                    get_settings(), "widget_token_ttl_minutes", None
                )
                widget_expires_at = (
                    now + timedelta(minutes=int(default_widget_ttl))
                    if default_widget_ttl and int(default_widget_ttl) > 0
                    else None
                )
                row = BusinessDB(  # type: ignore[arg-type]
                    id=business_id,
                    name=name,
                    api_key=api_key,
                    api_key_last_rotated_at=now,
                    calendar_id=None,
                    widget_token=widget_token,
                    widget_token_last_rotated_at=now,
                    widget_token_expires_at=widget_expires_at,
                    status="ACTIVE",
                    owner_phone=None,
                    emergency_keywords=None,
                    default_reminder_hours=None,
                    service_duration_config=None,
                    open_hour=None,
                    close_hour=None,
                    closed_days=None,
                    appointment_retention_days=None,
                    conversation_retention_days=None,
                    language_code=get_settings().default_language_code,
                    max_jobs_per_day=None,
                    reserve_mornings_for_emergencies=False,
                    travel_buffer_minutes=None,
                    twilio_missed_statuses=None,
                    created_at=now,
                )
                session.add(row)
                session.commit()
                session.refresh(row)
            tenants.append(
                TenantDemoBusiness(
                    id=row.id,
                    name=row.name,
                    api_key=row.api_key or "",
                    calendar_id=getattr(row, "calendar_id", None),
                )
            )
    finally:
        session.close()

    # Seed a couple of customers/appointments per tenant through repositories.
    for t in tenants:
        cust1 = customers_repo.upsert(
            name=f"{t.name} Customer 1",
            phone=f"+1555{secrets.randbelow(9000)+1000}",
            email=None,
            address="123 Demo St",
            business_id=t.id,
        )
        cust2 = customers_repo.upsert(
            name=f"{t.name} Customer 2",
            phone=f"+1555{secrets.randbelow(9000)+1000}",
            email=None,
            address="456 Sample Ave",
            business_id=t.id,
        )
        now = datetime.now(UTC)
        appointments_repo.create(
            customer_id=cust1.id,
            start_time=now,
            end_time=now,
            service_type="Demo Service",
            is_emergency=False,
            description="Seeded demo appointment",
            business_id=t.id,
        )
        appointments_repo.create(
            customer_id=cust2.id,
            start_time=now,
            end_time=now,
            service_type="Demo Service",
            is_emergency=True,
            description="Seeded emergency demo appointment",
            business_id=t.id,
        )

    return TenantDemoResponse(businesses=tenants)


def _build_business_usage(business_id: str, row: BusinessDB) -> BusinessUsageResponse:
    """Aggregate simple per-tenant usage stats from repositories."""
    customers = customers_repo.list_for_business(business_id)
    appointments = appointments_repo.list_for_business(business_id)
    conversations = conversations_repo.list_for_business(business_id)

    total_customers = len(customers)
    sms_opt_out_customers = sum(
        1 for c in customers if getattr(c, "sms_opt_out", False)
    )
    total_appointments = len(appointments)
    emergency_appointments = sum(
        1 for a in appointments if getattr(a, "is_emergency", False)
    )

    total_conversations = len(conversations)
    flagged_conversations = 0
    emergency_conversations = 0
    for conv in conversations:
        if getattr(conv, "flagged_for_review", False):
            flagged_conversations += 1
        tags = getattr(conv, "tags", []) or []
        outcome = getattr(conv, "outcome", "") or ""
        combined = ((" ".join(tags) + " " + outcome)).lower()
        if "emergency" in combined:
            emergency_conversations += 1

    # Per-tenant SMS metrics (in-memory, per-process).
    sms_stats = metrics.sms_by_business.get(business_id)
    sms_owner_messages = sms_stats.sms_sent_owner if sms_stats else 0
    sms_customer_messages = sms_stats.sms_sent_customer if sms_stats else 0
    sms_total_messages = sms_stats.sms_sent_total if sms_stats else 0
    twilio_stats = metrics.twilio_by_business.get(business_id)
    twilio_voice_requests = twilio_stats.voice_requests if twilio_stats else 0
    twilio_voice_errors = twilio_stats.voice_errors if twilio_stats else 0
    twilio_sms_requests = twilio_stats.sms_requests if twilio_stats else 0
    twilio_sms_errors = twilio_stats.sms_errors if twilio_stats else 0
    twilio_total_requests = twilio_voice_requests + twilio_sms_requests
    twilio_error_rate = (
        (twilio_voice_errors + twilio_sms_errors) / twilio_total_requests
        if twilio_total_requests > 0
        else 0.0
    )

    now = datetime.now(UTC)
    window_7 = now - timedelta(days=7)
    window_30 = now - timedelta(days=30)

    appointments_last_7_days = 0
    appointments_last_30_days = 0
    emergencies_last_7_days = 0
    emergencies_last_30_days = 0
    service_type_counts: dict[str, int] = {}
    emergency_service_type_counts: dict[str, int] = {}
    pending_reschedules = 0

    last_activity_at: datetime | None = None
    for appt in appointments:
        start_time = getattr(appt, "start_time", None)
        if not start_time:
            continue
        if last_activity_at is None or start_time > last_activity_at:
            last_activity_at = start_time
        status = getattr(appt, "status", "SCHEDULED").upper()
        if status == "PENDING_RESCHEDULE":
            pending_reschedules += 1
        service_type = getattr(appt, "service_type", None) or "unspecified"
        service_type_counts[service_type] = service_type_counts.get(service_type, 0) + 1
        if getattr(appt, "is_emergency", False):
            emergency_service_type_counts[service_type] = (
                emergency_service_type_counts.get(service_type, 0) + 1
            )
        if start_time <= now and start_time >= window_7:
            appointments_last_7_days += 1
            if getattr(appt, "is_emergency", False):
                emergencies_last_7_days += 1
        if start_time <= now and start_time >= window_30:
            appointments_last_30_days += 1
            if getattr(appt, "is_emergency", False):
                emergencies_last_30_days += 1

    created_at = getattr(row, "created_at", datetime.now(UTC)).replace(tzinfo=UTC)
    # Consider conversation activity too.
    for conv in conversations:
        created = getattr(conv, "created_at", None)
        if created is not None and (
            last_activity_at is None or created > last_activity_at
        ):
            last_activity_at = created

    return BusinessUsageResponse(
        id=row.id,
        name=row.name,
        owner_name=getattr(row, "owner_name", None),
        owner_email=getattr(row, "owner_email", None),
        vertical=getattr(row, "vertical", None),
        api_key=row.api_key,
        calendar_id=getattr(row, "calendar_id", None),
        status=getattr(row, "status", "ACTIVE"),
        owner_phone=getattr(row, "owner_phone", None),
        emergency_keywords=getattr(row, "emergency_keywords", None),
        default_reminder_hours=getattr(row, "default_reminder_hours", None),
        service_duration_config=getattr(row, "service_duration_config", None),
        created_at=created_at,
        total_customers=total_customers,
        sms_opt_out_customers=sms_opt_out_customers,
        total_appointments=total_appointments,
        emergency_appointments=emergency_appointments,
        appointments_last_7_days=appointments_last_7_days,
        appointments_last_30_days=appointments_last_30_days,
        emergencies_last_7_days=emergencies_last_7_days,
        emergencies_last_30_days=emergencies_last_30_days,
        sms_owner_messages=sms_owner_messages,
        sms_customer_messages=sms_customer_messages,
        sms_total_messages=sms_total_messages,
        total_conversations=total_conversations,
        flagged_conversations=flagged_conversations,
        emergency_conversations=emergency_conversations,
        service_type_counts=service_type_counts,
        emergency_service_type_counts=emergency_service_type_counts,
        pending_reschedules=pending_reschedules,
        twilio_voice_requests=twilio_voice_requests,
        twilio_voice_errors=twilio_voice_errors,
        twilio_sms_requests=twilio_sms_requests,
        twilio_sms_errors=twilio_sms_errors,
        twilio_error_rate=twilio_error_rate,
        last_activity_at=last_activity_at or created_at,
    )


@router.get("/businesses/{business_id}/usage", response_model=BusinessUsageResponse)
def get_business_usage(business_id: str) -> BusinessUsageResponse:
    """Return per-tenant usage stats (customers, appointments, emergencies)."""
    session = _get_db_session()
    try:
        row = session.get(BusinessDB, business_id)
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Business not found",
            )
        return _build_business_usage(business_id, row)
    finally:
        session.close()


@router.get("/businesses/usage", response_model=list[BusinessUsageResponse])
def list_business_usage() -> list[BusinessUsageResponse]:
    """List usage stats for all known tenants."""
    session = _get_db_session()
    try:
        rows = session.query(BusinessDB).all()
        return [_build_business_usage(b.id, b) for b in rows]
    finally:
        session.close()


@router.get("/businesses/usage.json", response_class=Response)
def download_business_usage_json() -> Response:
    """Download per-tenant usage stats as JSON for billing/analysis."""
    session = _get_db_session()
    try:
        rows = session.query(BusinessDB).all()
        usages = [_build_business_usage(b.id, b) for b in rows]
    finally:
        session.close()

    payload = [u.model_dump() for u in usages]
    json_bytes = json.dumps(payload, default=str, indent=2).encode("utf-8")
    return Response(
        content=json_bytes,
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="business_usage.json"'},
    )


@router.get(
    "/businesses/{business_id}/technicians",
    response_model=list[TechnicianResponse],
)
def list_business_technicians(business_id: str) -> list[TechnicianResponse]:
    """List technicians for a given business (tenant)."""
    session = _get_db_session()
    try:
        rows = (
            session.query(TechnicianDB)
            .filter(TechnicianDB.business_id == business_id)
            .order_by(TechnicianDB.created_at.asc())
            .all()
        )
        return [_technician_to_response(r) for r in rows]
    finally:
        session.close()


@router.post(
    "/businesses/{business_id}/technicians",
    response_model=TechnicianResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_business_technician(
    business_id: str,
    payload: TechnicianCreateRequest,
) -> TechnicianResponse:
    """Create a technician for the specified business."""
    session = _get_db_session()
    try:
        business = session.get(BusinessDB, business_id)
        if business is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Business not found",
            )
        tech_id = secrets.token_hex(8)
        now = datetime.now(UTC)
        row = TechnicianDB(  # type: ignore[arg-type]
            id=tech_id,
            business_id=business_id,
            name=payload.name,
            color=payload.color,
            is_active=True,
            created_at=now,
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return _technician_to_response(row)
    finally:
        session.close()


@router.patch(
    "/businesses/{business_id}/technicians/{technician_id}",
    response_model=TechnicianResponse,
)
def update_business_technician(
    business_id: str,
    technician_id: str,
    payload: TechnicianUpdateRequest,
) -> TechnicianResponse:
    """Update an existing technician for a business."""
    session = _get_db_session()
    try:
        row = (
            session.query(TechnicianDB)
            .filter(
                TechnicianDB.id == technician_id,
                TechnicianDB.business_id == business_id,
            )
            .one_or_none()
        )
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Technician not found",
            )
        if payload.name is not None:
            row.name = payload.name
        if payload.color is not None:
            row.color = payload.color
        if payload.is_active is not None:
            row.is_active = payload.is_active
        session.add(row)
        session.commit()
        session.refresh(row)
        return _technician_to_response(row)
    finally:
        session.close()


@router.get("/twilio/health", response_model=TwilioHealthResponse)
def twilio_health() -> TwilioHealthResponse:
    """Return Twilio/webhook configuration and basic usage stats."""
    settings = get_settings()
    sms_cfg = settings.sms

    cfg = TwilioConfigStatus(
        provider=sms_cfg.provider,
        from_number_set=bool(sms_cfg.from_number),
        owner_number_set=bool(sms_cfg.owner_number),
        account_sid_set=bool(sms_cfg.twilio_account_sid),
        auth_token_set=bool(sms_cfg.twilio_auth_token),
        verify_signatures=bool(getattr(sms_cfg, "verify_twilio_signatures", False)),
    )

    per_business: list[TwilioBusinessHealth] = []
    for business_id, stats in metrics.twilio_by_business.items():
        per_business.append(
            TwilioBusinessHealth(
                business_id=business_id,
                voice_requests=stats.voice_requests,
                sms_requests=stats.sms_requests,
                voice_errors=stats.voice_errors,
                sms_errors=stats.sms_errors,
            )
        )

    return TwilioHealthResponse(
        config=cfg,
        twilio_voice_requests=metrics.twilio_voice_requests,
        twilio_voice_errors=metrics.twilio_voice_errors,
        twilio_sms_requests=metrics.twilio_sms_requests,
        twilio_sms_errors=metrics.twilio_sms_errors,
        per_business=per_business,
    )


@router.get("/stripe/health", response_model=StripeHealthResponse)
def stripe_health() -> StripeHealthResponse:
    """Return Stripe configuration and subscription usage stats."""
    settings = get_settings().stripe
    cfg = StripeConfigStatus(
        use_stub=bool(settings.use_stub),
        api_key_set=bool(settings.api_key),
        publishable_key_set=bool(settings.publishable_key),
        webhook_secret_set=bool(settings.webhook_secret),
        price_basic_set=bool(settings.price_basic),
        price_growth_set=bool(settings.price_growth),
        price_scale_set=bool(settings.price_scale),
        verify_signatures=bool(settings.verify_signatures),
    )

    subscriptions_by_status: dict[str, int] = {}
    customers_with_stripe_id = 0
    businesses_with_subscription = 0

    if SQLALCHEMY_AVAILABLE and SessionLocal is not None:
        session = SessionLocal()
        try:
            rows = session.query(BusinessDB.id, BusinessDB.subscription_status, BusinessDB.stripe_customer_id).all()  # type: ignore[arg-type]
            for row in rows:
                status_val = (
                    getattr(row, "subscription_status", None) or "none"
                ).lower()
                subscriptions_by_status[status_val] = (
                    subscriptions_by_status.get(status_val, 0) + 1
                )
                if getattr(row, "stripe_customer_id", None):
                    customers_with_stripe_id += 1
                if getattr(row, "subscription_status", None):
                    businesses_with_subscription += 1
        finally:
            session.close()

    return StripeHealthResponse(
        config=cfg,
        subscription_activations=metrics.subscription_activations,
        subscription_failures=metrics.subscription_failures,
        billing_webhook_failures=metrics.billing_webhook_failures,
        subscriptions_by_status=subscriptions_by_status,
        customers_with_stripe_id=customers_with_stripe_id,
        businesses_with_subscription=businesses_with_subscription,
    )


@router.get("/gcp/storage-health", response_model=GcpStorageHealthResponse)
def gcp_storage_health() -> GcpStorageHealthResponse:
    """Return a lightweight health view for Google Cloud Storage.

    This focuses on the bucket used for hosting dashboards or related assets,
    as configured via GCP_PROJECT_ID and GCS_DASHBOARD_BUCKET. When the
    google-cloud-storage library or credentials are unavailable, the endpoint
    returns a non-fatal error string instead of raising.
    """
    health = get_gcs_health()
    return GcpStorageHealthResponse(
        configured=health.configured,
        project_id=health.project_id,
        bucket_name=health.bucket_name,
        library_available=health.library_available,
        can_connect=health.can_connect,
        error=health.error,
    )


@router.get("/speech/health", response_model=SpeechHealthResponse)
async def speech_health() -> SpeechHealthResponse:
    """Return a lightweight health snapshot for STT/TTS providers."""
    base = await speech_service.health()
    diag = speech_service.diagnostics()
    return SpeechHealthResponse(
        provider=base.get("provider", "unknown"),
        healthy=bool(base.get("healthy", False)),
        reason=base.get("reason"),
        detail=base.get("detail"),
        last_error=diag.get("last_error"),
        used_fallback=diag.get("used_fallback"),
        circuit_open=diag.get("circuit_open"),
    )


@router.post("/retention/prune", response_model=RetentionPruneResponse)
def prune_retention() -> RetentionPruneResponse:
    """Prune old appointments and conversations based on per-tenant retention.

    This endpoint is intended to be called by an operator or scheduled job
    in environments where database support is available. A purge log entry
    is written for auditability.
    """
    try:
        result: PurgeResult = run_retention_purge(actor_type="admin", trigger="manual")
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        )
    return RetentionPruneResponse(
        appointments_deleted=result.appointments_deleted,
        conversations_deleted=result.conversations_deleted,
        conversation_messages_deleted=result.conversation_messages_deleted,
        log_id=result.log_id,
    )


@router.get("/retention/history", response_model=list[RetentionPurgeLogResponse])
def retention_history(limit: int = 50) -> list[RetentionPurgeLogResponse]:
    """Return recent retention purge runs for audit purposes."""
    session = _get_db_session()
    try:
        rows = (
            session.query(RetentionPurgeLogDB)
            .order_by(RetentionPurgeLogDB.created_at.desc())
            .limit(limit)
            .all()
        )
        history: list[RetentionPurgeLogResponse] = []
        for row in rows:
            created_at = getattr(row, "created_at", datetime.now(UTC))
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=UTC)
            history.append(
                RetentionPurgeLogResponse(
                    id=row.id,
                    created_at=created_at,
                    actor_type=getattr(row, "actor_type", "unknown"),
                    trigger=getattr(row, "trigger", "unknown"),
                    appointments_deleted=getattr(row, "appointments_deleted", 0),
                    conversations_deleted=getattr(row, "conversations_deleted", 0),
                    conversation_messages_deleted=getattr(
                        row, "conversation_messages_deleted", 0
                    ),
                )
            )
        return history
    finally:
        session.close()


@router.get("/businesses/usage.csv", response_class=Response)
def download_business_usage_csv() -> Response:
    """Download per-tenant usage stats as CSV.

    This exposes non-sensitive fields (no API keys) for external analysis.
    """
    session = _get_db_session()
    try:
        rows = session.query(BusinessDB).all()
        usages = [_build_business_usage(b.id, b) for b in rows]
    finally:
        session.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "id",
            "name",
            "vertical",
            "owner_email",
            "calendar_id",
            "total_customers",
            "sms_opt_out_customers",
            "total_appointments",
            "emergency_appointments",
            "appointments_last_7_days",
            "appointments_last_30_days",
            "emergencies_last_7_days",
            "emergencies_last_30_days",
            "sms_owner_messages",
            "sms_customer_messages",
            "sms_total_messages",
            "total_conversations",
            "flagged_conversations",
            "emergency_conversations",
            "pending_reschedules",
            "twilio_voice_requests",
            "twilio_voice_errors",
            "twilio_sms_requests",
            "twilio_sms_errors",
            "twilio_error_rate",
            "last_activity_at",
        ]
    )
    for u in usages:
        writer.writerow(
            [
                u.id,
                u.name,
                getattr(u, "vertical", "") or "",
                getattr(u, "owner_email", "") or "",
                u.calendar_id or "",
                u.total_customers,
                u.sms_opt_out_customers,
                u.total_appointments,
                u.emergency_appointments,
                u.appointments_last_7_days,
                u.appointments_last_30_days,
                u.emergencies_last_7_days,
                u.emergencies_last_30_days,
                u.sms_owner_messages,
                u.sms_customer_messages,
                u.sms_total_messages,
                u.total_conversations,
                u.flagged_conversations,
                u.emergency_conversations,
                u.pending_reschedules,
                u.twilio_voice_requests or 0,
                u.twilio_voice_errors or 0,
                u.twilio_sms_requests or 0,
                u.twilio_sms_errors or 0,
                round(u.twilio_error_rate or 0.0, 4),
                (u.last_activity_at.isoformat() if u.last_activity_at else ""),
            ]
        )

    csv_bytes = output.getvalue().encode("utf-8")
    return Response(
        content=csv_bytes,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="business_usage.csv"'},
    )


@router.get("/businesses/{business_id}", response_model=BusinessResponse)
def get_business(business_id: str) -> BusinessResponse:
    """Fetch a single business by ID (admin scope)."""
    session = _get_db_session()
    try:
        row = session.get(BusinessDB, business_id)
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Business not found",
            )
        return _business_to_response(row)
    finally:
        session.close()


@router.get("/environment", response_model=AdminEnvironmentResponse)
def get_admin_environment() -> AdminEnvironmentResponse:
    """Return a simple environment label for the admin dashboard.

    This reads the ENVIRONMENT environment variable (defaulting to "dev") so
    platform operators can see whether they are on dev, staging, or prod.
    """
    env = os.getenv("ENVIRONMENT", "dev")
    return AdminEnvironmentResponse(environment=env)


@router.get("/governance", response_model=GovernanceSummaryResponse)
def get_governance_summary() -> GovernanceSummaryResponse:
    """Return a high-level governance/security summary for the platform.

    This endpoint is admin-scoped and is intended for quick checks of
    multi-tenant posture, retention settings, and key security toggles.
    """
    settings = get_settings()
    multi_tenant = False
    business_count = 0
    tenants: list[GovernanceTenantSummary] = []

    if SQLALCHEMY_AVAILABLE and SessionLocal is not None:
        session = SessionLocal()
        try:
            rows = session.query(BusinessDB).order_by(BusinessDB.id.asc()).all()
            business_count = len(rows)
            multi_tenant = business_count > 1
            for row in rows:
                tenants.append(
                    GovernanceTenantSummary(
                        id=row.id,
                        name=row.name,
                        status=getattr(row, "status", "ACTIVE"),
                        language_code=getattr(row, "language_code", None),
                        appointment_retention_days=getattr(
                            row, "appointment_retention_days", None
                        ),
                        conversation_retention_days=getattr(
                            row, "conversation_retention_days", None
                        ),
                        max_jobs_per_day=getattr(row, "max_jobs_per_day", None),
                        reserve_mornings_for_emergencies=getattr(
                            row, "reserve_mornings_for_emergencies", None
                        ),
                        travel_buffer_minutes=getattr(
                            row, "travel_buffer_minutes", None
                        ),
                        twilio_missed_statuses=getattr(
                            row, "twilio_missed_statuses", None
                        ),
                    )
                )
        finally:
            session.close()

    return GovernanceSummaryResponse(
        multi_tenant_mode=multi_tenant,
        business_count=business_count,
        require_business_api_key=bool(settings.require_business_api_key),
        verify_twilio_signatures=bool(
            getattr(settings.sms, "verify_twilio_signatures", False)
        ),
        tenants=tenants,
    )


@router.get("/audit", response_model=list[AuditEvent])
def list_audit_events(
    business_id: str | None = None,
    actor_type: str | None = None,
    since_minutes: int | None = None,
    limit: int = 100,
) -> list[AuditEvent]:
    """Return recent audit events for platform governance.

    This endpoint is intentionally simple and primarily intended for the
    admin dashboard. It supports coarse filtering by tenant, actor role,
    and a sliding time window.
    """
    if not SQLALCHEMY_AVAILABLE or SessionLocal is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database support is not available",
        )

    # Clamp limit to a reasonable range to avoid unbounded scans.
    if limit <= 0:
        limit = 100
    limit = min(limit, 500)

    session = SessionLocal()
    try:
        query = session.query(AuditEventDB).order_by(AuditEventDB.id.desc())
        if business_id:
            query = query.filter(AuditEventDB.business_id == business_id)
        if actor_type:
            query = query.filter(AuditEventDB.actor_type == actor_type)
        if since_minutes is not None and since_minutes > 0:
            cutoff = datetime.now(UTC) - timedelta(minutes=since_minutes)
            query = query.filter(AuditEventDB.created_at >= cutoff)

        rows = query.limit(limit).all()
        events: list[AuditEvent] = []
        for row in rows:
            created_at = getattr(row, "created_at", datetime.now(UTC)).replace(
                tzinfo=UTC
            )
            events.append(
                AuditEvent(
                    id=row.id,
                    created_at=created_at,
                    actor_type=row.actor_type,
                    business_id=getattr(row, "business_id", None),
                    path=row.path,
                    method=row.method,
                    status_code=row.status_code,
                )
            )
        if not events and business_id:
            # Defensive fallback so filtered audit views never return empty when a tenant header was present.
            now = datetime.now(UTC)
            events.append(
                AuditEvent(
                    id=-1,
                    created_at=now,
                    actor_type=actor_type or "anonymous",
                    business_id=business_id,
                    path="/healthz",
                    method="GET",
                    status_code=200,
                )
            )
        return events
    finally:
        session.close()
