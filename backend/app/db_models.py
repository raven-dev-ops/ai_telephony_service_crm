from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text

from .db import Base, SQLALCHEMY_AVAILABLE


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _new_id() -> str:
    return str(uuid4())


if SQLALCHEMY_AVAILABLE:

    class BusinessDB(Base):  # type: ignore[misc]
        __tablename__ = "businesses"

        id = Column(String, primary_key=True)  # type: ignore[call-arg]
        name = Column(String, nullable=False)  # type: ignore[call-arg]
        vertical = Column(String, nullable=True)  # type: ignore[call-arg]
        api_key = Column(String, nullable=True, index=True)  # type: ignore[call-arg]
        calendar_id = Column(String, nullable=True)  # type: ignore[call-arg]
        status = Column(String, nullable=False, default="ACTIVE")  # type: ignore[call-arg]
        owner_phone = Column(String, nullable=True)  # type: ignore[call-arg]
        twilio_phone_number = Column(String, nullable=True)  # type: ignore[call-arg]
        emergency_keywords = Column(String, nullable=True)  # type: ignore[call-arg]
        default_reminder_hours = Column(Integer, nullable=True)  # type: ignore[call-arg]
        service_duration_config = Column(String, nullable=True)  # type: ignore[call-arg]
        open_hour = Column(Integer, nullable=True)  # type: ignore[call-arg]
        close_hour = Column(Integer, nullable=True)  # type: ignore[call-arg]
        closed_days = Column(String, nullable=True)  # type: ignore[call-arg]
        appointment_retention_days = Column(Integer, nullable=True)  # type: ignore[call-arg]
        conversation_retention_days = Column(Integer, nullable=True)  # type: ignore[call-arg]
        language_code = Column(String, nullable=True)  # type: ignore[call-arg]
        max_jobs_per_day = Column(Integer, nullable=True)  # type: ignore[call-arg]
        reserve_mornings_for_emergencies = Column(
            Boolean, default=True, nullable=False
        )  # type: ignore[call-arg]
        travel_buffer_minutes = Column(Integer, nullable=True)  # type: ignore[call-arg]
        twilio_missed_statuses = Column(String, nullable=True)  # type: ignore[call-arg]
        intent_threshold = Column(Integer, nullable=True)  # type: ignore[call-arg]
        created_at = Column(DateTime, nullable=False, default=_utcnow, index=True)  # type: ignore[call-arg]
        widget_token = Column(String, nullable=True, index=True)  # type: ignore[call-arg]
        retention_enabled = Column(Boolean, nullable=True, default=True)  # type: ignore[call-arg]
        retention_sms_template = Column(Text, nullable=True)  # type: ignore[call-arg]
        zip_code = Column(String(255), nullable=True)  # type: ignore[call-arg]
        median_household_income = Column(Integer, nullable=True)  # type: ignore[call-arg]
        owner_name = Column(String(255), nullable=True)  # type: ignore[call-arg]
        owner_email = Column(String(255), nullable=True)  # type: ignore[call-arg]
        owner_profile_image_url = Column(String(1024), nullable=True)  # type: ignore[call-arg]
        service_tier = Column(String(64), nullable=True)  # type: ignore[call-arg]
        tts_voice = Column(String(64), nullable=True)  # type: ignore[call-arg]
        terms_accepted_at = Column(DateTime, nullable=True)  # type: ignore[call-arg]
        privacy_accepted_at = Column(DateTime, nullable=True)  # type: ignore[call-arg]
        integration_linkedin_status = Column(String(32), nullable=True)  # type: ignore[call-arg]
        integration_gmail_status = Column(String(32), nullable=True)  # type: ignore[call-arg]
        integration_gcalendar_status = Column(String(32), nullable=True)  # type: ignore[call-arg]
        integration_openai_status = Column(String(32), nullable=True)  # type: ignore[call-arg]
        integration_twilio_status = Column(String(32), nullable=True)  # type: ignore[call-arg]
        integration_qbo_status = Column(String(32), nullable=True)  # type: ignore[call-arg]
        gcalendar_access_token = Column(Text, nullable=True)  # type: ignore[call-arg]
        gcalendar_refresh_token = Column(Text, nullable=True)  # type: ignore[call-arg]
        gcalendar_token_expires_at = Column(DateTime, nullable=True)  # type: ignore[call-arg]
        gmail_access_token = Column(Text, nullable=True)  # type: ignore[call-arg]
        gmail_refresh_token = Column(Text, nullable=True)  # type: ignore[call-arg]
        gmail_token_expires_at = Column(DateTime, nullable=True)  # type: ignore[call-arg]
        qbo_realm_id = Column(String(128), nullable=True)  # type: ignore[call-arg]
        qbo_access_token = Column(Text, nullable=True)  # type: ignore[call-arg]
        qbo_refresh_token = Column(Text, nullable=True)  # type: ignore[call-arg]
        qbo_token_expires_at = Column(DateTime, nullable=True)  # type: ignore[call-arg]
        onboarding_step = Column(String(64), nullable=True)  # type: ignore[call-arg]
        onboarding_completed = Column(Boolean, default=False)  # type: ignore[call-arg]
        stripe_customer_id = Column(String(255), nullable=True)  # type: ignore[call-arg]
        stripe_subscription_id = Column(String(255), nullable=True)  # type: ignore[call-arg]
        subscription_status = Column(String(64), nullable=True)  # type: ignore[call-arg]
        subscription_current_period_end = Column(DateTime, nullable=True)  # type: ignore[call-arg]

    class UserDB(Base):  # type: ignore[misc]
        __tablename__ = "users"

        id = Column(String, primary_key=True, default=_new_id)  # type: ignore[call-arg]
        email = Column(String, nullable=False, unique=True, index=True)  # type: ignore[call-arg]
        password_hash = Column(String, nullable=True)  # type: ignore[call-arg]
        name = Column(String, nullable=True)  # type: ignore[call-arg]
        active_business_id = Column(String, nullable=True)  # type: ignore[call-arg]
        created_at = Column(DateTime, nullable=False, default=_utcnow, index=True)  # type: ignore[call-arg]
        failed_login_attempts = Column(Integer, nullable=False, default=0)  # type: ignore[call-arg]
        lockout_until = Column(DateTime, nullable=True)  # type: ignore[call-arg]
        reset_token_hash = Column(String, nullable=True, index=True)  # type: ignore[call-arg]
        reset_token_expires_at = Column(DateTime, nullable=True, index=True)  # type: ignore[call-arg]

    class BusinessUserDB(Base):  # type: ignore[misc]
        __tablename__ = "business_users"

        id = Column(String, primary_key=True, default=_new_id)  # type: ignore[call-arg]
        business_id = Column(String, nullable=False, index=True)  # type: ignore[call-arg]
        user_id = Column(String, nullable=False, index=True)  # type: ignore[call-arg]
        role = Column(String, nullable=False, default="owner")  # type: ignore[call-arg]

    class BusinessInviteDB(Base):  # type: ignore[misc]
        __tablename__ = "business_invites"

        id = Column(String, primary_key=True, default=_new_id)  # type: ignore[call-arg]
        business_id = Column(String, nullable=False, index=True)  # type: ignore[call-arg]
        email = Column(String, nullable=False, index=True)  # type: ignore[call-arg]
        role = Column(String, nullable=False, default="staff")  # type: ignore[call-arg]
        token_hash = Column(String, nullable=False, index=True)  # type: ignore[call-arg]
        created_at = Column(DateTime, nullable=False, default=_utcnow, index=True)  # type: ignore[call-arg]
        expires_at = Column(DateTime, nullable=True, index=True)  # type: ignore[call-arg]
        accepted_at = Column(DateTime, nullable=True, index=True)  # type: ignore[call-arg]
        accepted_by_user_id = Column(String, nullable=True, index=True)  # type: ignore[call-arg]
        created_by_user_id = Column(String, nullable=True, index=True)  # type: ignore[call-arg]

    class CustomerDB(Base):  # type: ignore[misc]
        __tablename__ = "customers"

        id = Column(String, primary_key=True)  # type: ignore[call-arg]
        name = Column(String, nullable=False)  # type: ignore[call-arg]
        phone = Column(String, nullable=False, index=True)  # type: ignore[call-arg]
        email = Column(String, nullable=True)  # type: ignore[call-arg]
        address = Column(String, nullable=True)  # type: ignore[call-arg]
        business_id = Column(String, nullable=False, index=True)  # type: ignore[call-arg]
        created_at = Column(DateTime, nullable=False, default=_utcnow, index=True)  # type: ignore[call-arg]
        sms_opt_out = Column(Boolean, nullable=False, default=False)  # type: ignore[call-arg]
        tags = Column(String, nullable=True)  # type: ignore[call-arg]

    class AppointmentDB(Base):  # type: ignore[misc]
        __tablename__ = "appointments"

        id = Column(String, primary_key=True)  # type: ignore[call-arg]
        customer_id = Column(String, nullable=False, index=True)  # type: ignore[call-arg]
        start_time = Column(DateTime, nullable=False, index=True)  # type: ignore[call-arg]
        end_time = Column(DateTime, nullable=False)  # type: ignore[call-arg]
        service_type = Column(String, nullable=True)  # type: ignore[call-arg]
        description = Column(String, nullable=True)  # type: ignore[call-arg]
        is_emergency = Column(Boolean, nullable=False, default=False)  # type: ignore[call-arg]
        status = Column(String, nullable=False, default="SCHEDULED")  # type: ignore[call-arg]
        lead_source = Column(String, nullable=True)  # type: ignore[call-arg]
        estimated_value = Column(Integer, nullable=True)  # type: ignore[call-arg]
        job_stage = Column(String, nullable=True)  # type: ignore[call-arg]
        quoted_value = Column(Integer, nullable=True)  # type: ignore[call-arg]
        quote_status = Column(String, nullable=True)  # type: ignore[call-arg]
        business_id = Column(String, nullable=False, index=True)  # type: ignore[call-arg]
        created_at = Column(DateTime, nullable=False, default=_utcnow, index=True)  # type: ignore[call-arg]
        reminder_sent = Column(Boolean, nullable=False, default=False)  # type: ignore[call-arg]
        calendar_event_id = Column(String, nullable=True)  # type: ignore[call-arg]
        tags = Column(String, nullable=True)  # type: ignore[call-arg]
        technician_id = Column(String, nullable=True)  # type: ignore[call-arg]

    class ConversationDB(Base):  # type: ignore[misc]
        __tablename__ = "conversations"

        id = Column(String, primary_key=True)  # type: ignore[call-arg]
        channel = Column(String, nullable=False)  # type: ignore[call-arg]
        customer_id = Column(String, nullable=True, index=True)  # type: ignore[call-arg]
        session_id = Column(String, nullable=True, index=True)  # type: ignore[call-arg]
        business_id = Column(String, nullable=False, index=True)  # type: ignore[call-arg]
        created_at = Column(DateTime, nullable=False, default=_utcnow, index=True)  # type: ignore[call-arg]
        intent = Column(String, nullable=True)  # type: ignore[call-arg]
        intent_confidence = Column(Integer, nullable=True)  # type: ignore[call-arg]

    class ConversationMessageDB(Base):  # type: ignore[misc]
        __tablename__ = "conversation_messages"

        id = Column(String, primary_key=True)  # type: ignore[call-arg]
        conversation_id = Column(String, nullable=False, index=True)  # type: ignore[call-arg]
        role = Column(String, nullable=False)  # type: ignore[call-arg]
        text = Column(String, nullable=False)  # type: ignore[call-arg]
        timestamp = Column(DateTime, nullable=False, default=_utcnow, index=True)  # type: ignore[call-arg]

    class RetentionPurgeLogDB(Base):  # type: ignore[misc]
        __tablename__ = "retention_purge_logs"

        id = Column(Integer, primary_key=True, autoincrement=True)  # type: ignore[call-arg]
        created_at = Column(DateTime, nullable=False, default=_utcnow, index=True)  # type: ignore[call-arg]
        actor_type = Column(String, nullable=False)  # type: ignore[call-arg]
        trigger = Column(String, nullable=False)  # type: ignore[call-arg]
        appointments_deleted = Column(Integer, nullable=False, default=0)  # type: ignore[call-arg]
        conversations_deleted = Column(Integer, nullable=False, default=0)  # type: ignore[call-arg]
        conversation_messages_deleted = Column(Integer, nullable=False, default=0)  # type: ignore[call-arg]

    class TechnicianDB(Base):  # type: ignore[misc]
        __tablename__ = "technicians"

        id = Column(String, primary_key=True, default=_new_id)  # type: ignore[call-arg]
        business_id = Column(String, nullable=False, index=True)  # type: ignore[call-arg]
        name = Column(String, nullable=False)  # type: ignore[call-arg]
        color = Column(String, nullable=True)  # type: ignore[call-arg]
        is_active = Column(Boolean, nullable=False, default=True)  # type: ignore[call-arg]
        created_at = Column(DateTime, nullable=False, default=_utcnow, index=True)  # type: ignore[call-arg]

    class AuditEventDB(Base):  # type: ignore[misc]
        __tablename__ = "audit_events"

        id = Column(Integer, primary_key=True, autoincrement=True)  # type: ignore[call-arg]
        created_at = Column(DateTime, nullable=False, default=_utcnow, index=True)  # type: ignore[call-arg]
        actor_type = Column(String, nullable=False)  # type: ignore[call-arg]
        business_id = Column(String, nullable=True, index=True)  # type: ignore[call-arg]
        path = Column(String, nullable=False)  # type: ignore[call-arg]
        method = Column(String, nullable=False)  # type: ignore[call-arg]
        status_code = Column(Integer, nullable=False)  # type: ignore[call-arg]

    class SmsAuditDB(Base):  # type: ignore[misc]
        __tablename__ = "sms_audit"

        id = Column(Integer, primary_key=True, autoincrement=True)  # type: ignore[call-arg]
        created_at = Column(DateTime, default=_utcnow, nullable=False, index=True)  # type: ignore[call-arg]
        business_id = Column(String, nullable=True, index=True)  # type: ignore[call-arg]
        phone = Column(String, nullable=False, index=True)  # type: ignore[call-arg]
        direction = Column(String, nullable=False)  # type: ignore[call-arg]
        message = Column(String, nullable=True)  # type: ignore[call-arg]
        event = Column(String, nullable=False)  # type: ignore[call-arg]  # "opt_out", "opt_in", "sent", "blocked"

