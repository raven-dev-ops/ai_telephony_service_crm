from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from app.main import app
from app.repositories import appointments_repo, customers_repo, conversations_repo
from app.metrics import BusinessSmsMetrics, BusinessTwilioMetrics, metrics
from app.deps import DEFAULT_BUSINESS_ID
from app.services.email_service import EmailResult
from app.db import SessionLocal
from app.db_models import BusinessDB


client = TestClient(app)


def test_owner_schedule_tomorrow_no_appointments():
    # Ensure repos are empty for this test.
    appointments_repo._by_id.clear()
    appointments_repo._by_customer.clear()
    appointments_repo._by_business.clear()
    customers_repo._by_id.clear()
    customers_repo._by_phone.clear()
    customers_repo._by_business.clear()

    resp = client.get("/v1/owner/schedule/tomorrow")
    assert resp.status_code == 200
    body = resp.json()
    assert "tomorrow you have no appointments" in body["reply_text"].lower()
    assert body["appointments"] == []


def test_owner_schedule_tomorrow_with_appointments():
    appointments_repo._by_id.clear()
    appointments_repo._by_customer.clear()
    appointments_repo._by_business.clear()
    customers_repo._by_id.clear()
    customers_repo._by_phone.clear()
    customers_repo._by_business.clear()

    # Create a customer and appointment for tomorrow.
    cust_resp = client.post(
        "/v1/crm/customers",
        json={"name": "Owner Test", "phone": "555-5555"},
    )
    customer_id = cust_resp.json()["id"]
    # Choose a stable time tomorrow (10:00 UTC) so the test
    # does not depend on the current wall-clock hour.
    now = datetime.now(UTC)
    tomorrow = now.date() + timedelta(days=1)
    start = datetime(
        year=tomorrow.year,
        month=tomorrow.month,
        day=tomorrow.day,
        hour=10,
        minute=0,
        second=0,
        tzinfo=UTC,
    )
    end = start + timedelta(hours=1)
    client.post(
        "/v1/crm/appointments",
        json={
            "customer_id": customer_id,
            "start_time": start.isoformat(),
            "end_time": end.isoformat(),
            "service_type": "Inspection",
            "is_emergency": False,
            "description": "Routine inspection",
        },
    )

    resp = client.get("/v1/owner/schedule/tomorrow")
    assert resp.status_code == 200
    body = resp.json()
    assert "tomorrow you have 1 appointment" in body["reply_text"].lower()
    assert len(body["appointments"]) == 1
    assert body["appointments"][0]["customer_name"] == "Owner Test"


def test_owner_business_endpoint_returns_default_business():
    # This endpoint should work even when using the in-memory repositories.
    resp = client.get("/v1/owner/business")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == "default_business"
    assert isinstance(body["name"], str)
    assert body["name"]


def test_owner_today_summary_email_respects_owner_email(monkeypatch):
    # Ensure the default business has an owner email configured.
    if SessionLocal is not None:
        session = SessionLocal()
        try:
            row = session.get(BusinessDB, DEFAULT_BUSINESS_ID)
            if row is not None:
                row.owner_email = "owner@example.com"  # type: ignore[assignment]
                row.owner_email_alerts_enabled = True  # type: ignore[assignment]
                session.add(row)
                session.commit()
        finally:
            session.close()

    calls = {}

    async def fake_notify_owner(subject, body, business_id=None, owner_email=None):
        calls["subject"] = subject
        calls["body"] = body
        calls["business_id"] = business_id
        calls["owner_email"] = owner_email
        return EmailResult(sent=True, provider="stub")

    monkeypatch.setattr(
        "app.routers.owner.email_service.notify_owner", fake_notify_owner
    )

    resp = client.post("/v1/owner/summary/today/email")
    assert resp.status_code == 200
    data = resp.json()
    assert data["sent"] is True
    assert data["provider"] == "stub"
    assert calls.get("owner_email") == "owner@example.com"


def test_owner_schedule_audio_handles_synthesis_error(monkeypatch):
    appointments_repo._by_id.clear()
    appointments_repo._by_customer.clear()
    appointments_repo._by_business.clear()
    customers_repo._by_id.clear()
    customers_repo._by_phone.clear()
    customers_repo._by_business.clear()

    async def failing_synthesize(*args, **kwargs):
        raise RuntimeError("audio fail")

    monkeypatch.setattr(
        "app.routers.owner.speech_service.synthesize", failing_synthesize
    )

    # Use a client that does not raise on server exceptions so we can assert status.
    resilient_client = TestClient(app, raise_server_exceptions=False)
    resp = resilient_client.get("/v1/owner/schedule/tomorrow/audio")
    assert resp.status_code == 500


