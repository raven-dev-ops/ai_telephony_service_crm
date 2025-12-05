from __future__ import annotations

from datetime import UTC, datetime

from .db import Base, SQLALCHEMY_AVAILABLE

if SQLALCHEMY_AVAILABLE:
    from sqlalchemy import Boolean, Column, DateTime, Integer, String  # type: ignore


def _utcnow() -> datetime:
    return datetime.now(UTC)


if SQLALCHEMY_AVAILABLE:

    class Business(Base):  # type: ignore[misc]
        __tablename__ = "businesses"

        id = Column(String, primary_key=True, index=True)  # type: ignore[call-arg]
        name = Column(String, nullable=False)  # type: ignore[call-arg]
        vertical = Column(String, nullable=True)  # type: ignore[call-arg]
        api_key = Column(String, nullable=True, index=True)  # type: ignore[call-arg]
        calendar_id = Column(String, nullable=True)  # type: ignore[call-arg]
        status = Column(String, default="ACTIVE", nullable=False)  # type: ignore[call-arg]
        owner_phone = Column(String, nullable=True)  # type: ignore[call-arg]
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
        reserve_mornings_for_emergencies = Column(Boolean, default=False, nullable=False)  # type: ignore[call-arg]
        travel_buffer_minutes = Column(Integer, nullable=True)  # type: ignore[call-arg]
        twilio_missed_statuses = Column(String, nullable=True)  # type: ignore[call-arg]
        retention_enabled = Column(Boolean, default=True, nullable=False)  # type: ignore[call-arg]
        retention_sms_template = Column(String, nullable=True)  # type: ignore[call-arg]
        created_at = Column(DateTime, default=_utcnow, nullable=False)  # type: ignore[call-arg]
        widget_token = Column(String, nullable=True, index=True)  # type: ignore[call-arg]


    class CustomerDB(Base):  # type: ignore[misc]
        __tablename__ = "customers"

        id = Column(String, primary_key=True, index=True)  # type: ignore[call-arg]
        name = Column(String, nullable=False)  # type: ignore[call-arg]
        phone = Column(String, nullable=False, index=True)  # type: ignore[call-arg]
        email = Column(String, nullable=True)  # type: ignore[call-arg]
        address = Column(String, nullable=True)  # type: ignore[call-arg]
        business_id = Column(String, nullable=False, index=True)  # type: ignore[call-arg]
        created_at = Column(DateTime, default=_utcnow, nullable=False)  # type: ignore[call-arg]
        sms_opt_out = Column(Boolean, default=False, nullable=False)  # type: ignore[call-arg]
        tags = Column(String, nullable=True)  # type: ignore[call-arg]


    class AppointmentDB(Base):  # type: ignore[misc]
        __tablename__ = "appointments"

        id = Column(String, primary_key=True, index=True)  # type: ignore[call-arg]
        customer_id = Column(String, nullable=False, index=True)  # type: ignore[call-arg]
        start_time = Column(DateTime, nullable=False)  # type: ignore[call-arg]
        end_time = Column(DateTime, nullable=False)  # type: ignore[call-arg]
        service_type = Column(String, nullable=True)  # type: ignore[call-arg]
        description = Column(String, nullable=True)  # type: ignore[call-arg]
        is_emergency = Column(Boolean, default=False, nullable=False)  # type: ignore[call-arg]
        status = Column(String, default="SCHEDULED", nullable=False)  # type: ignore[call-arg]
        lead_source = Column(String, nullable=True)  # type: ignore[call-arg]
        estimated_value = Column(Integer, nullable=True)  # type: ignore[call-arg]
        job_stage = Column(String, nullable=True)  # type: ignore[call-arg]
        quoted_value = Column(Integer, nullable=True)  # type: ignore[call-arg]
        quote_status = Column(String, nullable=True)  # type: ignore[call-arg]
        business_id = Column(String, nullable=False, index=True)  # type: ignore[call-arg]
        created_at = Column(DateTime, default=_utcnow, nullable=False)  # type: ignore[call-arg]
        reminder_sent = Column(Boolean, default=False, nullable=False)  # type: ignore[call-arg]
        calendar_event_id = Column(String, nullable=True)  # type: ignore[call-arg]
        tags = Column(String, nullable=True)  # type: ignore[call-arg]
        technician_id = Column(String, nullable=True, index=True)  # type: ignore[call-arg]


    class TechnicianDB(Base):  # type: ignore[misc]
        __tablename__ = "technicians"

        id = Column(String, primary_key=True, index=True)  # type: ignore[call-arg]
        business_id = Column(String, nullable=False, index=True)  # type: ignore[call-arg]
        name = Column(String, nullable=False)  # type: ignore[call-arg]
        color = Column(String, nullable=True)  # type: ignore[call-arg]
        is_active = Column(Boolean, default=True, nullable=False)  # type: ignore[call-arg]
        created_at = Column(DateTime, default=_utcnow, nullable=False)  # type: ignore[call-arg]


    class ConversationDB(Base):  # type: ignore[misc]
        __tablename__ = "conversations"

        id = Column(String, primary_key=True, index=True)  # type: ignore[call-arg]
        channel = Column(String, nullable=False)  # type: ignore[call-arg]
        customer_id = Column(String, nullable=True, index=True)  # type: ignore[call-arg]
        session_id = Column(String, nullable=True, index=True)  # type: ignore[call-arg]
        business_id = Column(String, nullable=False, index=True)  # type: ignore[call-arg]
        created_at = Column(DateTime, default=_utcnow, nullable=False)  # type: ignore[call-arg]


    class ConversationMessageDB(Base):  # type: ignore[misc]
        __tablename__ = "conversation_messages"

        id = Column(String, primary_key=True, index=True)  # type: ignore[call-arg]
        conversation_id = Column(String, nullable=False, index=True)  # type: ignore[call-arg]
        role = Column(String, nullable=False)  # type: ignore[call-arg]
        text = Column(String, nullable=False)  # type: ignore[call-arg]
        timestamp = Column(DateTime, default=_utcnow, nullable=False)  # type: ignore[call-arg]


    class AuditEventDB(Base):  # type: ignore[misc]
        __tablename__ = "audit_events"

        id = Column(Integer, primary_key=True, autoincrement=True)  # type: ignore[call-arg]
        created_at = Column(DateTime, default=_utcnow, nullable=False, index=True)  # type: ignore[call-arg]
        actor_type = Column(String, nullable=False, index=True)  # type: ignore[call-arg]
        business_id = Column(String, nullable=True, index=True)  # type: ignore[call-arg]
        path = Column(String, nullable=False)  # type: ignore[call-arg]
        method = Column(String, nullable=False)  # type: ignore[call-arg]
        status_code = Column(Integer, nullable=False)  # type: ignore[call-arg]

