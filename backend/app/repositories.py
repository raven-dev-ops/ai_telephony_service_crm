from __future__ import annotations

from typing import Dict, List, Optional
import os

from .config import get_settings
from .db import SQLALCHEMY_AVAILABLE, SessionLocal
from .db_models import (
    AppointmentDB,
    BusinessDB,
    ConversationDB,
    ConversationMessageDB,
    CustomerDB,
)
from .models import (
    Appointment,
    Conversation,
    ConversationMessage,
    Customer,
    new_appointment_id,
    new_conversation_id,
    new_customer_id,
)
from .services.privacy import redact_text


def _split_tags(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [t.strip() for t in str(raw).split(",") if t.strip()]


def _join_tags(tags: list[str] | None) -> str | None:
    if not tags:
        return None
    cleaned = [t.strip() for t in tags if t and t.strip()]
    return ",".join(cleaned) if cleaned else None


def _capture_transcripts_allowed(business_id: str | None) -> bool:
    """Return whether transcripts should be stored for a tenant."""
    settings = get_settings()
    if not getattr(settings, "capture_transcripts", True):
        return False
    if not business_id or not (SQLALCHEMY_AVAILABLE and SessionLocal is not None):
        return True
    session = SessionLocal()
    try:
        row = session.get(BusinessDB, business_id)
        if row is not None and getattr(row, "retention_enabled", True) is False:
            return False
    except Exception:
        return True
    finally:
        session.close()
    return True


class InMemoryCustomerRepository:
    def __init__(self) -> None:
        self._by_id: Dict[str, Customer] = {}
        self._by_phone: Dict[str, str] = {}
        self._by_business: Dict[str, List[str]] = {}

    def upsert(
        self,
        name: str,
        phone: str,
        email: str | None = None,
        address: str | None = None,
        business_id: str = "default_business",
        tags: list[str] | None = None,
    ) -> Customer:
        existing = self.get_by_phone(phone, business_id=business_id)
        if existing:
            if name:
                existing.name = name
            if email:
                existing.email = email
            if address:
                existing.address = address
            if tags is not None:
                existing.tags = list(tags)
            self._by_id[existing.id] = existing
            return existing

        customer = Customer(
            id=new_customer_id(),
            name=name,
            phone=phone,
            email=email,
            address=address,
            business_id=business_id,
            tags=list(tags or []),
        )
        self._by_id[customer.id] = customer
        self._by_phone[phone] = customer.id
        self._by_business.setdefault(business_id, []).append(customer.id)
        return customer

    def get(self, customer_id: str) -> Optional[Customer]:
        return self._by_id.get(customer_id)

    def get_by_phone(
        self, phone: str, business_id: str | None = None
    ) -> Optional[Customer]:
        # When a business_id is provided, restrict the lookup to that tenant.
        if business_id is not None:
            ids = self._by_business.get(business_id, [])
            for cid in ids:
                c = self._by_id.get(cid)
                if c and c.phone == phone:
                    return c
            return None
        # Fallback: use the last customer stored for this phone across tenants.
        customer_id = self._by_phone.get(phone)
        if not customer_id:
            return None
        return self._by_id.get(customer_id)

    def list_all(self) -> List[Customer]:
        return list(self._by_id.values())

    def list_for_business(self, business_id: str) -> List[Customer]:
        ids = self._by_business.get(business_id, [])
        return [self._by_id[i] for i in ids]

    def delete(self, customer_id: str) -> None:
        """Delete a customer and remove from indexes."""
        customer = self._by_id.pop(customer_id, None)
        if not customer:
            return
        for phone, cid in list(self._by_phone.items()):
            if cid == customer_id:
                self._by_phone.pop(phone, None)
        for biz, ids in list(self._by_business.items()):
            self._by_business[biz] = [cid for cid in ids if cid != customer_id]

    def set_sms_opt_out(
        self,
        phone: str,
        business_id: str,
        opt_out: bool = True,
    ) -> None:
        customer = self.get_by_phone(phone, business_id=business_id)
        if not customer:
            return
        customer.sms_opt_out = opt_out


class InMemoryAppointmentRepository:
    def __init__(self) -> None:
        self._by_id: Dict[str, Appointment] = {}
        self._by_customer: Dict[str, List[str]] = {}
        self._by_business: Dict[str, List[str]] = {}

    def create(
        self,
        customer_id: str,
        start_time,
        end_time,
        service_type: str | None,
        is_emergency: bool,
        description: str | None = None,
        lead_source: str | None = None,
        estimated_value: int | None = None,
        job_stage: str | None = None,
        business_id: str = "default_business",
        calendar_event_id: str | None = None,
        tags: list[str] | None = None,
        technician_id: str | None = None,
        quoted_value: float | None = None,
        quote_status: str | None = None,
    ) -> Appointment:
        appointment = Appointment(
            id=new_appointment_id(),
            customer_id=customer_id,
            start_time=start_time,
            end_time=end_time,
            service_type=service_type,
            description=description,
            is_emergency=is_emergency,
            lead_source=lead_source,
            estimated_value=(
                float(estimated_value) if estimated_value is not None else None
            ),
            job_stage=job_stage,
            quoted_value=float(quoted_value) if quoted_value is not None else None,
            quote_status=quote_status,
            business_id=business_id,
            calendar_event_id=calendar_event_id,
            tags=list(tags or []),
            technician_id=technician_id,
        )
        self._by_id[appointment.id] = appointment
        self._by_customer.setdefault(customer_id, []).append(appointment.id)
        self._by_business.setdefault(business_id, []).append(appointment.id)
        return appointment

    def list_for_customer(self, customer_id: str) -> List[Appointment]:
        ids = self._by_customer.get(customer_id, [])
        return [self._by_id[i] for i in ids]

    def list_all(self) -> List[Appointment]:
        return list(self._by_id.values())

    def list_for_business(self, business_id: str) -> List[Appointment]:
        ids = self._by_business.get(business_id, [])
        return [self._by_id[i] for i in ids]

    def delete_for_customer(self, customer_id: str) -> None:
        """Delete appointments for a customer and clean indexes."""
        ids = self._by_customer.pop(customer_id, [])
        for appt_id in ids:
            self._by_id.pop(appt_id, None)
        for biz, appts in list(self._by_business.items()):
            self._by_business[biz] = [aid for aid in appts if aid not in ids]

    def get(self, appointment_id: str) -> Optional[Appointment]:
        return self._by_id.get(appointment_id)

    def find_by_calendar_event(
        self, calendar_event_id: str, *, business_id: str | None = None
    ) -> Optional[Appointment]:
        """Return the first appointment matching a calendar_event_id for a tenant."""
        if business_id:
            ids = self._by_business.get(business_id, [])
            for appt_id in ids:
                appt = self._by_id.get(appt_id)
                if (
                    appt
                    and getattr(appt, "calendar_event_id", None) == calendar_event_id
                ):
                    return appt
        else:
            for appt in self._by_id.values():
                if getattr(appt, "calendar_event_id", None) == calendar_event_id:
                    return appt
        return None

    def update(
        self,
        appointment_id: str,
        *,
        start_time=None,
        end_time=None,
        service_type: str | None = None,
        description: str | None = None,
        is_emergency: Optional[bool] = None,
        status: str | None = None,
        lead_source: str | None = None,
        estimated_value: Optional[int] = None,
        job_stage: str | None = None,
        tags: list[str] | None = None,
        technician_id: str | None = None,
        quoted_value: Optional[float] = None,
        quote_status: str | None = None,
    ) -> Optional[Appointment]:
        appt = self._by_id.get(appointment_id)
        if not appt:
            return None
        if start_time is not None:
            appt.start_time = start_time
        if end_time is not None:
            appt.end_time = end_time
        if service_type is not None:
            appt.service_type = service_type
        if description is not None:
            appt.description = description
        if is_emergency is not None:
            appt.is_emergency = is_emergency
        if status is not None:
            appt.status = status
        if lead_source is not None:
            appt.lead_source = lead_source
        if estimated_value is not None:
            appt.estimated_value = float(estimated_value)
        if job_stage is not None:
            appt.job_stage = job_stage
        if tags is not None:
            appt.tags = list(tags)
        if quoted_value is not None:
            appt.quoted_value = float(quoted_value)
        if quote_status is not None:
            appt.quote_status = quote_status
        if technician_id is not None:
            appt.technician_id = technician_id
        return appt


class InMemoryConversationRepository:
    def __init__(self) -> None:
        self._by_id: Dict[str, Conversation] = {}
        self._by_session: Dict[str, str] = {}
        self._by_business: Dict[str, List[str]] = {}

    def create(
        self,
        channel: str,
        customer_id: str | None = None,
        session_id: str | None = None,
        business_id: str = "default_business",
    ) -> Conversation:
        conv = Conversation(
            id=new_conversation_id(),
            channel=channel,
            customer_id=customer_id,
            session_id=session_id,
            business_id=business_id,
            intent=None,
            intent_confidence=None,
        )
        self._by_id[conv.id] = conv
        if session_id:
            self._by_session[session_id] = conv.id
        self._by_business.setdefault(business_id, []).append(conv.id)
        return conv

    def get(self, conversation_id: str) -> Optional[Conversation]:
        return self._by_id.get(conversation_id)

    def get_by_session(self, session_id: str) -> Optional[Conversation]:
        conv_id = self._by_session.get(session_id)
        if not conv_id:
            return None
        return self._by_id.get(conv_id)

    def list_for_customer(self, customer_id: str) -> List[Conversation]:
        return [c for c in self._by_id.values() if c.customer_id == customer_id]

    def delete_for_customer(self, customer_id: str) -> None:
        """Delete all conversations for a customer."""
        for conv_id, conv in list(self._by_id.items()):
            if conv.customer_id == customer_id:
                self._by_id.pop(conv_id, None)
                if conv.session_id:
                    self._by_session.pop(conv.session_id, None)
                if conv.business_id in self._by_business:
                    self._by_business[conv.business_id] = [
                        cid
                        for cid in self._by_business[conv.business_id]
                        if cid != conv_id
                    ]

    def append_message(self, conversation_id: str, role: str, text: str) -> None:
        conv = self._by_id.get(conversation_id)
        if not conv:
            return
        if not _capture_transcripts_allowed(getattr(conv, "business_id", None)):
            return
        conv.messages.append(ConversationMessage(role=role, text=redact_text(text)))

    def set_intent(
        self, conversation_id: str, intent: str | None, confidence: float | None
    ) -> None:
        conv = self._by_id.get(conversation_id)
        if not conv:
            return
        conv.intent = intent
        conv.intent_confidence = confidence
        self._by_id[conversation_id] = conv

    def list_all(self) -> List[Conversation]:
        return list(self._by_id.values())

    def list_for_business(self, business_id: str) -> List[Conversation]:
        ids = self._by_business.get(business_id, [])
        return [self._by_id[i] for i in ids]


class DbCustomerRepository:
    """Customer repository backed by the SQLAlchemy database.

    This implementation is opt-in and selected via USE_DB_CUSTOMERS when
    SQLAlchemy and a SessionLocal are available.
    """

    def _to_model(self, row: CustomerDB) -> Customer:
        return Customer(
            id=row.id,
            name=row.name,
            phone=row.phone,
            email=row.email,
            address=row.address,
            business_id=row.business_id,
            created_at=row.created_at,
            sms_opt_out=getattr(row, "sms_opt_out", False),
            tags=_split_tags(getattr(row, "tags", None)),
        )

    def upsert(
        self,
        name: str,
        phone: str,
        email: str | None = None,
        address: str | None = None,
        business_id: str = "default_business",
        tags: list[str] | None = None,
    ) -> Customer:
        if SessionLocal is None:
            raise RuntimeError("Database session factory is not available")
        session = SessionLocal()
        try:
            row = (
                session.query(CustomerDB)
                .filter(
                    CustomerDB.phone == phone,
                    CustomerDB.business_id == business_id,
                )
                .one_or_none()
            )
            if row is None:
                row = CustomerDB(
                    id=new_customer_id(),
                    name=name,
                    phone=phone,
                    email=email,
                    address=address,
                    business_id=business_id,
                    sms_opt_out=False,
                    tags=_join_tags(tags or []),
                )  # type: ignore[call-arg]
                session.add(row)
            else:
                if name:
                    row.name = name
                if email is not None:
                    row.email = email
                if address is not None:
                    row.address = address
                if tags is not None:
                    row.tags = _join_tags(tags)
                session.add(row)
            session.commit()
            session.refresh(row)
            return self._to_model(row)
        finally:
            session.close()

    def set_sms_opt_out(
        self,
        phone: str,
        business_id: str,
        opt_out: bool = True,
        reason: str | None = None,
    ) -> None:
        if SessionLocal is None:
            raise RuntimeError("Database session factory is not available")
        session = SessionLocal()
        try:
            row = (
                session.query(CustomerDB)
                .filter(
                    CustomerDB.phone == phone,
                    CustomerDB.business_id == business_id,
                )
                .one_or_none()
            )
            if not row:
                return
            row.sms_opt_out = opt_out
            session.add(row)
            session.commit()
        finally:
            session.close()

    def get(self, customer_id: str) -> Optional[Customer]:
        if SessionLocal is None:
            raise RuntimeError("Database session factory is not available")
        session = SessionLocal()
        try:
            row = session.get(CustomerDB, customer_id)
            return self._to_model(row) if row else None
        finally:
            session.close()

    def get_by_phone(
        self, phone: str, business_id: str = "default_business"
    ) -> Optional[Customer]:
        if SessionLocal is None:
            raise RuntimeError("Database session factory is not available")
        session = SessionLocal()
        try:
            row = (
                session.query(CustomerDB)
                .filter(
                    CustomerDB.phone == phone,
                    CustomerDB.business_id == business_id,
                )
                .one_or_none()
            )
            return self._to_model(row) if row else None
        finally:
            session.close()

    def list_all(self) -> List[Customer]:
        if SessionLocal is None:
            raise RuntimeError("Database session factory is not available")
        session = SessionLocal()
        try:
            rows = session.query(CustomerDB).all()
            return [self._to_model(r) for r in rows]
        finally:
            session.close()

    def list_for_business(self, business_id: str) -> List[Customer]:
        if SessionLocal is None:
            raise RuntimeError("Database session factory is not available")
        session = SessionLocal()
        try:
            rows = (
                session.query(CustomerDB)
                .filter(CustomerDB.business_id == business_id)
                .all()
            )
            return [self._to_model(r) for r in rows]
        finally:
            session.close()

    def delete(self, customer_id: str) -> None:
        if SessionLocal is None:
            raise RuntimeError("Database session factory is not available")
        session = SessionLocal()
        try:
            row = session.get(CustomerDB, customer_id)
            if row:
                session.delete(row)
                session.commit()
        finally:
            session.close()


USE_DB_CUSTOMERS = os.getenv("USE_DB_CUSTOMERS", "false").lower() == "true"

if USE_DB_CUSTOMERS and SQLALCHEMY_AVAILABLE and SessionLocal is not None:
    customers_repo = DbCustomerRepository()
else:
    customers_repo = InMemoryCustomerRepository()


class DbAppointmentRepository:
    """Appointment repository backed by the SQLAlchemy database."""

    def _to_model(self, row: AppointmentDB) -> Appointment:
        return Appointment(
            id=row.id,
            customer_id=row.customer_id,
            start_time=row.start_time,
            end_time=row.end_time,
            service_type=row.service_type,
            description=row.description,
            is_emergency=row.is_emergency,
            status=row.status,
            lead_source=getattr(row, "lead_source", None),
            estimated_value=getattr(row, "estimated_value", None),
            job_stage=getattr(row, "job_stage", None),
            quoted_value=getattr(row, "quoted_value", None),
            quote_status=getattr(row, "quote_status", None),
            business_id=row.business_id,
            created_at=row.created_at,
            reminder_sent=row.reminder_sent,
            calendar_event_id=getattr(row, "calendar_event_id", None),
            tags=_split_tags(getattr(row, "tags", None)),
            technician_id=getattr(row, "technician_id", None),
        )

    def create(
        self,
        customer_id: str,
        start_time,
        end_time,
        service_type: str | None,
        is_emergency: bool,
        description: str | None = None,
        lead_source: str | None = None,
        estimated_value: int | None = None,
        job_stage: str | None = None,
        business_id: str = "default_business",
        calendar_event_id: str | None = None,
        tags: list[str] | None = None,
        technician_id: str | None = None,
        quoted_value: int | None = None,
        quote_status: str | None = None,
    ) -> Appointment:
        if SessionLocal is None:
            raise RuntimeError("Database session factory is not available")
        session = SessionLocal()
        try:
            row = AppointmentDB(
                id=new_appointment_id(),
                customer_id=customer_id,
                start_time=start_time,
                end_time=end_time,
                service_type=service_type,
                description=description,
                is_emergency=is_emergency,
                status="SCHEDULED",
                lead_source=lead_source,
                estimated_value=estimated_value,
                job_stage=job_stage,
                quoted_value=quoted_value,
                quote_status=quote_status,
                business_id=business_id,
                reminder_sent=False,
                calendar_event_id=calendar_event_id,
                tags=_join_tags(tags or []),
                technician_id=technician_id,
            )  # type: ignore[call-arg]
            session.add(row)
            session.commit()
            session.refresh(row)
            return self._to_model(row)
        finally:
            session.close()

    def list_for_customer(self, customer_id: str) -> List[Appointment]:
        if SessionLocal is None:
            raise RuntimeError("Database session factory is not available")
        session = SessionLocal()
        try:
            rows = (
                session.query(AppointmentDB)
                .filter(AppointmentDB.customer_id == customer_id)
                .all()
            )
            return [self._to_model(r) for r in rows]
        finally:
            session.close()

    def list_all(self) -> List[Appointment]:
        if SessionLocal is None:
            raise RuntimeError("Database session factory is not available")
        session = SessionLocal()
        try:
            rows = session.query(AppointmentDB).all()
            return [self._to_model(r) for r in rows]
        finally:
            session.close()

    def list_for_business(self, business_id: str) -> List[Appointment]:
        if SessionLocal is None:
            raise RuntimeError("Database session factory is not available")
        session = SessionLocal()
        try:
            rows = (
                session.query(AppointmentDB)
                .filter(AppointmentDB.business_id == business_id)
                .all()
            )
            return [self._to_model(r) for r in rows]
        finally:
            session.close()

    def delete_for_customer(self, customer_id: str) -> None:
        if SessionLocal is None:
            raise RuntimeError("Database session factory is not available")
        session = SessionLocal()
        try:
            session.query(AppointmentDB).filter(
                AppointmentDB.customer_id == customer_id
            ).delete()
            session.commit()
        finally:
            session.close()

    def get(self, appointment_id: str) -> Optional[Appointment]:
        if SessionLocal is None:
            raise RuntimeError("Database session factory is not available")
        session = SessionLocal()
        try:
            row = session.get(AppointmentDB, appointment_id)
            if not row:
                return None
            return self._to_model(row)
        finally:
            session.close()

    def find_by_calendar_event(
        self, calendar_event_id: str, *, business_id: str | None = None
    ) -> Optional[Appointment]:
        """Return the first appointment matching a calendar_event_id for a tenant."""
        if SessionLocal is None:
            raise RuntimeError("Database session factory is not available")
        session = SessionLocal()
        try:
            query = session.query(AppointmentDB).filter(
                AppointmentDB.calendar_event_id == calendar_event_id
            )
            if business_id:
                query = query.filter(AppointmentDB.business_id == business_id)
            row = query.first()
            return self._to_model(row) if row else None
        finally:
            session.close()

    def update(
        self,
        appointment_id: str,
        *,
        start_time=None,
        end_time=None,
        service_type: str | None = None,
        description: str | None = None,
        is_emergency: Optional[bool] = None,
        status: str | None = None,
        lead_source: str | None = None,
        estimated_value: Optional[int] = None,
        job_stage: str | None = None,
        tags: list[str] | None = None,
        technician_id: str | None = None,
        quoted_value: Optional[int] = None,
        quote_status: str | None = None,
    ) -> Optional[Appointment]:
        if SessionLocal is None:
            raise RuntimeError("Database session factory is not available")
        session = SessionLocal()
        try:
            row = session.get(AppointmentDB, appointment_id)
            if not row:
                return None
            if start_time is not None:
                row.start_time = start_time
            if end_time is not None:
                row.end_time = end_time
            if service_type is not None:
                row.service_type = service_type
            if description is not None:
                row.description = description
            if is_emergency is not None:
                row.is_emergency = is_emergency
            if status is not None:
                row.status = status
            if lead_source is not None:
                row.lead_source = lead_source
            if estimated_value is not None:
                row.estimated_value = estimated_value
            if job_stage is not None:
                row.job_stage = job_stage
            if tags is not None:
                row.tags = _join_tags(tags)
            if quoted_value is not None:
                row.quoted_value = quoted_value
            if quote_status is not None:
                row.quote_status = quote_status
            if technician_id is not None:
                row.technician_id = technician_id
            session.add(row)
            session.commit()
            session.refresh(row)
            return self._to_model(row)
        finally:
            session.close()


USE_DB_APPOINTMENTS = os.getenv("USE_DB_APPOINTMENTS", "false").lower() == "true"

if USE_DB_APPOINTMENTS and SQLALCHEMY_AVAILABLE and SessionLocal is not None:
    appointments_repo = DbAppointmentRepository()
else:
    appointments_repo = InMemoryAppointmentRepository()


class DbConversationRepository:
    """Conversation repository backed by the SQLAlchemy database."""

    def _to_model(
        self, row: ConversationDB, messages: List[ConversationMessageDB]
    ) -> Conversation:
        raw_conf = getattr(row, "intent_confidence", None)
        conf_val: float | None = None
        try:
            if raw_conf is not None:
                conf_val = float(raw_conf)
                if conf_val > 1:
                    conf_val = conf_val / 100.0
        except Exception:
            conf_val = None
        return Conversation(
            id=row.id,
            channel=row.channel,
            customer_id=row.customer_id,
            session_id=row.session_id,
            business_id=row.business_id,
            created_at=row.created_at,
            intent=getattr(row, "intent", None),
            intent_confidence=conf_val,
            messages=[
                ConversationMessage(role=m.role, text=m.text, timestamp=m.timestamp)
                for m in messages
            ],
        )

    def create(
        self,
        channel: str,
        customer_id: str | None = None,
        session_id: str | None = None,
        business_id: str = "default_business",
    ) -> Conversation:
        if SessionLocal is None:
            raise RuntimeError("Database session factory is not available")
        session = SessionLocal()
        try:
            row = ConversationDB(
                id=new_conversation_id(),
                channel=channel,
                customer_id=customer_id,
                session_id=session_id,
                business_id=business_id,
                intent=None,
                intent_confidence=None,
            )  # type: ignore[call-arg]
            session.add(row)
            session.commit()
            session.refresh(row)
            return self._to_model(row, [])
        finally:
            session.close()

    def get(self, conversation_id: str) -> Optional[Conversation]:
        if SessionLocal is None:
            raise RuntimeError("Database session factory is not available")
        session = SessionLocal()
        try:
            row = session.get(ConversationDB, conversation_id)
            if not row:
                return None
            messages = (
                session.query(ConversationMessageDB)
                .filter(ConversationMessageDB.conversation_id == conversation_id)
                .order_by(ConversationMessageDB.timestamp.asc())
                .all()
            )
            return self._to_model(row, messages)
        finally:
            session.close()

    def get_by_session(self, session_id: str) -> Optional[Conversation]:
        if SessionLocal is None:
            raise RuntimeError("Database session factory is not available")
        session = SessionLocal()
        try:
            row = (
                session.query(ConversationDB)
                .filter(ConversationDB.session_id == session_id)
                .one_or_none()
            )
            if not row:
                return None
            messages = (
                session.query(ConversationMessageDB)
                .filter(ConversationMessageDB.conversation_id == row.id)
                .order_by(ConversationMessageDB.timestamp.asc())
                .all()
            )
            return self._to_model(row, messages)
        finally:
            session.close()

    def append_message(self, conversation_id: str, role: str, text: str) -> None:
        if SessionLocal is None:
            raise RuntimeError("Database session factory is not available")
        session = SessionLocal()
        try:
            conv_row = session.get(ConversationDB, conversation_id)
            if not conv_row:
                return
            if not _capture_transcripts_allowed(getattr(conv_row, "business_id", None)):
                return
            msg = ConversationMessageDB(
                id=new_conversation_id(),
                conversation_id=conversation_id,
                role=role,
                text=redact_text(text),
            )  # type: ignore[call-arg]
            session.add(msg)
            session.commit()
        finally:
            session.close()

    def set_intent(
        self, conversation_id: str, intent: str | None, confidence: float | None
    ) -> None:
        if SessionLocal is None:
            raise RuntimeError("Database session factory is not available")
        session = SessionLocal()
        try:
            row = session.get(ConversationDB, conversation_id)
            if not row:
                return
            row.intent = intent  # type: ignore[assignment]
            if confidence is None:
                row.intent_confidence = None  # type: ignore[assignment]
            else:
                row.intent_confidence = int(round(confidence * 100))  # type: ignore[assignment]
            session.add(row)
            session.commit()
        finally:
            session.close()

    def list_all(self) -> List[Conversation]:
        if SessionLocal is None:
            raise RuntimeError("Database session factory is not available")
        session = SessionLocal()
        try:
            rows = session.query(ConversationDB).all()
            conversations: List[Conversation] = []
            for row in rows:
                messages = (
                    session.query(ConversationMessageDB)
                    .filter(ConversationMessageDB.conversation_id == row.id)
                    .order_by(ConversationMessageDB.timestamp.asc())
                    .all()
                )
                conversations.append(self._to_model(row, messages))
            return conversations
        finally:
            session.close()

    def list_for_business(self, business_id: str) -> List[Conversation]:
        if SessionLocal is None:
            raise RuntimeError("Database session factory is not available")
        session = SessionLocal()
        try:
            rows = (
                session.query(ConversationDB)
                .filter(ConversationDB.business_id == business_id)
                .all()
            )
            conversations: List[Conversation] = []
            for row in rows:
                messages = (
                    session.query(ConversationMessageDB)
                    .filter(ConversationMessageDB.conversation_id == row.id)
                    .order_by(ConversationMessageDB.timestamp.asc())
                    .all()
                )
                conversations.append(self._to_model(row, messages))
            return conversations
        finally:
            session.close()

    def list_for_customer(self, customer_id: str) -> List[Conversation]:
        if SessionLocal is None:
            raise RuntimeError("Database session factory is not available")
        session = SessionLocal()
        try:
            rows = (
                session.query(ConversationDB)
                .filter(ConversationDB.customer_id == customer_id)
                .all()
            )
            conversations: List[Conversation] = []
            for row in rows:
                messages = (
                    session.query(ConversationMessageDB)
                    .filter(ConversationMessageDB.conversation_id == row.id)
                    .order_by(ConversationMessageDB.timestamp.asc())
                    .all()
                )
                conversations.append(self._to_model(row, messages))
            return conversations
        finally:
            session.close()

    def delete_for_customer(self, customer_id: str) -> None:
        if SessionLocal is None:
            raise RuntimeError("Database session factory is not available")
        session = SessionLocal()
        try:
            conv_rows = (
                session.query(ConversationDB)
                .filter(ConversationDB.customer_id == customer_id)
                .all()
            )
            for conv in conv_rows:
                session.query(ConversationMessageDB).filter(
                    ConversationMessageDB.conversation_id == conv.id
                ).delete()
                session.delete(conv)
            session.commit()
        finally:
            session.close()


USE_DB_CONVERSATIONS = os.getenv("USE_DB_CONVERSATIONS", "false").lower() == "true"

if USE_DB_CONVERSATIONS and SQLALCHEMY_AVAILABLE and SessionLocal is not None:
    conversations_repo = DbConversationRepository()
else:
    conversations_repo = InMemoryConversationRepository()