def test_owner_reschedules_endpoint_lists_pending_reschedules():
    appointments_repo._by_id.clear()
    appointments_repo._by_customer.clear()
    appointments_repo._by_business.clear()
    customers_repo._by_id.clear()
    customers_repo._by_phone.clear()
    customers_repo._by_business.clear()

    # Create a customer and one appointment marked as PENDING_RESCHEDULE.
    cust_resp = client.post(
        "/v1/crm/customers",
        json={"name": "Reschedule Owner", "phone": "555-9999"},
    )
    customer_id = cust_resp.json()["id"]

    now = datetime.now(UTC)
    start = now + timedelta(hours=5)
    end = start + timedelta(hours=1)
    appt_resp = client.post(
        "/v1/crm/appointments",
        json={
            "customer_id": customer_id,
            "start_time": start.isoformat(),
            "end_time": end.isoformat(),
            "service_type": "Inspection",
            "is_emergency": False,
            "description": "Pending reschedule",
        },
    )
    appt_id = appt_resp.json()["id"]
    appt_model = appointments_repo.get(appt_id)
    assert appt_model is not None
    appt_model.status = "PENDING_RESCHEDULE"

    resp = client.get("/v1/owner/reschedules")
    assert resp.status_code == 200
    body = resp.json()
    assert "1 appointment" in body["reply_text"].lower()
    assert len(body["reschedules"]) == 1
    assert body["reschedules"][0]["id"] == appt_id


def test_owner_sms_metrics_endpoint_returns_counts():
    # This endpoint should always return integer counts, even when no SMS has been sent.
    resp = client.get("/v1/owner/sms-metrics")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body["owner_messages"], int)
    assert isinstance(body["customer_messages"], int)
    assert isinstance(body["total_messages"], int)
    # Share fields should be present and either floats or null.
    assert "confirmation_share_via_sms" in body
    assert "cancellation_share_via_sms" in body


def test_owner_sms_metrics_share_fields_reflect_ratios():
    # Ensure clean repositories and SMS metrics for this tenant.
    appointments_repo._by_id.clear()
    appointments_repo._by_customer.clear()
    appointments_repo._by_business.clear()
    customers_repo._by_id.clear()
    customers_repo._by_phone.clear()
    customers_repo._by_business.clear()
    metrics.sms_by_business.clear()

    # Seed a customer and a mix of confirmed and cancelled appointments.
    cust_resp = client.post(
        "/v1/crm/customers",
        json={"name": "SMS Analytics", "phone": "555-4444"},
    )
    customer_id = cust_resp.json()["id"]

    now = datetime.now(UTC)

    # Two confirmed appointments.
    for _ in range(2):
        start = now
        end = start + timedelta(hours=1)
        appt_resp = client.post(
            "/v1/crm/appointments",
            json={
                "customer_id": customer_id,
                "start_time": start.isoformat(),
                "end_time": end.isoformat(),
                "service_type": "Inspection",
                "is_emergency": False,
                "description": "Confirmed via any channel",
            },
        )
        appt = appointments_repo.get(appt_resp.json()["id"])
        assert appt is not None
        appt.status = "CONFIRMED"

    # Two cancelled appointments.
    for _ in range(2):
        start = now
        end = start + timedelta(hours=1)
        appt_resp = client.post(
            "/v1/crm/appointments",
            json={
                "customer_id": customer_id,
                "start_time": start.isoformat(),
                "end_time": end.isoformat(),
                "service_type": "Inspection",
                "is_emergency": False,
                "description": "Cancelled via any channel",
            },
        )
        appt = appointments_repo.get(appt_resp.json()["id"])
        assert appt is not None
        appt.status = "CANCELLED"

    # Record SMS-driven confirmations and cancellations for the default tenant.
    metrics.sms_by_business[DEFAULT_BUSINESS_ID] = BusinessSmsMetrics(
        sms_sent_total=0,
        sms_sent_owner=0,
        sms_sent_customer=0,
        lead_followups_sent=0,
        retention_messages_sent=0,
        sms_confirmations_via_sms=1,
        sms_cancellations_via_sms=1,
        sms_reschedules_via_sms=0,
        sms_opt_out_events=0,
        sms_opt_in_events=0,
    )

    resp = client.get("/v1/owner/sms-metrics")
    assert resp.status_code == 200
    body = resp.json()

    # Raw counts should be reflected from metrics.
    assert body["confirmations_via_sms"] == 1
    assert body["cancellations_via_sms"] == 1

    # With two total confirmed and two total cancelled appointments, a single
    # SMS-driven event of each type should yield a 0.5 share.
    assert body["confirmation_share_via_sms"] == 0.5
    assert body["cancellation_share_via_sms"] == 0.5