else:

    class Business:  # pragma: no cover - placeholder when SQLAlchemy missing
        id: str
        name: str
        vertical: str | None
        api_key: str | None
        calendar_id: str | None
        status: str
        owner_phone: str | None
        emergency_keywords: str | None
        default_reminder_hours: int | None
        service_duration_config: str | None
        created_at: datetime
        widget_token: str | None
        open_hour: int | None
        close_hour: int | None
        closed_days: str | None
        appointment_retention_days: int | None
        conversation_retention_days: int | None
        language_code: str | None
        max_jobs_per_day: int | None
        reserve_mornings_for_emergencies: bool
        travel_buffer_minutes: int | None
        twilio_missed_statuses: str | None
        retention_enabled: bool
        retention_sms_template: str | None

    class CustomerDB:  # pragma: no cover - placeholder
        id: str
        name: str
        phone: str
        email: str | None
        address: str | None
        business_id: str
        created_at: datetime
        sms_opt_out: bool
        tags: str | None

    class AppointmentDB:  # pragma: no cover - placeholder
        id: str
        customer_id: str
        start_time: datetime
        end_time: datetime
        service_type: str | None
        description: str | None
        is_emergency: bool
        status: str
        lead_source: str | None
        estimated_value: int | None
        job_stage: str | None
        quoted_value: int | None
        quote_status: str | None
        business_id: str
        created_at: datetime
        reminder_sent: bool
        calendar_event_id: str | None
        tags: str | None
        technician_id: str | None

    class TechnicianDB:  # pragma: no cover - placeholder
        id: str
        business_id: str
        name: str
        color: str | None
        is_active: bool
        created_at: datetime

    class ConversationDB:  # pragma: no cover - placeholder
        id: str
        channel: str
        customer_id: str | None
        session_id: str | None
        business_id: str
        created_at: datetime

    class ConversationMessageDB:  # pragma: no cover - placeholder
        id: str
        conversation_id: str
        role: str
        text: str
        timestamp: datetime

    class AuditEventDB:  # pragma: no cover - placeholder
        id: int
        created_at: datetime
        actor_type: str
        business_id: str | None
        path: str
        method: str
        status_code: int
