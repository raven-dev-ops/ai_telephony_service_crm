from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import List, Optional
from uuid import uuid4


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass
class Business:
    id: str
    name: str
    created_at: datetime = field(default_factory=_utcnow)


@dataclass
class Customer:
    id: str
    name: str
    phone: str
    email: Optional[str] = None
    address: Optional[str] = None
    business_id: str = "default_business"
    created_at: datetime = field(default_factory=_utcnow)
    sms_opt_out: bool = False
    tags: List[str] = field(default_factory=list)


@dataclass
class Appointment:
    id: str
    customer_id: str
    start_time: datetime
    end_time: datetime
    service_type: Optional[str] = None
    description: Optional[str] = None
    is_emergency: bool = False
    status: str = "SCHEDULED"
    lead_source: Optional[str] = None
    estimated_value: Optional[float] = None
    job_stage: Optional[str] = None
    quoted_value: Optional[float] = None
    quote_status: Optional[str] = None
    business_id: str = "default_business"
    created_at: datetime = field(default_factory=_utcnow)
    reminder_sent: bool = False
    calendar_event_id: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    technician_id: Optional[str] = None


@dataclass
class Conversation:
    id: str
    channel: str  # "phone", "web", "sms", etc.
    customer_id: Optional[str] = None
    session_id: Optional[str] = None
    business_id: str = "default_business"
    created_at: datetime = field(default_factory=_utcnow)
    flagged_for_review: bool = False
    tags: List[str] = field(default_factory=list)
    outcome: Optional[str] = None
    notes: Optional[str] = None
    messages: List["ConversationMessage"] = field(default_factory=list)


@dataclass
class ConversationMessage:
    role: str  # "user" or "assistant"
    text: str
    timestamp: datetime = field(default_factory=_utcnow)


def new_customer_id() -> str:
    return str(uuid4())


def new_appointment_id() -> str:
    return str(uuid4())


def new_conversation_id() -> str:
    return str(uuid4())


def new_technician_id() -> str:
    return str(uuid4())