def test_owner_service_mix_last_30_days():
    appointments_repo._by_id.clear()
    appointments_repo._by_customer.clear()
    appointments_repo._by_business.clear()
    customers_repo._by_id.clear()
    customers_repo._by_phone.clear()
    customers_repo._by_business.clear()

    # Create a customer and several appointments, some inside and some outside the 30-day window.
    cust_resp = client.post(
        "/v1/crm/customers",
        json={"name": "Service Mix Owner", "phone": "555-1212"},
    )
    customer_id = cust_resp.json()["id"]

    now = datetime.now(UTC)

    # Inside 30 days, standard job.
    start_recent = now - timedelta(days=5)
    end_recent = start_recent + timedelta(hours=1)
    client.post(
        "/v1/crm/appointments",
        json={
            "customer_id": customer_id,
            "start_time": start_recent.isoformat(),
            "end_time": end_recent.isoformat(),
            "service_type": "tankless_water_heater",
            "is_emergency": False,
            "description": "Recent tankless job",
        },
    )

    # Inside 30 days, emergency job.
    start_recent_emerg = now - timedelta(days=2)
    end_recent_emerg = start_recent_emerg + timedelta(hours=1)
    client.post(
        "/v1/crm/appointments",
        json={
            "customer_id": customer_id,
            "start_time": start_recent_emerg.isoformat(),
            "end_time": end_recent_emerg.isoformat(),
            "service_type": "drain_or_sewer",
            "is_emergency": True,
            "description": "Recent emergency drain job",
        },
    )

    # Outside 30 days, should be ignored.
    start_old = now - timedelta(days=40)
    end_old = start_old + timedelta(hours=1)
    client.post(
        "/v1/crm/appointments",
        json={
            "customer_id": customer_id,
            "start_time": start_old.isoformat(),
            "end_time": end_old.isoformat(),
            "service_type": "fixture_or_leak_repair",
            "is_emergency": True,
            "description": "Old emergency job",
        },
    )

    resp = client.get("/v1/owner/service-mix")
    assert resp.status_code == 200
    body = resp.json()

    # Only the two recent appointments should be counted.
    assert body["total_appointments_30d"] == 2
    assert body["emergency_appointments_30d"] == 1

    svc_counts = body["service_type_counts_30d"]
    emerg_counts = body["emergency_service_type_counts_30d"]
    assert svc_counts.get("tankless_water_heater", 0) == 1
    assert svc_counts.get("drain_or_sewer", 0) == 1
    assert emerg_counts.get("drain_or_sewer", 0) == 1


def test_owner_lead_sources_summarises_volume_and_value():
    appointments_repo._by_id.clear()
    appointments_repo._by_customer.clear()
    appointments_repo._by_business.clear()
    customers_repo._by_id.clear()
    customers_repo._by_phone.clear()
    customers_repo._by_business.clear()

    # Create a customer and several appointments with different lead sources.
    cust_resp = client.post(
        "/v1/crm/customers",
        json={"name": "Lead Source Owner", "phone": "555-1313"},
    )
    customer_id = cust_resp.json()["id"]

    now = datetime.now(UTC)

    # Inside 30 days, phone lead.
    start_phone = now - timedelta(days=3)
    end_phone = start_phone + timedelta(hours=1)
    client.post(
        "/v1/crm/appointments",
        json={
            "customer_id": customer_id,
            "start_time": start_phone.isoformat(),
            "end_time": end_phone.isoformat(),
            "service_type": "Inspection",
            "is_emergency": False,
            "description": "Recent phone lead",
            "lead_source": "phone",
            "estimated_value": 150.0,
        },
    )

    # Inside 30 days, web lead.
    start_web = now - timedelta(days=10)
    end_web = start_web + timedelta(hours=1)
    client.post(
        "/v1/crm/appointments",
        json={
            "customer_id": customer_id,
            "start_time": start_web.isoformat(),
            "end_time": end_web.isoformat(),
            "service_type": "Install",
            "is_emergency": False,
            "description": "Recent web lead",
            "lead_source": "web",
            "estimated_value": 250.0,
        },
    )

    # Outside 30 days, should be ignored.
    start_old = now - timedelta(days=45)
    end_old = start_old + timedelta(hours=1)
    client.post(
        "/v1/crm/appointments",
        json={
            "customer_id": customer_id,
            "start_time": start_old.isoformat(),
            "end_time": end_old.isoformat(),
            "service_type": "Inspection",
            "is_emergency": False,
            "description": "Old lead source",
            "lead_source": "referral",
            "estimated_value": 300.0,
        },
    )

    resp = client.get("/v1/owner/lead-sources", params={"days": 30})
    assert resp.status_code == 200
    body = resp.json()

    # Only the two recent appointments should be counted.
    assert body["total_appointments"] == 2
    assert body["total_estimated_value"] == 400.0

    items = body["items"]
    assert isinstance(items, list)
    sources = {item["lead_source"]: item for item in items}
    assert sources["phone"]["appointments"] == 1
    assert sources["phone"]["estimated_value_total"] == 150.0
    assert sources["web"]["appointments"] == 1
    assert sources["web"]["estimated_value_total"] == 250.0


