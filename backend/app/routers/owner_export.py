from __future__ import annotations

import csv
import io
from dataclasses import asdict
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query, Response

from ..db import SQLALCHEMY_AVAILABLE, SessionLocal
from ..db_models import BusinessDB
from ..deps import ensure_business_active, require_dashboard_role
from ..repositories import appointments_repo, conversations_repo, customers_repo


router = APIRouter(
    dependencies=[
        Depends(require_dashboard_role(["admin", "owner", "staff", "viewer"]))
    ]
)


@router.get("/service-mix.csv", response_class=Response)
def export_service_mix_csv(
    business_id: str = Depends(ensure_business_active),
    days: int = Query(30, ge=1, le=90),
) -> Response:
    """Export recent service mix for the current tenant as CSV.

    This is an owner-scoped view so individual tenants can download their own
    service mix without needing admin access. The ``days`` query parameter
    controls the time window (default 30 days, max 90).
    """
    now = datetime.now(UTC)
    window = now - timedelta(days=days)

    rows = []
    for appt in appointments_repo.list_for_business(business_id):
        start_time = getattr(appt, "start_time", None)
        if not start_time:
            continue
        if start_time < window or start_time > now:
            continue
        status = getattr(appt, "status", "SCHEDULED").upper()
        if status not in {"SCHEDULED", "CONFIRMED"}:
            continue
        service_type = getattr(appt, "service_type", None) or "unspecified"
        is_emergency = bool(getattr(appt, "is_emergency", False))
        rows.append(
            {
                "service_type": service_type,
                "start_time": start_time.isoformat(),
                "is_emergency": "true" if is_emergency else "false",
            }
        )

    output = io.StringIO()
    writer = csv.DictWriter(
        output, fieldnames=["service_type", "start_time", "is_emergency"]
    )
    writer.writeheader()
    for row in rows:
        writer.writerow(row)

    csv_bytes = output.getvalue().encode("utf-8")
    return Response(
        content=csv_bytes,
        media_type="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename=service_mix_{days}d.csv"
        },
    )


@router.get("/full.json")
def export_tenant_full_json(
    business_id: str = Depends(ensure_business_active),
) -> dict:
    """Export a complete JSON snapshot for the current tenant.

    This is intended for data-portability and governance use cases. It
    includes basic business metadata plus customers, appointments, and
    conversations (with messages) for the tenant.
    """
    business_meta: dict[str, object] = {"id": business_id}
    if SQLALCHEMY_AVAILABLE and SessionLocal is not None:
        session = SessionLocal()
        try:
            row = session.get(BusinessDB, business_id)
        finally:
            session.close()
        if row is not None:
            business_meta.update(
                {
                    "name": getattr(row, "name", None),
                    "status": getattr(row, "status", None),
                    "vertical": getattr(row, "vertical", None),
                    "language_code": getattr(row, "language_code", None),
                    "created_at": getattr(row, "created_at", None),
                }
            )

    customers = [asdict(c) for c in customers_repo.list_for_business(business_id)]
    appointments = [asdict(a) for a in appointments_repo.list_for_business(business_id)]
    conversations = [
        asdict(conv) for conv in conversations_repo.list_for_business(business_id)
    ]

    return {
        "business": business_meta,
        "generated_at": datetime.now(UTC),
        "customers": customers,
        "appointments": appointments,
        "conversations": conversations,
    }


@router.get("/conversations.csv", response_class=Response)
def export_conversations_csv(
    business_id: str = Depends(ensure_business_active),
    days: int = Query(30, ge=1, le=90),
) -> Response:
    """Export recent conversations for the current tenant as CSV.

    This is intended for QA and after-action review, so it includes basic
    metadata and inferred service type/booking status. The ``days`` query
    parameter controls the time window (default 30 days, max 90).
    """
    now = datetime.now(UTC)
    window = now - timedelta(days=days)

    rows = []
    for conv in conversations_repo.list_for_business(business_id):
        created_at = getattr(conv, "created_at", None)
        if not created_at:
            continue
        # Normalise to aware UTC if needed for safe comparison.
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        if created_at < window or created_at > now:
            continue

        customer_id = getattr(conv, "customer_id", None)
        service_type: str | None = None
        has_appointments = False
        if customer_id:
            appts = appointments_repo.list_for_customer(customer_id)
            appts = [
                a
                for a in appts
                if getattr(a, "business_id", business_id) == business_id
            ]
            appts.sort(key=lambda a: a.start_time, reverse=True)
            if appts:
                service_type = appts[0].service_type
                has_appointments = any(
                    getattr(a, "status", "SCHEDULED").upper()
                    in {"SCHEDULED", "CONFIRMED"}
                    for a in appts
                )

        flagged = bool(getattr(conv, "flagged_for_review", False))
        tags = getattr(conv, "tags", []) or []
        outcome = getattr(conv, "outcome", "") or ""
        notes = getattr(conv, "notes", "") or ""

        rows.append(
            {
                "id": conv.id,
                "channel": conv.channel,
                "created_at": created_at.isoformat(),
                "customer_id": customer_id or "",
                "service_type": service_type or "",
                "has_appointments": "true" if has_appointments else "false",
                "flagged_for_review": "true" if flagged else "false",
                "tags": ";".join(tags),
                "outcome": outcome,
                "notes": notes,
            }
        )

    output = io.StringIO()
    fieldnames = [
        "id",
        "channel",
        "created_at",
        "customer_id",
        "service_type",
        "has_appointments",
        "flagged_for_review",
        "tags",
        "outcome",
        "notes",
    ]
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    for row in rows:
        writer.writerow(row)

    csv_bytes = output.getvalue().encode("utf-8")
    return Response(
        content=csv_bytes,
        media_type="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename=conversations_{days}d.csv"
        },
    )


