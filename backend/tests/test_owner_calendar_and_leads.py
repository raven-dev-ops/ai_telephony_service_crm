from datetime import UTC, date, datetime, timedelta

from fastapi.testclient import TestClient

from app.deps import DEFAULT_BUSINESS_ID
from app.main import app
from app.metrics import metrics
from app.repositories import appointments_repo, customers_repo, conversations_repo


client = TestClient(app)


def _reset_state() -> None:
    appointments_repo._by_id.clear()
    appointments_repo._by_customer.clear()
    appointments_repo._by_business.clear()
    customers_repo._by_id.clear()
    customers_repo._by_phone.clear()
    customers_repo._by_business.clear()
    conversations_repo._by_id.clear()
    conversations_repo._by_session.clear()
    conversations_repo._by_business.clear()
    metrics.sms_by_business.clear()
    metrics.callbacks_by_business.clear()
    metrics.retention_by_business.clear()


def _seed_calendar_data(now: datetime) -> tuple[date, date]:
    cust1 = customers_repo.upsert(
        name="Calendar One",
        phone="300",
        business_id=DEFAULT_BUSINESS_ID,
        tags=["gold"],
    )
    cust2 = customers_repo.upsert(
        name="Calendar Two",
        phone="301",
        business_id=DEFAULT_BUSINESS_ID,
        tags=["silver"],
    )

    # Conversation occurs before the appointment for conversion funnel coverage.
    conv1 = conversations_repo.create(
        channel="sms", customer_id=cust1.id, business_id=DEFAULT_BUSINESS_ID
    )
    conv1.created_at = now - timedelta(days=5)

    conv2 = conversations_repo.create(
        channel="web", customer_id=cust2.id, business_id=DEFAULT_BUSINESS_ID
    )
    conv2.created_at = now - timedelta(days=2)

    appt1_start = now - timedelta(days=3)
    appt1_end = appt1_start + timedelta(hours=2)
    appt1 = appointments_repo.create(
        customer_id=cust1.id,
        start_time=appt1_start,
        end_time=appt1_end,
        service_type="HVAC",
        is_emergency=False,
        description="Seasonal tune-up",
        lead_source="web",
        estimated_value=400,
        job_stage="Quoted",
        business_id=DEFAULT_BUSINESS_ID,
        tags=["maintenance"],
    )
    appt1.status = "SCHEDULED"

    appt2_start = now - timedelta(days=1)
    appt2_end = appt2_start + timedelta(hours=1)
    appt2 = appointments_repo.create(
        customer_id=cust2.id,
        start_time=appt2_start,
        end_time=appt2_end,
        service_type="Plumbing",
        is_emergency=True,
        description="Burst pipe",
        lead_source="referral",
        estimated_value=800,
        job_stage="Scheduled",
        business_id=DEFAULT_BUSINESS_ID,
        tags=["emergency", "pipe"],
    )
    appt2.status = "CONFIRMED"

    return appt1.start_time.date(), appt2.start_time.date()


def test_owner_calendar_views_and_reports() -> None:
    _reset_state()
    today = datetime.now(UTC)
    day1, _ = _seed_calendar_data(today)

    cal_resp = client.get("/v1/owner/calendar/90d")
    assert cal_resp.status_code == 200
    cal_body = cal_resp.json()
    assert cal_body["days"]

    pdf_resp = client.get(
        "/v1/owner/calendar/report.pdf", params={"day": day1.isoformat()}
    )
    assert pdf_resp.status_code == 200
    assert pdf_resp.content  # fallback PDF bytes or real PDF should be non-empty


def test_owner_leads_service_economics_and_conversion() -> None:
    _reset_state()
    now = datetime.now(UTC)
    _seed_calendar_data(now)

    lead_sources = client.get("/v1/owner/lead-sources", params={"days": 30}).json()
    sources = {item["lead_source"]: item for item in lead_sources["items"]}
    assert sources["web"]["appointments"] == 1
    assert sources["referral"]["appointments"] == 1

    economics = client.get("/v1/owner/service-economics", params={"days": 30}).json()
    items = {item["service_type"]: item for item in economics["items"]}
    assert items["HVAC"]["appointments"] == 1
    assert items["Plumbing"]["estimated_value_total"] >= 800.0

    metrics_resp = client.get("/v1/owner/service-metrics", params={"days": 90}).json()
    metric_items = {item["service_type"]: item for item in metrics_resp["items"]}
    assert metric_items["HVAC"]["scheduled_minutes_average"] > 0
    assert metric_items["Plumbing"]["appointments"] == 1

    time_to_book = client.get("/v1/owner/time-to-book", params={"days": 90}).json()
    assert time_to_book["overall_samples"] >= 1
    assert time_to_book["by_channel"]

    funnel = client.get("/v1/owner/conversion-funnel", params={"days": 90}).json()
    assert funnel["overall_leads"] >= 1
    assert funnel["channels"]