def test_owner_customers_analytics_cohorts_and_economics():
    appointments_repo._by_id.clear()
    appointments_repo._by_customer.clear()
    appointments_repo._by_business.clear()
    customers_repo._by_id.clear()
    customers_repo._by_phone.clear()
    customers_repo._by_business.clear()

    # Create two customers; one repeat and one new.
    resp_repeat = client.post(
        "/v1/crm/customers",
        json={"name": "Repeat Customer", "phone": "555-1414"},
    )
    repeat_customer_id = resp_repeat.json()["id"]
    resp_new = client.post(
        "/v1/crm/customers",
        json={"name": "New Customer", "phone": "555-1515"},
    )
    new_customer_id = resp_new.json()["id"]

    now = datetime.now(UTC)

    # Repeat customer: two appointments in the window, one emergency.
    start1 = now - timedelta(days=30)
    end1 = start1 + timedelta(hours=1)
    client.post(
        "/v1/crm/appointments",
        json={
            "customer_id": repeat_customer_id,
            "start_time": start1.isoformat(),
            "end_time": end1.isoformat(),
            "service_type": "Inspection",
            "is_emergency": True,
            "description": "Emergency visit",
            "estimated_value": 200.0,
        },
    )

    start2 = now - timedelta(days=10)
    end2 = start2 + timedelta(hours=1)
    client.post(
        "/v1/crm/appointments",
        json={
            "customer_id": repeat_customer_id,
            "start_time": start2.isoformat(),
            "end_time": end2.isoformat(),
            "service_type": "Follow-up",
            "is_emergency": False,
            "description": "Standard visit",
            "estimated_value": 150.0,
        },
    )

    # New customer: one standard appointment.
    start3 = now - timedelta(days=5)
    end3 = start3 + timedelta(hours=1)
    client.post(
        "/v1/crm/appointments",
        json={
            "customer_id": new_customer_id,
            "start_time": start3.isoformat(),
            "end_time": end3.isoformat(),
            "service_type": "Inspection",
            "is_emergency": False,
            "description": "New customer visit",
            "estimated_value": 100.0,
        },
    )

    resp = client.get("/v1/owner/customers/analytics", params={"days": 365})
    assert resp.status_code == 200
    body = resp.json()

    assert body["window_days"] == 365
    assert body["total_customers"] == 2
    assert body["repeat_customers"] == 1

    # Cohort buckets should be present.
    cohorts = body["cohorts"]
    assert isinstance(cohorts, list)
    assert len(cohorts) >= 1

    econ = body["economics"]
    # Three total appointments with one emergency.
    assert econ["total_appointments"] == 3
    assert econ["emergency_appointments"] == 1
    assert econ["total_estimated_value"] == 450.0
    assert econ["emergency_estimated_value"] == 200.0

    # Average tickets should reflect the mix above.
    assert econ["average_ticket"] == 150.0
    assert econ["average_ticket_emergency"] == 200.0
    assert econ["average_ticket_standard"] == 125.0

    # Repeat vs new appointment share should be approximately 2/3 vs 1/3.
    repeat_share = econ["repeat_customer_share"]
    new_share = econ["new_customer_share"]
    assert round(repeat_share, 2) == 0.67
    assert round(new_share, 2) == 0.33


def test_owner_time_to_book_groups_by_channel():
    appointments_repo._by_id.clear()
    appointments_repo._by_customer.clear()
    appointments_repo._by_business.clear()
    customers_repo._by_id.clear()
    customers_repo._by_phone.clear()
    customers_repo._by_business.clear()
    if hasattr(conversations_repo, "_by_id"):
        conversations_repo._by_id.clear()
        conversations_repo._by_session.clear()
        conversations_repo._by_business.clear()

    # Create a customer and an initial conversation some days ago.
    cust_resp = client.post(
        "/v1/crm/customers",
        json={"name": "Time To Book", "phone": "555-1616"},
    )
    customer_id = cust_resp.json()["id"]

    now = datetime.now(UTC)
    first_contact = now - timedelta(days=10)

    conv = conversations_repo.create(
        channel="phone",
        customer_id=customer_id,
        business_id="default_business",
    )
    # For in-memory repositories, adjust created_at so the initial contact
    # is clearly before the appointment.
    if hasattr(conv, "created_at"):
        conv.created_at = first_contact

    # Appointment scheduled one day after first contact, still in the past.
    start = first_contact + timedelta(days=1)
    end = start + timedelta(hours=1)
    client.post(
        "/v1/crm/appointments",
        json={
            "customer_id": customer_id,
            "start_time": start.isoformat(),
            "end_time": end.isoformat(),
            "service_type": "Inspection",
            "is_emergency": False,
            "description": "Booked after initial contact",
        },
    )

    resp = client.get("/v1/owner/time-to-book", params={"days": 90})
    assert resp.status_code == 200
    body = resp.json()

    assert body["window_days"] == 90
    assert body["overall_samples"] == 1

    # Time between initial contact and first appointment is ~1 day.
    overall_avg = body["overall_average_minutes"]
    assert overall_avg > 0

    by_channel = body["by_channel"]
    assert isinstance(by_channel, list)
    assert len(by_channel) == 1
    bucket = by_channel[0]
    assert bucket["channel"] == "phone"
    assert bucket["samples"] == 1
    assert bucket["average_minutes"] == overall_avg


