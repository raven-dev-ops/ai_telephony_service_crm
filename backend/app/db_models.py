from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text

from .db import Base, SQLALCHEMY_AVAILABLE


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _new_id() -> str:
    return str(uuid4())


if TYPE_CHECKING or SQLALCHEMY_AVAILABLE:

    class BusinessDB(Base):
        __tablename__ = "businesses"

        id = Column(String, primary_key=True)
        name = Column(String, nullable=False)
        vertical = Column(String, nullable=True)
        api_key = Column(String, nullable=True, index=True)
        api_key_last_used_at = Column(DateTime, nullable=True)
        api_key_last_rotated_at = Column(DateTime, nullable=True)
        calendar_id = Column(String, nullable=True)
        status = Column(String, nullable=False, default="ACTIVE")
        owner_phone = Column(String, nullable=True)
        twilio_phone_number = Column(String, nullable=True)
        emergency_keywords = Column(String, nullable=True)
        default_reminder_hours = Column(Integer, nullable=True)
        service_duration_config = Column(String, nullable=True)
        open_hour = Column(Integer, nullable=True)
        close_hour = Column(Integer, nullable=True)
        closed_days = Column(String, nullable=True)
        appointment_retention_days = Column(Integer, nullable=True)
        conversation_retention_days = Column(Integer, nullable=True)
        language_code = Column(String, nullable=True)
        max_jobs_per_day = Column(Integer, nullable=True)
        reserve_mornings_for_emergencies = Column(
            Boolean, default=False, nullable=False
        )
        travel_buffer_minutes = Column(Integer, nullable=True)
        twilio_missed_statuses = Column(String, nullable=True)
        intent_threshold = Column(Integer, nullable=True)
        created_at = Column(DateTime, nullable=False, default=_utcnow, index=True)
        widget_token = Column(String, nullable=True, index=True)
        widget_token_last_used_at = Column(DateTime, nullable=True)
        widget_token_last_rotated_at = Column(DateTime, nullable=True)
        widget_token_expires_at = Column(DateTime, nullable=True)
        retention_enabled = Column(Boolean, nullable=True, default=True)
        retention_sms_template = Column(Text, nullable=True)
        zip_code = Column(String(255), nullable=True)
        median_household_income = Column(Integer, nullable=True)
        owner_name = Column(String(255), nullable=True)
        owner_email = Column(String(255), nullable=True)
        owner_profile_image_url = Column(String(1024), nullable=True)
        service_tier = Column(String(64), nullable=True)
        tts_voice = Column(String(64), nullable=True)
        terms_accepted_at = Column(DateTime, nullable=True)
        privacy_accepted_at = Column(DateTime, nullable=True)
        integration_linkedin_status = Column(String(32), nullable=True)
        integration_gmail_status = Column(String(32), nullable=True)
        integration_gcalendar_status = Column(String(32), nullable=True)
        integration_openai_status = Column(String(32), nullable=True)
        integration_twilio_status = Column(String(32), nullable=True)
        integration_qbo_status = Column(String(32), nullable=True)
        gcalendar_access_token = Column(Text, nullable=True)
        gcalendar_refresh_token = Column(Text, nullable=True)
        gcalendar_token_expires_at = Column(DateTime, nullable=True)
        gmail_access_token = Column(Text, nullable=True)
        gmail_refresh_token = Column(Text, nullable=True)
        gmail_token_expires_at = Column(DateTime, nullable=True)
        owner_email_alerts_enabled = Column(Boolean, nullable=True)
        qbo_realm_id = Column(String(128), nullable=True)
        qbo_access_token = Column(Text, nullable=True)
        qbo_refresh_token = Column(Text, nullable=True)
        qbo_token_expires_at = Column(DateTime, nullable=True)
        onboarding_step = Column(String(64), nullable=True)
        onboarding_completed = Column(Boolean, default=False)
        lockdown_mode = Column(Boolean, nullable=True)
        stripe_customer_id = Column(String(255), nullable=True)
        stripe_subscription_id = Column(String(255), nullable=True)
        subscription_status = Column(String(64), nullable=True)
        subscription_current_period_end = Column(DateTime, nullable=True)

    class UserDB(Base):
        __tablename__ = "users"

        id = Column(String, primary_key=True, default=_new_id)
        email = Column(String, nullable=False, unique=True, index=True)
        password_hash = Column(String, nullable=True)
        name = Column(String, nullable=True)
        active_business_id = Column(String, nullable=True)
        created_at = Column(DateTime, nullable=False, default=_utcnow, index=True)
        failed_login_attempts = Column(Integer, nullable=False, default=0)
        lockout_until = Column(DateTime, nullable=True)
        reset_token_hash = Column(String, nullable=True, index=True)
        reset_token_expires_at = Column(DateTime, nullable=True, index=True)

    class BusinessUserDB(Base):
        __tablename__ = "business_users"

        id = Column(String, primary_key=True, default=_new_id)
        business_id = Column(String, nullable=False, index=True)
        user_id = Column(String, nullable=False, index=True)
        role = Column(String, nullable=False, default="owner")

    class BusinessInviteDB(Base):
        __tablename__ = "business_invites"

        id = Column(String, primary_key=True, default=_new_id)
        business_id = Column(String, nullable=False, index=True)
        email = Column(String, nullable=False, index=True)
        role = Column(String, nullable=False, default="staff")
        token_hash = Column(String, nullable=False, index=True)
        created_at = Column(DateTime, nullable=False, default=_utcnow, index=True)
        expires_at = Column(DateTime, nullable=True, index=True)
        accepted_at = Column(DateTime, nullable=True, index=True)
        accepted_by_user_id = Column(String, nullable=True, index=True)
        created_by_user_id = Column(String, nullable=True, index=True)

    class CustomerDB(Base):
        __tablename__ = "customers"

        id = Column(String, primary_key=True)
        name = Column(String, nullable=False)
        phone = Column(String, nullable=False, index=True)
        email = Column(String, nullable=True)
        address = Column(String, nullable=True)
        business_id = Column(String, nullable=False, index=True)
        created_at = Column(DateTime, nullable=False, default=_utcnow, index=True)
        sms_opt_out = Column(Boolean, nullable=False, default=False)
        tags = Column(String, nullable=True)

    class AppointmentDB(Base):
        __tablename__ = "appointments"

        id = Column(String, primary_key=True)
        customer_id = Column(String, nullable=False, index=True)
        start_time = Column(DateTime, nullable=False, index=True)
        end_time = Column(DateTime, nullable=False)
        service_type = Column(String, nullable=True)
        description = Column(String, nullable=True)
        is_emergency = Column(Boolean, nullable=False, default=False)
        status = Column(String, nullable=False, default="SCHEDULED")
        lead_source = Column(String, nullable=True)
        estimated_value = Column(Integer, nullable=True)
        job_stage = Column(String, nullable=True)
        quoted_value = Column(Integer, nullable=True)
        quote_status = Column(String, nullable=True)
        business_id = Column(String, nullable=False, index=True)
        created_at = Column(DateTime, nullable=False, default=_utcnow, index=True)
        reminder_sent = Column(Boolean, nullable=False, default=False)
        calendar_event_id = Column(String, nullable=True)
        tags = Column(String, nullable=True)
        technician_id = Column(String, nullable=True)

    class ConversationDB(Base):
        __tablename__ = "conversations"

        id = Column(String, primary_key=True)
        channel = Column(String, nullable=False)
        customer_id = Column(String, nullable=True, index=True)
        session_id = Column(String, nullable=True, index=True)
        business_id = Column(String, nullable=False, index=True)
        created_at = Column(DateTime, nullable=False, default=_utcnow, index=True)
        intent = Column(String, nullable=True)
        intent_confidence = Column(Integer, nullable=True)

    class ConversationMessageDB(Base):
        __tablename__ = "conversation_messages"

        id = Column(String, primary_key=True)
        conversation_id = Column(String, nullable=False, index=True)
        role = Column(String, nullable=False)
        text = Column(String, nullable=False)
        timestamp = Column(DateTime, nullable=False, default=_utcnow, index=True)

    class RetentionPurgeLogDB(Base):
        __tablename__ = "retention_purge_logs"

        id = Column(Integer, primary_key=True, autoincrement=True)
        created_at = Column(DateTime, nullable=False, default=_utcnow, index=True)
        actor_type = Column(String, nullable=False)
        trigger = Column(String, nullable=False)
        appointments_deleted = Column(Integer, nullable=False, default=0)
        conversations_deleted = Column(Integer, nullable=False, default=0)
        conversation_messages_deleted = Column(Integer, nullable=False, default=0)

    class TechnicianDB(Base):
        __tablename__ = "technicians"

        id = Column(String, primary_key=True, default=_new_id)
        business_id = Column(String, nullable=False, index=True)
        name = Column(String, nullable=False)
        color = Column(String, nullable=True)
        is_active = Column(Boolean, nullable=False, default=True)
        created_at = Column(DateTime, nullable=False, default=_utcnow, index=True)

    class AuditEventDB(Base):
        __tablename__ = "audit_events"

        id = Column(Integer, primary_key=True, autoincrement=True)
        created_at = Column(DateTime, nullable=False, default=_utcnow, index=True)
        actor_type = Column(String, nullable=False)
        business_id = Column(String, nullable=True, index=True)
        path = Column(String, nullable=False)
        method = Column(String, nullable=False)
        status_code = Column(Integer, nullable=False)

    class SmsAuditDB(Base):
        __tablename__ = "sms_audit"

        id = Column(Integer, primary_key=True, autoincrement=True)
        created_at = Column(DateTime, default=_utcnow, nullable=False, index=True)
        business_id = Column(String, nullable=True, index=True)
        phone = Column(String, nullable=False, index=True)
        direction = Column(String, nullable=False)
        message = Column(String, nullable=True)
        event = Column(String, nullable=False)  # "opt_out", "opt_in", "sent", "blocked"

