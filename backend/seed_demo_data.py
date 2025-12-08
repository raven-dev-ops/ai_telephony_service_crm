"""
Seed demo/staging data for the in-memory repositories (and DB when enabled).

Usage examples (from repo root):
    cd backend
    python seed_demo_data.py --reset
    python seed_demo_data.py --reset --anonymize
    # Skip if already populated for a tenant:
    python seed_demo_data.py --if-empty

Flags:
- --reset: clear existing in-memory data before seeding
- --anonymize: replace names/phones with generic values
- --business-id: seed for a specific business (default: demo_plumbing)
- --if-empty: skip when the business already has customers (DB mode)
- --dry-run: print the plan without writing
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta

from app.db import SQLALCHEMY_AVAILABLE, SessionLocal
from app.db_models import (
    AppointmentDB,
    BusinessDB,
    ConversationDB,
    ConversationMessageDB,
    CustomerDB,
)
from app.repositories import appointments_repo, conversations_repo, customers_repo
from app.models import Appointment, Conversation, Customer


def _reset_in_memory() -> None:
    """Best-effort reset of in-memory repositories."""
    if hasattr(customers_repo, "_by_id"):
        customers_repo._by_id.clear()
        customers_repo._by_phone.clear()
        customers_repo._by_business.clear()
    if hasattr(appointments_repo, "_by_id"):
        appointments_repo._by_id.clear()
        appointments_repo._by_customer.clear()
        appointments_repo._by_business.clear()
    if hasattr(conversations_repo, "_by_id"):
        conversations_repo._by_id.clear()
        conversations_repo._by_session.clear()
        conversations_repo._by_business.clear()


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


def _ensure_business(business_id: str) -> None:
    """Create a bare-bones Business row when seeding in DB mode."""
    if not SQLALCHEMY_AVAILABLE or SessionLocal is None:
        return
    session = SessionLocal()
    try:
        if session.get(BusinessDB, business_id) is None:
            session.add(
                BusinessDB(  # type: ignore[call-arg]
                    id=business_id,
                    name=business_id.replace("_", " ").title(),
                    status="ACTIVE",
                )
            )
            session.commit()
    finally:
        session.close()


def _demo_customers(anonymize: bool) -> list[dict]:
    base = [
        {"name": "Ava Reed", "phone": "555-0100", "address": "1200 Main St"},
        {"name": "Miguel Santos", "phone": "555-0101", "address": "42 Elm Ave"},
        {"name": "Priya Patel", "phone": "555-0102", "address": "88 Oak Dr"},
        {"name": "Liam Chen", "phone": "555-0103", "address": "7 River Rd"},
    ]
    if not anonymize:
        return base
    anonymized = []
    for idx, item in enumerate(base, start=1):
        anonymized.append(
            {
                "name": f"Customer-{idx:03d}",
                "phone": f"555-9{idx:03d}",
                "address": item["address"],
            }
        )
    return anonymized


def seed_demo_data(
    business_id: str,
    anonymize: bool,
    dry_run: bool = False,
) -> dict:
    now = datetime.now(UTC)
    customers: list[Customer] = []
    appts: list[Appointment] = []
    convs: list[Conversation] = []

    # Customers
    for c in _demo_customers(anonymize):
        if dry_run:
            customers.append(
                Customer(
                    id="dry",
                    name=c["name"],
                    phone=c["phone"],
                    address=c["address"],
                    business_id=business_id,
                )
            )
            continue
        cust = customers_repo.upsert(
            name=c["name"],
            phone=c["phone"],
            address=c["address"],
            business_id=business_id,
        )
        customers.append(cust)

    # Appointments (mix of emergency/standard)
    windows = [
        (1, False, "water_heater"),
        (2, True, "drain_or_sewer"),
        (3, False, "fixture_or_leak_repair"),
        (5, False, "maintenance"),
    ]
    for idx, (days_out, is_emergency, svc) in enumerate(windows):
        start = now + timedelta(days=days_out, hours=9 + idx)
        end = start + timedelta(hours=2)
        cust = customers[idx % len(customers)]
        if dry_run:
            appts.append(
                Appointment(
                    id="dry",
                    customer_id=cust.id,
                    start_time=start,
                    end_time=end,
                    service_type=svc,
                    description="Demo seeded appointment",
                    is_emergency=is_emergency,
                    lead_source="Demo",
                    business_id=business_id,
                )
            )
            continue
        appts.append(
            appointments_repo.create(
                customer_id=cust.id,
                start_time=start,
                end_time=end,
                service_type=svc,
                description="Demo seeded appointment",
                is_emergency=is_emergency,
                lead_source="Demo",
                business_id=business_id,
            )
        )

    # Conversations with sample messages
    sample_topics = [
        ("owner", "How many emergencies are scheduled this week?"),
        ("owner", "Summarize bookings vs last week."),
        ("owner", "List top repeat customers in 90 days."),
    ]
    for topic_idx, (channel, first_msg) in enumerate(sample_topics):
        if dry_run:
            convs.append(
                Conversation(
                    id=f"dry-{topic_idx}",
                    channel=channel,
                    customer_id=None,
                    session_id=None,
                    business_id=business_id,
                    messages=[],
                )
            )
            continue
        conv = conversations_repo.create(
            channel=channel, business_id=business_id, customer_id=None
        )
        conversations_repo.append_message(conv.id, role="user", text=first_msg)
        conversations_repo.append_message(
            conv.id,
            role="assistant",
            text="Demo response: this data is seeded for staging.",
        )
        convs.append(conv)

    return {
        "customers": customers,
        "appointments": appts,
        "conversations": convs,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed demo/staging data.")
    parser.add_argument("--business-id", default="demo_plumbing")
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
        _reset_in_memory()

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
        _ensure_business(args.business_id)

    result = seed_demo_data(args.business_id, args.anonymize, dry_run=args.dry_run)
    print(
        f"Seeded (business={args.business_id}, anonymize={args.anonymize}, dry_run={args.dry_run}):"
    )
    print(f"- customers: {len(result['customers'])}")
    print(f"- appointments: {len(result['appointments'])}")
    print(f"- conversations: {len(result['conversations'])}")


if __name__ == "__main__":
    main()