def test_owner_neighborhoods_summarises_volume_and_value_by_zip():
    appointments_repo._by_id.clear()
    appointments_repo._by_customer.clear()
    appointments_repo._by_business.clear()
    customers_repo._by_id.clear()
    customers_repo._by_phone.clear()
    customers_repo._by_business.clear()

    now = datetime.now(UTC)

    # First customer in ZIP 66210 with two appointments (one emergency).
    cust1_resp = client.post(
        "/v1/crm/customers",
        json={
            "name": "Neighborhood One",
            "phone": "555-1717",
            "address": "123 Main St, Overland Park, KS 66210",
        },
    )
    cust1_id = cust1_resp.json()["id"]

    start1 = now - timedelta(days=10)
    end1 = start1 + timedelta(hours=1)
    client.post(
        "/v1/crm/appointments",
        json={
            "customer_id": cust1_id,
            "start_time": start1.isoformat(),
            "end_time": end1.isoformat(),
            "service_type": "Inspection",
            "is_emergency": False,
            "description": "Standard job",
            "estimated_value": 200.0,
        },
    )

    start2 = now - timedelta(days=5)
    end2 = start2 + timedelta(hours=1)
    client.post(
        "/v1/crm/appointments",
        json={
            "customer_id": cust1_id,
            "start_time": start2.isoformat(),
            "end_time": end2.isoformat(),
            "service_type": "Emergency repair",
            "is_emergency": True,
            "description": "Emergency job",
            "estimated_value": 300.0,
        },
    )

    # Second customer in ZIP 64112 with one standard appointment.
    cust2_resp = client.post(
        "/v1/crm/customers",
        json={
            "name": "Neighborhood Two",
            "phone": "555-1818",
            "address": "456 Oak St, Kansas City, MO 64112",
        },
    )
    cust2_id = cust2_resp.json()["id"]

    start3 = now - timedelta(days=2)
    end3 = start3 + timedelta(hours=1)
    client.post(
        "/v1/crm/appointments",
        json={
            "customer_id": cust2_id,
            "start_time": start3.isoformat(),
            "end_time": end3.isoformat(),
            "service_type": "Inspection",
            "is_emergency": False,
            "description": "Standard job second neighborhood",
            "estimated_value": 150.0,
        },
    )

    # An old appointment outside the window should be ignored.
    start_old = now - timedelta(days=120)
    end_old = start_old + timedelta(hours=1)
    client.post(
        "/v1/crm/appointments",
        json={
            "customer_id": cust1_id,
            "start_time": start_old.isoformat(),
            "end_time": end_old.isoformat(),
            "service_type": "Inspection",
            "is_emergency": True,
            "description": "Old emergency job",
            "estimated_value": 999.0,
        },
    )

    resp = client.get("/v1/owner/neighborhoods", params={"days": 90})
    assert resp.status_code == 200
    body = resp.json()

    assert body["window_days"] == 90
    items = body["items"]
    assert isinstance(items, list)

    neighborhoods = {item["label"]: item for item in items}
    # ZIP 66210: one customer, two appointments, one emergency, total value 500.0.
    n1 = neighborhoods["66210"]
    assert n1["customers"] == 1
    assert n1["appointments"] == 2
    assert n1["emergency_appointments"] == 1
    assert n1["estimated_value_total"] == 500.0

    # ZIP 64112: one customer, one appointment, no emergencies, total value 150.0.
    n2 = neighborhoods["64112"]
    assert n2["customers"] == 1
    assert n2["appointments"] == 1
    assert n2["emergency_appointments"] == 0
    assert n2["estimated_value_total"] == 150.0


def test_owner_conversion_funnel_per_channel():
    appointments_repo._by_id.clear()
    appointments_repo._by_customer.clear()
    appointments_repo._by_business.clear()
    customers_repo._by_id.clear()
    customers_repo._by_phone.clear()
    customers_repo._by_business.clear()
    if hasattr(conversations_repo, "_by_id"):
        conversations_repo._by_id.clear()
        conversations_repo._by_session.clear()
        conversations_repo._by_business.clear()

    resp_phone = client.post(
        "/v1/crm/customers",
        json={"name": "Phone Lead", "phone": "555-1919"},
    )
    phone_customer_id = resp_phone.json()["id"]
    resp_web = client.post(
        "/v1/crm/customers",
        json={"name": "Web Lead", "phone": "555-2020"},
    )
    web_customer_id = resp_web.json()["id"]

    now = datetime.now(UTC)
    first_contact_phone = now - timedelta(days=5)
    first_contact_web = now - timedelta(days=3)

    conv_phone = conversations_repo.create(
        channel="phone",
        customer_id=phone_customer_id,
        business_id="default_business",
    )
    if hasattr(conv_phone, "created_at"):
        conv_phone.created_at = first_contact_phone

    conv_web = conversations_repo.create(
        channel="web",
        customer_id=web_customer_id,
        business_id="default_business",
    )
    if hasattr(conv_web, "created_at"):
        conv_web.created_at = first_contact_web

    start_phone = first_contact_phone + timedelta(days=1)
    end_phone = start_phone + timedelta(hours=1)
    client.post(
        "/v1/crm/appointments",
        json={
            "customer_id": phone_customer_id,
            "start_time": start_phone.isoformat(),
            "end_time": end_phone.isoformat(),
            "service_type": "Inspection",
            "is_emergency": False,
            "description": "Booked from phone lead",
        },
    )

    resp = client.get("/v1/owner/conversion-funnel", params={"days": 90})
    assert resp.status_code == 200
    body = resp.json()

    assert body["window_days"] == 90
    assert body["overall_leads"] == 2
    assert body["overall_booked"] == 1
    assert round(body["overall_conversion_rate"], 2) == 0.5

    channels = {c["channel"]: c for c in body["channels"]}
    phone_bucket = channels["phone"]
    web_bucket = channels["web"]

    assert phone_bucket["leads"] == 1
    assert phone_bucket["booked_appointments"] == 1
    assert round(phone_bucket["conversion_rate"], 2) == 1.0
    assert phone_bucket["average_time_to_book_minutes"] > 0

    assert web_bucket["leads"] == 1
    assert web_bucket["booked_appointments"] == 0
    assert web_bucket["conversion_rate"] == 0.0
    assert web_bucket["average_time_to_book_minutes"] == 0.0


