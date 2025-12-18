"""
Seed demo/staging data for the in-memory repositories (and DB when enabled).

Usage examples (from repo root):
    cd backend
    # Seed default_business (what the owner dashboard uses by default):
    python seed_demo_data.py --reset

    # Seed a separate demo tenant:
    python seed_demo_data.py --reset --business-id demo_plumbing

    # Anonymized dataset (safe for screenshots/demos):
    python seed_demo_data.py --reset --anonymize

    # Skip if already populated for a tenant:
    python seed_demo_data.py --if-empty

Flags:
- --reset: clear existing in-memory data before seeding
- --anonymize: replace names/phones with generic values
- --business-id: seed for a specific business (default: default_business)
- --if-empty: skip when the business already has customers (DB mode)
- --dry-run: print the plan without writing
"""

from __future__ import annotations

import argparse
from datetime import date
from datetime import UTC, datetime, timedelta
import secrets

from app.db import SQLALCHEMY_AVAILABLE, SessionLocal
from app.db_models import (
    AppointmentDB,
    BusinessDB,
    ConversationDB,
    ConversationMessageDB,
    CustomerDB,
)
from app.metrics import BusinessSmsMetrics, CallbackItem, metrics
from app.repositories import appointments_repo, conversations_repo, customers_repo
from app.models import Appointment, Conversation, Customer


def _reset_in_memory(business_id: str | None = None) -> None:
    """Best-effort reset of in-memory repositories.

    When business_id is provided, remove only that tenant's data.
    """
    if hasattr(customers_repo, "_by_id") and hasattr(customers_repo, "_by_business"):
        if business_id:
            customer_ids = list(customers_repo._by_business.get(business_id, []))
            customers_repo._by_business.pop(business_id, None)
            for customer_id in customer_ids:
                customer = customers_repo._by_id.pop(customer_id, None)
                if not customer:
                    continue
                for phone, cid in list(customers_repo._by_phone.items()):
                    if cid == customer_id:
                        customers_repo._by_phone.pop(phone, None)
        else:
            customers_repo._by_id.clear()
            customers_repo._by_phone.clear()
            customers_repo._by_business.clear()

    if hasattr(appointments_repo, "_by_id") and hasattr(
        appointments_repo, "_by_business"
    ):
        if business_id:
            appt_ids = list(appointments_repo._by_business.get(business_id, []))
            appointments_repo._by_business.pop(business_id, None)
            for appt_id in appt_ids:
                appt = appointments_repo._by_id.pop(appt_id, None)
                if not appt:
                    continue
                customer_ids = list(appointments_repo._by_customer.keys())
                for customer_id in customer_ids:
                    ids = appointments_repo._by_customer.get(customer_id, [])
                    appointments_repo._by_customer[customer_id] = [
                        aid for aid in ids if aid != appt_id
                    ]
                    if not appointments_repo._by_customer[customer_id]:
                        appointments_repo._by_customer.pop(customer_id, None)
        else:
            appointments_repo._by_id.clear()
            appointments_repo._by_customer.clear()
            appointments_repo._by_business.clear()

    if hasattr(conversations_repo, "_by_id") and hasattr(
        conversations_repo, "_by_business"
    ):
        if business_id:
            conv_ids = list(conversations_repo._by_business.get(business_id, []))
            conversations_repo._by_business.pop(business_id, None)
            for conv_id in conv_ids:
                conv = conversations_repo._by_id.pop(conv_id, None)
                if not conv:
                    continue
                if getattr(conv, "session_id", None):
                    conversations_repo._by_session.pop(conv.session_id, None)
        else:
            conversations_repo._by_id.clear()
            conversations_repo._by_session.clear()
            conversations_repo._by_business.clear()


def _reset_metrics(business_id: str) -> None:
    metrics.sms_by_business.pop(business_id, None)
    metrics.callbacks_by_business.pop(business_id, None)
    metrics.twilio_by_business.pop(business_id, None)
    metrics.voice_sessions_by_business.pop(business_id, None)
    metrics.owner_notification_status_by_business.pop(business_id, None)
    metrics.owner_notification_events.pop(business_id, None)


