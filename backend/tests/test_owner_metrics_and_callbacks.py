from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from app.deps import DEFAULT_BUSINESS_ID
from app.main import app
from app.metrics import CallbackItem, BusinessSmsMetrics, metrics
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


def test_owner_callbacks_flow_and_summary() -> None:
    _reset_state()
    now = datetime.now(UTC)
    metrics.callbacks_by_business[DEFAULT_BUSINESS_ID] = {
        "111": CallbackItem(
            phone="111",
            first_seen=now - timedelta(hours=1),
            last_seen=now,
            count=2,
            channel="sms",
            lead_source="ads",
            status="PENDING",
            reason="PARTIAL_INTAKE",
        ),
        "222": CallbackItem(
            phone="222",
            first_seen=now - timedelta(hours=2),
            last_seen=now - timedelta(minutes=30),
            count=1,
            channel="phone",
            lead_source="referral",
            status="COMPLETED",
            last_result="done",
            reason="MISSED_CALL",
        ),
    }

    queue_resp = client.get("/v1/owner/callbacks")
    assert queue_resp.status_code == 200
    queue_items = queue_resp.json()["items"]
    # Only pending items are returned by the queue endpoint.
    assert len(queue_items) == 1
    assert queue_items[0]["phone"] == "111"
    assert queue_items[0]["channel"] == "sms"

    summary_resp = client.get("/v1/owner/callbacks/summary")
    assert summary_resp.status_code == 200
    summary = summary_resp.json()
    assert summary["total_callbacks"] == 2
    assert summary["pending"] == 1
    assert summary["completed"] == 1
    assert summary["partial_intake_callbacks"] == 1
    lead_sources = {item["lead_source"]: item for item in summary["lead_sources"]}
    assert lead_sources["ads"]["pending"] == 1

    update_resp = client.patch(
        "/v1/owner/callbacks/111",
        json={"status": "completed", "result": "called back"},
    )
    assert update_resp.status_code == 200
    updated = update_resp.json()
    assert updated["status"] == "COMPLETED"
    assert updated["last_result"] == "called back"

    delete_resp = client.delete("/v1/owner/callbacks/111")
    assert delete_resp.status_code == 204

    final_summary = client.get("/v1/owner/callbacks/summary").json()
    assert final_summary["pending"] == 0
    assert final_summary["completed"] == 1
    assert final_summary["total_callbacks"] == 1


def test_owner_metrics_segments_and_followups() -> None:
    _reset_state()
    now = datetime.now(UTC)

    cust_a = customers_repo.upsert(
        name="Alice",
        phone="100",
        email="alice@example.com",
        address="123 Main St",
        business_id=DEFAULT_BUSINESS_ID,
        tags=["vip"],
    )
    cust_b = customers_repo.upsert(
        name="Bob",
        phone="200",
        email=None,
        address="",
        business_id=DEFAULT_BUSINESS_ID,
        tags=["drain", "vip"],
    )

    appt_a = appointments_repo.create(
        customer_id=cust_a.id,
        start_time=now - timedelta(days=1),
        end_time=now - timedelta(days=1, hours=-1),
        service_type="Drain Cleaning",
        is_emergency=True,
        description="Emergency drain clean",
        lead_source="web",
        estimated_value=250,
        job_stage="Quoted",
        business_id=DEFAULT_BUSINESS_ID,
        tags=["vip", "drain"],
    )
    appt_a.status = "CONFIRMED"

    appt_b = appointments_repo.create(
        customer_id=cust_b.id,
        start_time=now - timedelta(days=2),
        end_time=now - timedelta(days=2, hours=-1),
        service_type="Install",
        is_emergency=False,
        description="Water heater install",
        lead_source="referral",
        estimated_value=500,
        job_stage="Scheduled",
        business_id=DEFAULT_BUSINESS_ID,
        tags=["install"],
    )
    appt_b.status = "CANCELLED"

    conv_a = conversations_repo.create(
        channel="sms", customer_id=cust_a.id, business_id=DEFAULT_BUSINESS_ID
    )
    conv_a.created_at = now - timedelta(days=1)
    conv_b = conversations_repo.create(
        channel="web", customer_id=cust_b.id, business_id=DEFAULT_BUSINESS_ID
    )
    conv_b.created_at = now - timedelta(days=1)

    metrics.sms_by_business[DEFAULT_BUSINESS_ID] = BusinessSmsMetrics(
        sms_sent_total=5,
        sms_sent_owner=2,
        sms_sent_customer=3,
        lead_followups_sent=2,
        retention_messages_sent=1,
        sms_confirmations_via_sms=1,
        sms_cancellations_via_sms=1,
        sms_reschedules_via_sms=1,
        sms_opt_out_events=2,
        sms_opt_in_events=1,
    )
    metrics.retention_by_business[DEFAULT_BUSINESS_ID] = {"winback": 1}

    sms_resp = client.get("/v1/owner/sms-metrics")
    assert sms_resp.status_code == 200
    sms_body = sms_resp.json()
    assert sms_body["owner_messages"] == 2
    assert sms_body["customer_messages"] == 3
    assert sms_body["total_messages"] == 5
    assert sms_body["confirmations_via_sms"] == 1
    assert sms_body["cancellations_via_sms"] == 1
    assert sms_body["confirmation_share_via_sms"] == 1.0
    assert sms_body["cancellation_share_via_sms"] == 1.0

    service_mix = client.get("/v1/owner/service-mix", params={"days": 30}).json()
    assert service_mix["total_appointments_30d"] == 1
    assert service_mix["emergency_appointments_30d"] == 1
    assert service_mix["service_type_counts_30d"]["Drain Cleaning"] == 1

    pipeline = client.get("/v1/owner/pipeline", params={"days": 30}).json()
    stages = {s["stage"]: s for s in pipeline["stages"]}
    assert stages["Quoted"]["count"] == 1
    assert pipeline["total_estimated_value"] >= 750.0

    segments = client.get("/v1/owner/segments").json()
    tags = {item["tag"]: item for item in segments["items"]}
    assert tags["vip"]["customers"] == 2
    assert tags["drain"]["appointments"] == 1
    assert tags["install"]["appointments"] == 1

    completeness = client.get("/v1/owner/data-completeness").json()
    assert completeness["total_customers"] == 2
    assert completeness["customers_with_email"] == 1
    assert completeness["total_appointments"] >= 1
    assert completeness["appointments_with_service_type"] >= 1

    followups = client.get("/v1/owner/followups", params={"days": 7}).json()
    assert followups["followups_sent"] == 2
    assert followups["recent_leads_with_appointments"] == 1
    assert followups["recent_leads_without_appointments"] == 1

    retention = client.get("/v1/owner/retention").json()
    assert retention["total_messages_sent"] == 1
    assert retention["campaigns"][0]["campaign_type"] == "winback"