@router.get("/pipeline.csv", response_class=Response)
def export_pipeline_csv(
    business_id: str = Depends(ensure_business_active),
    days: int = Query(30, ge=1, le=180),
) -> Response:
    """Export simple pipeline data (by appointment) as CSV.

    Includes job_stage, lead_source (which may contain campaign tags),
    estimated and quoted values so owners can analyze marketing performance.
    """
    now = datetime.now(UTC)
    window = now - timedelta(days=days)

    rows = []
    for appt in appointments_repo.list_for_business(business_id):
        start_time = getattr(appt, "start_time", None)
        if not start_time:
            continue
        if start_time < window or start_time > now:
            continue
        stage = (
            getattr(appt, "job_stage", None) or "Unspecified"
        ).strip() or "Unspecified"
        lead_source = (getattr(appt, "lead_source", None) or "").strip()
        est_value = getattr(appt, "estimated_value", None)
        quoted_value = getattr(appt, "quoted_value", None)
        quote_status = getattr(appt, "quote_status", None) or ""
        service_type = getattr(appt, "service_type", None) or ""
        is_emergency = "true" if bool(getattr(appt, "is_emergency", False)) else "false"
        rows.append(
            {
                "start_time": start_time.isoformat(),
                "job_stage": stage,
                "lead_source": lead_source,
                "estimated_value": est_value if est_value is not None else "",
                "quoted_value": quoted_value if quoted_value is not None else "",
                "quote_status": quote_status,
                "service_type": service_type,
                "is_emergency": is_emergency,
            }
        )

    output = io.StringIO()
    fieldnames = [
        "start_time",
        "job_stage",
        "lead_source",
        "estimated_value",
        "quoted_value",
        "quote_status",
        "service_type",
        "is_emergency",
    ]
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    for row in rows:
        writer.writerow(row)

    csv_bytes = output.getvalue().encode("utf-8")
    return Response(
        content=csv_bytes,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=pipeline_{days}d.csv"},
    )


@router.get("/conversion-funnel.csv", response_class=Response)
def export_conversion_funnel_csv(
    business_id: str = Depends(ensure_business_active),
    days: int = Query(90, ge=7, le=365),
) -> Response:
    """Export per-channel conversion funnel data as CSV.

    The CSV includes, per initial-contact channel, the number of leads,
    booked appointments, conversion rate, and average time-to-book (in
    minutes) based on conversations and appointments in the window.
    """
    now = datetime.now(UTC)
    window_start = now - timedelta(days=days)

    # Earliest conversation per customer in the window.
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

    per_channel_leads: dict[str, int] = {}
    per_channel_booked: dict[str, int] = {}
    per_channel_minutes: dict[str, float] = {}

    for customer_id, (first_ts, channel) in first_contact.items():
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

        per_channel_booked[channel] = per_channel_booked.get(channel, 0) + 1
        per_channel_minutes[channel] = per_channel_minutes.get(channel, 0.0) + minutes

    output = io.StringIO()
    fieldnames = [
        "channel",
        "leads",
        "booked_appointments",
        "conversion_rate",
        "average_time_to_book_minutes",
    ]
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()

    for channel in sorted(per_channel_leads.keys()):
        leads = per_channel_leads.get(channel, 0)
        booked = per_channel_booked.get(channel, 0)
        conversion_rate = float(booked) / float(leads) if leads > 0 else 0.0
        avg_minutes = (
            per_channel_minutes.get(channel, 0.0) / float(booked) if booked > 0 else 0.0
        )
        writer.writerow(
            {
                "channel": channel,
                "leads": leads,
                "booked_appointments": booked,
                "conversion_rate": conversion_rate,
                "average_time_to_book_minutes": avg_minutes,
            }
        )

    csv_bytes = output.getvalue().encode("utf-8")
    return Response(
        content=csv_bytes,
        media_type="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename=conversion_funnel_{days}d.csv"
        },
    )