def _reset_db(business_id: str) -> None:
    """Clear seeded rows for a business in the database if DB repos are active."""
    if not SQLALCHEMY_AVAILABLE or SessionLocal is None:
        return
    session = SessionLocal()
    try:
        conv_ids = [
            cid
            for (cid,) in session.query(ConversationDB.id).filter(
                ConversationDB.business_id == business_id
            )
        ]
        if conv_ids:
            session.query(ConversationMessageDB).filter(
                ConversationMessageDB.conversation_id.in_(conv_ids)
            ).delete(synchronize_session=False)
        session.query(ConversationDB).filter(
            ConversationDB.business_id == business_id
        ).delete(synchronize_session=False)
        session.query(AppointmentDB).filter(
            AppointmentDB.business_id == business_id
        ).delete(synchronize_session=False)
        session.query(CustomerDB).filter(CustomerDB.business_id == business_id).delete(
            synchronize_session=False
        )
        session.commit()
    finally:
        session.close()


def _ensure_business(business_id: str, now: datetime) -> dict[str, str]:
    """Create/update a Business row when seeding in DB mode.

    Returns (api_key, widget_token) for convenience.
    """
    if not SQLALCHEMY_AVAILABLE or SessionLocal is None:
        return {"api_key": "", "widget_token": ""}
    session = SessionLocal()
    try:
        row = session.get(BusinessDB, business_id)
        if row is None:
            row = BusinessDB(  # type: ignore[call-arg]
                id=business_id,
                name=business_id.replace("_", " ").title(),
                status="ACTIVE",
                api_key=secrets.token_hex(16),
                api_key_last_rotated_at=now,
                api_key_last_used_at=None,
                widget_token=secrets.token_hex(16),
                widget_token_last_rotated_at=now,
                widget_token_expires_at=None,
                owner_name="Demo Owner",
                owner_email="demo-owner@example.com",
                terms_accepted_at=now,
                privacy_accepted_at=now,
                service_tier="100",
                open_hour=8,
                close_hour=17,
                onboarding_step="complete",
                onboarding_completed=True,
                language_code="en",
                vertical="plumbing",
            )
            session.add(row)
        else:
            if not getattr(row, "status", None):
                row.status = "ACTIVE"
                session.add(row)
            if not getattr(row, "api_key", None):
                row.api_key = secrets.token_hex(16)
                row.api_key_last_rotated_at = now
                session.add(row)
            if not getattr(row, "widget_token", None):
                row.widget_token = secrets.token_hex(16)
                row.widget_token_last_rotated_at = now
                session.add(row)
        session.commit()
        session.refresh(row)
        return {
            "api_key": getattr(row, "api_key", "") or "",
            "widget_token": getattr(row, "widget_token", "") or "",
        }
    finally:
        session.close()


def _demo_customers(anonymize: bool) -> list[dict]:
    base = [
        {
            "name": "Ava Reed",
            "phone": "+1555010000",
            "email": "ava.reed@example.com",
            "address": "1200 Main St, Merriam, KS 66202",
        },
        {
            "name": "Miguel Santos",
            "phone": "+1555010001",
            "email": "miguel.santos@example.com",
            "address": "42 Elm Ave, Overland Park, KS 66204",
        },
        {
            "name": "Priya Patel",
            "phone": "+1555010002",
            "email": None,
            "address": "88 Oak Dr, Kansas City, MO 64112",
        },
        {
            "name": "Liam Chen",
            "phone": "+1555010003",
            "email": "liam.chen@example.com",
            "address": None,
        },
        {
            "name": "Sophia Nguyen",
            "phone": "+1555010004",
            "email": "sophia.nguyen@example.com",
            "address": "15 Maple St, Prairie Village, KS 66208",
        },
        {
            "name": "Jordan Smith",
            "phone": "+1555010005",
            "email": None,
            "address": "2100 W 47th St, Westwood, KS 66205",
        },
    ]
    if not anonymize:
        return base
    anonymized = []
    for idx, item in enumerate(base, start=1):
        anonymized.append(
            {
                "name": f"Customer-{idx:03d}",
                "phone": f"555-9{idx:03d}",
                "email": f"customer{idx:03d}@example.com",
                "address": item["address"],
            }
        )
    return anonymized