else:  # pragma: no cover - for environments without SQLAlchemy

    class BusinessDB:
        __tablename__ = "businesses"
        id: str
        api_key: str
        api_key_last_used_at: datetime | None
        api_key_last_rotated_at: datetime | None
        widget_token: str
        widget_token_last_used_at: datetime | None
        widget_token_last_rotated_at: datetime | None
        widget_token_expires_at: datetime | None
        status: str
        twilio_phone_number: str | None
        intent_threshold: int | None

    class UserDB:
        __tablename__ = "users"
        id: str
        email: str

    class BusinessUserDB:
        __tablename__ = "business_users"
        id: str
        business_id: str
        user_id: str
        role: str

    class BusinessInviteDB:
        __tablename__ = "business_invites"
        id: str
        business_id: str
        email: str
        role: str
        token_hash: str
        created_at: datetime
        expires_at: datetime
        accepted_at: datetime | None
        accepted_by_user_id: str | None
        created_by_user_id: str | None

    class CustomerDB:
        __tablename__ = "customers"
        id: str
        name: str
        phone: str
        business_id: str
        sms_opt_out: bool

    class AppointmentDB:
        __tablename__ = "appointments"
        id: str
        customer_id: str
        business_id: str

    class ConversationDB:
        __tablename__ = "conversations"
        id: str
        business_id: str
        intent: str | None
        intent_confidence: int | None

    class ConversationMessageDB:
        __tablename__ = "conversation_messages"
        id: str
        conversation_id: str

    class RetentionPurgeLogDB:
        __tablename__ = "retention_purge_logs"
        id: int

    class TechnicianDB:
        __tablename__ = "technicians"
        id: str
        business_id: str

    class AuditEventDB:
        __tablename__ = "audit_events"
        id: int

    class SmsAuditDB:
        __tablename__ = "sms_audit"
        id: int