def test_owner_data_completeness_counts_and_scores():
    appointments_repo._by_id.clear()
    appointments_repo._by_customer.clear()
    appointments_repo._by_business.clear()
    customers_repo._by_id.clear()
    customers_repo._by_phone.clear()
    customers_repo._by_business.clear()

    # One complete customer and one missing key fields.
    client.post(
        "/v1/crm/customers",
        json={
            "name": "Complete Customer",
            "phone": "555-2121",
            "email": "complete@example.com",
            "address": "123 Main St",
        },
    )
    client.post(
        "/v1/crm/customers",
        json={
            "name": "Incomplete Customer",
            "phone": "555-2222",
        },
    )

    now = datetime.now(UTC)

    # Two appointments in the window: one complete, one missing fields.
    start1 = now - timedelta(days=10)
    end1 = start1 + timedelta(hours=1)
    client.post(
        "/v1/crm/appointments",
        json={
            "customer_id": customers_repo.list_for_business("default_business")[0].id,
            "start_time": start1.isoformat(),
            "end_time": end1.isoformat(),
            "service_type": "Inspection",
            "is_emergency": False,
            "description": "Complete appointment",
            "estimated_value": 200.0,
            "lead_source": "web",
        },
    )

    start2 = now - timedelta(days=5)
    end2 = start2 + timedelta(hours=1)
    client.post(
        "/v1/crm/appointments",
        json={
            "customer_id": customers_repo.list_for_business("default_business")[1].id,
            "start_time": start2.isoformat(),
            "end_time": end2.isoformat(),
            "service_type": "Inspection",
            "is_emergency": False,
            "description": "Incomplete appointment (missing value and lead source)",
        },
    )

    # An old appointment outside the window should be ignored.
    start_old = now - timedelta(days=400)
    end_old = start_old + timedelta(hours=1)
    client.post(
        "/v1/crm/appointments",
        json={
            "customer_id": customers_repo.list_for_business("default_business")[0].id,
            "start_time": start_old.isoformat(),
            "end_time": end_old.isoformat(),
            "service_type": "Inspection",
            "is_emergency": False,
            "description": "Old appointment",
        },
    )

    resp = client.get("/v1/owner/data-completeness", params={"days": 365})
    assert resp.status_code == 200
    body = resp.json()

    assert body["window_days"] == 365
    assert body["total_customers"] == 2
    assert body["customers_with_email"] == 1
    assert body["customers_with_address"] == 1
    assert body["customers_complete"] == 1

    assert body["total_appointments"] == 2
    assert body["appointments_with_service_type"] == 2
    assert body["appointments_with_estimated_value"] == 1
    assert body["appointments_with_lead_source"] == 1
    assert body["appointments_complete"] == 1

    assert body["customer_completeness_score"] == 0.5
    assert body["appointment_completeness_score"] == 0.5


def test_owner_twilio_metrics_endpoint_returns_counts():
    # This endpoint should always return integer counts, even when no Twilio traffic has been recorded.
    resp = client.get("/v1/owner/twilio-metrics")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body["voice_requests"], int)