def _set_conversation_created_at(conversation_id: str, created_at: datetime) -> None:
    """Best-effort created_at override for DB-backed conversations."""
    if not SQLALCHEMY_AVAILABLE or SessionLocal is None:
        return
    session = SessionLocal()
    try:
        row = session.get(ConversationDB, conversation_id)
        if row is None:
            return
        row.created_at = created_at  # type: ignore[assignment]
        session.add(row)
        session.commit()
    finally:
        session.close()


def _seed_metrics(business_id: str, now: datetime) -> None:
    metrics.sms_by_business[business_id] = BusinessSmsMetrics(
        sms_sent_total=14,
        sms_sent_owner=6,
        sms_sent_customer=8,
        lead_followups_sent=2,
        retention_messages_sent=3,
        sms_confirmations_via_sms=1,
        sms_cancellations_via_sms=1,
        sms_reschedules_via_sms=0,
        sms_opt_out_events=1,
        sms_opt_in_events=2,
    )
    metrics.owner_notification_status_by_business[business_id] = {
        "status": "delivered",
        "channel": "sms",
        "timestamp": now.isoformat(),
    }
    queue = metrics.callbacks_by_business.setdefault(business_id, {})
    queue["+1555010999"] = CallbackItem(
        phone="+1555010999",
        first_seen=now - timedelta(hours=6),
        last_seen=now - timedelta(hours=2),
        count=2,
        channel="phone",
        lead_source="Google",
        status="PENDING",
        reason="MISSED_CALL",
        voicemail_url=None,
    )
    queue["+1555010998"] = CallbackItem(
        phone="+1555010998",
        first_seen=now - timedelta(days=1, hours=2),
        last_seen=now - timedelta(days=1),
        count=1,
        channel="phone",
        lead_source="Facebook",
        status="COMPLETED",
        reason="PARTIAL_INTAKE",
        voicemail_url=None,
        last_result="completed",
    )