else:  # pragma: no cover - for environments without SQLAlchemy

    class BusinessDB:  # type: ignore[misc]
        __tablename__ = "businesses"
        id: str
        api_key: str
        widget_token: str
        status: str
        twilio_phone_number: str | None
        intent_threshold: int | None

    class UserDB:  # type: ignore[misc]
        __tablename__ = "users"
        id: str
        email: str

    class BusinessUserDB:  # type: ignore[misc]
        __tablename__ = "business_users"
        id: str
        business_id: str
        user_id: str
        role: str

    class BusinessInviteDB:  # type: ignore[misc]
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

    class CustomerDB:  # type: ignore[misc]
        __tablename__ = "customers"
        id: str
        name: str
        phone: str
        business_id: str
        sms_opt_out: bool

    class AppointmentDB:  # type: ignore[misc]
        __tablename__ = "appointments"
        id: str
        customer_id: str
        business_id: str

    class ConversationDB:  # type: ignore[misc]
        __tablename__ = "conversations"
        id: str
        business_id: str
        intent: str | None
        intent_confidence: int | None

    class ConversationMessageDB:  # type: ignore[misc]
        __tablename__ = "conversation_messages"
        id: str
        conversation_id: str

    class RetentionPurgeLogDB:  # type: ignore[misc]
        __tablename__ = "retention_purge_logs"
        id: int

    class TechnicianDB:  # type: ignore[misc]
        __tablename__ = "technicians"
        id: str
        business_id: str

    class AuditEventDB:  # type: ignore[misc]
        __tablename__ = "audit_events"
        id: int

    class SmsAuditDB:  # type: ignore[misc]
        __tablename__ = "sms_audit"
        id: int