def test_owner_twilio_metrics_reflects_twilio_usage() -> None:
    # Reset global and per-tenant Twilio metrics.
    metrics.twilio_voice_requests = 0
    metrics.twilio_voice_errors = 0
    metrics.twilio_sms_requests = 0
    metrics.twilio_sms_errors = 0
    metrics.twilio_by_business.clear()

    # Seed some Twilio traffic for the default tenant via the real webhooks.
    resp_voice = client.post(
        "/twilio/voice",
        data={"CallSid": "CA_METRICS", "From": "+15550001234"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert resp_voice.status_code == 200

    resp_sms = client.post(
        "/twilio/sms",
        data={"From": "+15550001234", "Body": "Hello"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert resp_sms.status_code == 200

    # Owner Twilio metrics should reflect per-tenant counts.
    resp = client.get("/v1/owner/twilio-metrics")
    assert resp.status_code == 200
    body = resp.json()

    per = metrics.twilio_by_business.get(DEFAULT_BUSINESS_ID, BusinessTwilioMetrics())
    assert body["voice_requests"] == per.voice_requests
    assert body["voice_errors"] == per.voice_errors
    assert body["sms_requests"] == per.sms_requests
    assert body["sms_errors"] == per.sms_errors


def test_owner_data_completeness_handles_empty_tenant() -> None:
    # Ensure no customers or appointments are present for the default business.
    appointments_repo._by_id.clear()
    appointments_repo._by_customer.clear()
    appointments_repo._by_business.clear()
    customers_repo._by_id.clear()
    customers_repo._by_phone.clear()
    customers_repo._by_business.clear()

    resp = client.get("/v1/owner/data-completeness", params={"days": 30})
    assert resp.status_code == 200
    body = resp.json()

    assert body["window_days"] == 30
    assert body["total_customers"] == 0
    assert body["customers_with_email"] == 0
    assert body["customers_with_address"] == 0
    assert body["customers_complete"] == 0

    assert body["total_appointments"] == 0
    assert body["appointments_with_service_type"] == 0
    assert body["appointments_with_estimated_value"] == 0
    assert body["appointments_with_lead_source"] == 0
    assert body["appointments_complete"] == 0

    # When there is no data, completeness scores fall back to 0.0.
    assert body["customer_completeness_score"] == 0.0
    assert body["appointment_completeness_score"] == 0.0


def test_owner_workload_next_summarises_next_days():
    appointments_repo._by_id.clear()
    appointments_repo._by_customer.clear()
    appointments_repo._by_business.clear()
    customers_repo._by_id.clear()
    customers_repo._by_phone.clear()
    customers_repo._by_business.clear()

    # Create a customer.
    cust_resp = client.post(
        "/v1/crm/customers",
        json={"name": "Workload Owner", "phone": "555-2323"},
    )
    customer_id = cust_resp.json()["id"]

    now = datetime.now(UTC)
    today = now.date()
    tomorrow = today + timedelta(days=1)

    # Today: one standard appointment.
    start_today = datetime(
        year=today.year,
        month=today.month,
        day=today.day,
        hour=9,
        minute=0,
        tzinfo=UTC,
    )
    end_today = start_today + timedelta(hours=1)
    client.post(
        "/v1/crm/appointments",
        json={
            "customer_id": customer_id,
            "start_time": start_today.isoformat(),
            "end_time": end_today.isoformat(),
            "service_type": "Inspection",
            "is_emergency": False,
            "description": "Today standard job",
        },
    )

    # Tomorrow: one emergency appointment.
    start_tomorrow = datetime(
        year=tomorrow.year,
        month=tomorrow.month,
        day=tomorrow.day,
        hour=10,
        minute=0,
        tzinfo=UTC,
    )
    end_tomorrow = start_tomorrow + timedelta(hours=1)
    client.post(
        "/v1/crm/appointments",
        json={
            "customer_id": customer_id,
            "start_time": start_tomorrow.isoformat(),
            "end_time": end_tomorrow.isoformat(),
            "service_type": "Emergency",
            "is_emergency": True,
            "description": "Tomorrow emergency job",
        },
    )

    resp = client.get("/v1/owner/workload/next", params={"days": 2})
    assert resp.status_code == 200
    body = resp.json()
    assert body["days"] == 2
    items = body["items"]
    assert isinstance(items, list) and len(items) == 2

    # Today bucket.
    first = items[0]
    assert first["total_appointments"] == 1
    assert first["emergency_appointments"] == 0
    assert first["standard_appointments"] == 1

    # Tomorrow bucket.
    second = items[1]
    assert second["total_appointments"] == 1
    assert second["emergency_appointments"] == 1
    assert second["standard_appointments"] == 0


def test_owner_workload_next_includes_days_with_no_appointments() -> None:
    appointments_repo._by_id.clear()
    appointments_repo._by_customer.clear()
    appointments_repo._by_business.clear()
    customers_repo._by_id.clear()
    customers_repo._by_phone.clear()
    customers_repo._by_business.clear()

    # Create a customer.
    cust_resp = client.post(
        "/v1/crm/customers",
        json={"name": "Sparse Workload Owner", "phone": "555-2424"},
    )
    customer_id = cust_resp.json()["id"]

    now = datetime.now(UTC)
    today = now.date()
    day_two = today + timedelta(days=1)

    # Only create an appointment on day two of a three-day window.
    start_day_two = datetime(
        year=day_two.year,
        month=day_two.month,
        day=day_two.day,
        hour=11,
        minute=0,
        tzinfo=UTC,
    )
    end_day_two = start_day_two + timedelta(hours=1)
    client.post(
        "/v1/crm/appointments",
        json={
            "customer_id": customer_id,
            "start_time": start_day_two.isoformat(),
            "end_time": end_day_two.isoformat(),
            "service_type": "Inspection",
            "is_emergency": False,
            "description": "Middle-day job",
        },
    )

    resp = client.get("/v1/owner/workload/next", params={"days": 3})
    assert resp.status_code == 200
    body = resp.json()
    assert body["days"] == 3
    items = body["items"]
    assert len(items) == 3

    # Day 1: no appointments.
    assert items[0]["total_appointments"] == 0
    assert items[0]["emergency_appointments"] == 0
    assert items[0]["standard_appointments"] == 0

    # Day 2: one standard appointment.
    assert items[1]["total_appointments"] == 1
    assert items[1]["emergency_appointments"] == 0
    assert items[1]["standard_appointments"] == 1

    # Day 3: still no appointments.
    assert items[2]["total_appointments"] == 0
    assert items[2]["emergency_appointments"] == 0
    assert items[2]["standard_appointments"] == 0


def test_owner_schedule_tomorrow_audio():
    appointments_repo._by_id.clear()
    appointments_repo._by_customer.clear()
    appointments_repo._by_business.clear()
    customers_repo._by_id.clear()
    customers_repo._by_phone.clear()
    customers_repo._by_business.clear()

    resp = client.get("/v1/owner/schedule/tomorrow/audio")
    assert resp.status_code == 200
    body = resp.json()
    # In stub mode we still expect an audio token.
    assert "tomorrow you have no appointments" in body["reply_text"].lower()
    assert isinstance(body["audio"], str)


def test_owner_today_summary_no_appointments():
    appointments_repo._by_id.clear()
    appointments_repo._by_customer.clear()
    appointments_repo._by_business.clear()
    customers_repo._by_id.clear()
    customers_repo._by_phone.clear()
    customers_repo._by_business.clear()

    resp = client.get("/v1/owner/summary/today")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_appointments"] == 0
    assert body["emergency_appointments"] == 0
    assert body["standard_appointments"] == 0
    assert "today you have no appointments" in body["reply_text"].lower()


def test_owner_today_summary_audio_no_appointments():
    appointments_repo._by_id.clear()
    appointments_repo._by_customer.clear()
    appointments_repo._by_business.clear()
    customers_repo._by_id.clear()
    customers_repo._by_phone.clear()
    customers_repo._by_business.clear()

    resp = client.get("/v1/owner/summary/today/audio")
    assert resp.status_code == 200
    body = resp.json()
    assert "today you have no appointments" in body["reply_text"].lower()
    assert isinstance(body["audio"], str)


def test_owner_today_summary_counts():
    appointments_repo._by_id.clear()
    appointments_repo._by_customer.clear()
    appointments_repo._by_business.clear()
    customers_repo._by_id.clear()
    customers_repo._by_phone.clear()
    customers_repo._by_business.clear()

    # Create a customer and two appointments for today.
    cust_resp = client.post(
        "/v1/crm/customers",
        json={"name": "Hybrid Owner", "phone": "555-7777"},
    )
    customer_id = cust_resp.json()["id"]

    now = datetime.now(UTC)
    today = now.date()
    start1 = datetime(
        year=today.year,
        month=today.month,
        day=today.day,
        hour=9,
        minute=0,
        second=0,
        tzinfo=UTC,
    )
    end1 = start1 + timedelta(hours=1)

    start2 = start1 + timedelta(hours=2)
    end2 = start2 + timedelta(hours=1)

    # Standard job.
    client.post(
        "/v1/crm/appointments",
        json={
            "customer_id": customer_id,
            "start_time": start1.isoformat(),
            "end_time": end1.isoformat(),
            "service_type": "Inspection",
            "is_emergency": False,
            "description": "Routine inspection",
        },
    )

    # Emergency job.
    client.post(
        "/v1/crm/appointments",
        json={
            "customer_id": customer_id,
            "start_time": start2.isoformat(),
            "end_time": end2.isoformat(),
            "service_type": "Emergency repair",
            "is_emergency": True,
            "description": "Burst pipe",
        },
    )

    resp = client.get("/v1/owner/summary/today")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_appointments"] == 2
    assert body["emergency_appointments"] == 1
    assert body["standard_appointments"] == 1
    assert "today you have 2 appointments" in body["reply_text"].lower()


def test_owner_views_ignore_cancelled_appointments():
    appointments_repo._by_id.clear()
    appointments_repo._by_customer.clear()
    appointments_repo._by_business.clear()
    customers_repo._by_id.clear()
    customers_repo._by_phone.clear()
    customers_repo._by_business.clear()

    # Create a customer and a cancelled appointment for tomorrow and today.
    cust_resp = client.post(
        "/v1/crm/customers",
        json={"name": "Cancelled Owner", "phone": "555-8888"},
    )
    customer_id = cust_resp.json()["id"]

    now = datetime.now(UTC)
    today = now.date()
    # Today's cancelled appointment.
    start_today = datetime(
        year=today.year,
        month=today.month,
        day=today.day,
        hour=8,
        minute=0,
        second=0,
        tzinfo=UTC,
    )
    end_today = start_today + timedelta(hours=1)
    resp_appt_today = client.post(
        "/v1/crm/appointments",
        json={
            "customer_id": customer_id,
            "start_time": start_today.isoformat(),
            "end_time": end_today.isoformat(),
            "service_type": "Inspection",
            "is_emergency": False,
            "description": "Cancelled today",
        },
    )
    appt_today = resp_appt_today.json()

    # Tomorrow's cancelled appointment.
    tomorrow = today + timedelta(days=1)
    start_tomorrow = datetime(
        year=tomorrow.year,
        month=tomorrow.month,
        day=tomorrow.day,
        hour=10,
        minute=0,
        second=0,
        tzinfo=UTC,
    )
    end_tomorrow = start_tomorrow + timedelta(hours=1)
    resp_appt_tomorrow = client.post(
        "/v1/crm/appointments",
        json={
            "customer_id": customer_id,
            "start_time": start_tomorrow.isoformat(),
            "end_time": end_tomorrow.isoformat(),
            "service_type": "Inspection",
            "is_emergency": False,
            "description": "Cancelled tomorrow",
        },
    )
    appt_tomorrow = resp_appt_tomorrow.json()

    # Mark both as cancelled directly via repository.
    appt_model_today = appointments_repo.get(appt_today["id"])
    appt_model_tomorrow = appointments_repo.get(appt_tomorrow["id"])
    assert appt_model_today is not None
    assert appt_model_tomorrow is not None
    appt_model_today.status = "CANCELLED"
    appt_model_tomorrow.status = "CANCELLED"

    # Owner views should treat this as "no appointments".
    today_resp = client.get("/v1/owner/summary/today")
    assert today_resp.status_code == 200
    body_today = today_resp.json()
    assert body_today["total_appointments"] == 0
    assert "today you have no appointments" in body_today["reply_text"].lower()

    tomorrow_resp = client.get("/v1/owner/schedule/tomorrow")
    assert tomorrow_resp.status_code == 200
    body_tomorrow = tomorrow_resp.json()
    assert body_tomorrow["appointments"] == []
    assert "tomorrow you have no appointments" in body_tomorrow["reply_text"].lower()