def _utc_at(now: datetime, day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(
        day.year, day.month, day.day, hour, minute, tzinfo=now.tzinfo or UTC
    )


def seed_demo_data(
    business_id: str,
    anonymize: bool,
    dry_run: bool = False,
) -> dict:
    now = datetime.now(UTC)
    customers: list[Customer] = []
    appts: list[Appointment] = []
    convs: list[Conversation] = []
    conversations_in_memory = hasattr(conversations_repo, "_by_id")

    # Seed in-memory metrics for dashboard cards (safe for DB mode too; metrics are in-process).
    if not dry_run:
        _seed_metrics(business_id, now)

    # Customers
    for c in _demo_customers(anonymize):
        if dry_run:
            customers.append(
                Customer(
                    id="dry",
                    name=c["name"],
                    phone=c["phone"],
                    email=c.get("email"),
                    address=c["address"],
                    business_id=business_id,
                )
            )
            continue
        cust = customers_repo.upsert(
            name=c["name"],
            phone=c["phone"],
            email=c.get("email"),
            address=c["address"],
            business_id=business_id,
        )
        customers.append(cust)

    # Appointments (mix of past + future; include emergency, maintenance, quotes, etc).
    today = now.date()
    tomorrow = today + timedelta(days=1)
    next_week = today + timedelta(days=7)

    appointment_plan = [
        # Past (counts for service mix, pipeline, customer analytics)
        {
            "customer_idx": 0,
            "start": now - timedelta(hours=1),
            "minutes": 90,
            "service_type": "drain_or_sewer",
            "is_emergency": True,
            "lead_source": "Google",
            "estimated_value": 1200,
            "job_stage": "Booked",
            "status": "SCHEDULED",
        },
        {
            "customer_idx": 1,
            "start": now - timedelta(hours=20),
            "minutes": 60,
            "service_type": "fixture_or_leak_repair",
            "is_emergency": False,
            "lead_source": "Referral",
            "estimated_value": 250,
            "job_stage": "Booked",
            "status": "CONFIRMED",
        },
        {
            "customer_idx": 2,
            "start": now - timedelta(days=5),
            "minutes": 120,
            "service_type": "water_heater",
            "is_emergency": False,
            "lead_source": "Yelp",
            "estimated_value": 900,
            "job_stage": "Estimate / Quote",
            "status": "SCHEDULED",
            "quoted_value": 950,
            "quote_status": "QUOTED",
        },
        {
            "customer_idx": 3,
            "start": now - timedelta(days=18),
            "minutes": 75,
            "service_type": "maintenance",
            "is_emergency": False,
            "lead_source": "Google",
            "estimated_value": 180,
            "job_stage": "Completed",
            "status": "COMPLETED",
        },
        {
            "customer_idx": 4,
            "start": now - timedelta(days=12),
            "minutes": 45,
            "service_type": "inspection",
            "is_emergency": False,
            "lead_source": "Direct",
            "estimated_value": 125,
            "job_stage": "Lead",
            "status": "CANCELLED",
        },
        # Today + future (today summary, tomorrow schedule, 90d calendar tags)
        {
            "customer_idx": 5,
            "start": _utc_at(now, today, 14),
            "minutes": 60,
            "service_type": "fixture_or_leak_repair",
            "is_emergency": False,
            "lead_source": "Google",
            "estimated_value": 220,
            "job_stage": "Booked",
            "status": "SCHEDULED",
        },
        {
            "customer_idx": 0,
            "start": _utc_at(now, tomorrow, 10),
            "minutes": 90,
            "service_type": "water_heater",
            "is_emergency": False,
            "lead_source": "Website",
            "estimated_value": 800,
            "job_stage": "Booked",
            "status": "SCHEDULED",
        },
        {
            "customer_idx": 2,
            "start": _utc_at(now, next_week, 9),
            "minutes": 60,
            "service_type": "maintenance",
            "is_emergency": False,
            "lead_source": "Retention",
            "estimated_value": 160,
            "job_stage": "Scheduled",
            "status": "SCHEDULED",
        },
        {
            "customer_idx": 1,
            "start": _utc_at(now, next_week, 13),
            "minutes": 120,
            "service_type": "drain_or_sewer",
            "is_emergency": True,
            "lead_source": "Emergency",
            "estimated_value": 1500,
            "job_stage": "Booked",
            "status": "SCHEDULED",
        },
    ]

    for item in appointment_plan:
        cust = customers[int(item["customer_idx"]) % len(customers)]
        start = item["start"]
        end = start + timedelta(minutes=int(item["minutes"]))
        if dry_run:
            appts.append(
                Appointment(
                    id="dry",
                    customer_id=cust.id,
                    start_time=start,
                    end_time=end,
                    service_type=item["service_type"],
                    description="Demo seeded appointment",
                    is_emergency=bool(item["is_emergency"]),
                    lead_source=item.get("lead_source"),
                    estimated_value=item.get("estimated_value"),
                    job_stage=item.get("job_stage"),
                    business_id=business_id,
                    quoted_value=item.get("quoted_value"),
                    quote_status=item.get("quote_status"),
                )
            )
            continue
        appt = appointments_repo.create(
            customer_id=cust.id,
            start_time=start,
            end_time=end,
            service_type=item["service_type"],
            description="Demo seeded appointment",
            is_emergency=bool(item["is_emergency"]),
            lead_source=item.get("lead_source"),
            estimated_value=item.get("estimated_value"),
            job_stage=item.get("job_stage"),
            business_id=business_id,
            quoted_value=item.get("quoted_value"),
            quote_status=item.get("quote_status"),
        )
        status = item.get("status")
        if status and status != "SCHEDULED":
            appointments_repo.update(appt.id, status=status)
        appts.append(appt)

    # Conversations with sample messages (linked to customers so analytics endpoints have data).
    conversation_plan = [
        {
            "channel": "phone",
            "customer_idx": 0,
            "created_at": now - timedelta(hours=3),
            "flagged": True,
            "tags": ["qa"],
            "outcome": "Needs follow-up",
            "first_msg": "My drain is backing up and water is on the floor.",
        },
        {
            "channel": "web",
            "customer_idx": 1,
            "created_at": now - timedelta(days=2, hours=3),
            "flagged": False,
            "tags": [],
            "outcome": "Booked",
            "first_msg": "Can I book a leak repair for tomorrow?",
        },
        {
            "channel": "web",
            "customer_idx": 2,
            "created_at": now - timedelta(hours=5),
            "flagged": False,
            "tags": ["emergency"],
            "outcome": "Emergency intake",
            "first_msg": "Burst pipe, need help ASAP.",
        },
        {
            "channel": "phone",
            "customer_idx": 4,
            "created_at": now - timedelta(days=10),
            "flagged": False,
            "tags": [],
            "outcome": "Lead (no booking yet)",
            "first_msg": "Just checking pricing for a water heater install.",
        },
    ]
    for idx, item in enumerate(conversation_plan):
        if dry_run:
            convs.append(
                Conversation(
                    id=f"dry-{idx}",
                    channel=item["channel"],
                    customer_id=None,
                    session_id=None,
                    business_id=business_id,
                    messages=[],
                )
            )
            continue
        cust = customers[int(item["customer_idx"]) % len(customers)]
        conv = conversations_repo.create(
            channel=item["channel"],
            business_id=business_id,
            customer_id=cust.id,
        )
        created_at = item["created_at"]
        if conversations_in_memory:
            conv.created_at = created_at
        else:
            _set_conversation_created_at(conv.id, created_at)
        if hasattr(conv, "flagged_for_review"):
            conv.flagged_for_review = bool(item.get("flagged", False))
        if hasattr(conv, "tags"):
            conv.tags = list(item.get("tags", []) or [])
        if hasattr(conv, "outcome"):
            conv.outcome = item.get("outcome")
        conversations_repo.append_message(conv.id, role="user", text=item["first_msg"])
        conversations_repo.append_message(
            conv.id,
            role="assistant",
            text="Demo response: this data is seeded for local development.",
        )
        convs.append(conv)

    return {
        "customers": customers,
        "appointments": appts,
        "conversations": convs,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed demo/staging data.")
    parser.add_argument("--business-id", default="default_business")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Clear existing data for this business before seeding.",
    )
    parser.add_argument(
        "--if-empty",
        action="store_true",
        help="Skip seeding if the target business already has customers.",
    )
    parser.add_argument(
        "--anonymize", action="store_true", help="Use generic names/phones."
    )
    parser.add_argument("--dry-run", action="store_true", help="Plan only, no writes.")
    args = parser.parse_args()

    repo_is_in_memory = hasattr(customers_repo, "_by_id")
    use_db = SQLALCHEMY_AVAILABLE and SessionLocal is not None and not repo_is_in_memory
    if args.reset and not args.dry_run:
        if use_db:
            _reset_db(args.business_id)
        _reset_in_memory(args.business_id)
        _reset_metrics(args.business_id)

    if args.if_empty and not args.dry_run and use_db:
        session = SessionLocal()
        try:
            existing = (
                session.query(CustomerDB)
                .filter(CustomerDB.business_id == args.business_id)
                .count()
            )
            if existing > 0:
                print(
                    f"Business {args.business_id} already has {existing} customers; skipping seed (--if-empty)."
                )
                return
        finally:
            session.close()

    if use_db and not args.dry_run:
        _ensure_business(args.business_id, now=datetime.now(UTC))

    result = seed_demo_data(args.business_id, args.anonymize, dry_run=args.dry_run)
    print(
        f"Seeded (business={args.business_id}, anonymize={args.anonymize}, dry_run={args.dry_run}):"
    )
    print(f"- customers: {len(result['customers'])}")
    print(f"- appointments: {len(result['appointments'])}")
    print(f"- conversations: {len(result['conversations'])}")
    if use_db and not args.dry_run:
        print(
            "Tenant credentials were created/updated. For safety, tokens are not printed.\n"
            "Retrieve them via the admin API (`GET /v1/admin/businesses`) or the admin dashboard."
        )


if __name__ == "__main__":
    main()
