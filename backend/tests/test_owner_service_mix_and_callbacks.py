from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from app.main import app
from app.metrics import CallbackItem, metrics
from app.repositories import appointments_repo, customers_repo


client = TestClient(app)


def _reset_appointments_and_customers() -> None:
    # These repository attributes exist on the in-memory implementations used in tests.
    appointments_repo._by_id.clear()  # type: ignore[attr-defined]
    appointments_repo._by_customer.clear()  # type: ignore[attr-defined]
    appointments_repo._by_business.clear()  # type: ignore[attr-defined]
    customers_repo._by_id.clear()  # type: ignore[attr-defined]
    customers_repo._by_phone.clear()  # type: ignore[attr-defined]
    customers_repo._by_business.clear()  # type: ignore[attr-defined]


def test_owner_service_mix_counts_by_service_type_and_emergency_flag() -> None:
    _reset_appointments_and_customers()

    # Create a customer for the default business.
    customer = customers_repo.upsert(
        name="Service Mix Customer",
        phone="+15551234567",
        business_id="default_business",
    )
    now = datetime.now(UTC)

    # Emergency drain job within window.
    start1 = now - timedelta(days=2)
    end1 = start1 + timedelta(hours=1)
    appointments_repo.create(
        customer_id=customer.id,
        start_time=start1,
        end_time=end1,
        service_type="drain_or_sewer",
        is_emergency=True,
        description="Emergency drain",
        business_id="default_business",
        calendar_event_id=None,
    )

    # Standard install job within window.
    start2 = now - timedelta(days=3)
    end2 = start2 + timedelta(hours=2)
    appointments_repo.create(
        customer_id=customer.id,
        start_time=start2,
        end_time=end2,
        service_type="tankless_water_heater",
        is_emergency=False,
        description="Standard install",
        business_id="default_business",
        calendar_event_id=None,
    )

    # Old appointment outside window; should be ignored.
    start_old = now - timedelta(days=40)
    end_old = start_old + timedelta(hours=1)
    appointments_repo.create(
        customer_id=customer.id,
        start_time=start_old,
        end_time=end_old,
        service_type="ignored_service",
        is_emergency=True,
        description="Old job",
        business_id="default_business",
        calendar_event_id=None,
    )

    resp = client.get("/v1/owner/service-mix?days=30")
    assert resp.status_code == 200
    body = resp.json()

    assert body["total_appointments_30d"] == 2
    assert body["emergency_appointments_30d"] == 1

    svc_counts = body["service_type_counts_30d"]
    emergency_counts = body["emergency_service_type_counts_30d"]

    assert svc_counts["drain_or_sewer"] == 1
    assert svc_counts["tankless_water_heater"] == 1
    assert "ignored_service" not in svc_counts

    assert emergency_counts["drain_or_sewer"] == 1
    assert "tankless_water_heater" not in emergency_counts


def test_owner_callbacks_queue_and_summary_reflect_metrics_state() -> None:
    # Seed the callback queue for the default business directly via metrics.
    metrics.callbacks_by_business.clear()
    biz_id = "default_business"
    now = datetime.now(UTC)

    queue = metrics.callbacks_by_business.setdefault(biz_id, {})
    queue["+15550000001"] = CallbackItem(
        phone="+15550000001",
        first_seen=now - timedelta(hours=2),
        last_seen=now - timedelta(hours=1),
        count=1,
        channel="phone",
        lead_source="web",
        status="PENDING",
        last_result=None,
        reason="MISSED_CALL",
    )
    queue["+15550000002"] = CallbackItem(
        phone="+15550000002",
        first_seen=now - timedelta(days=1),
        last_seen=now - timedelta(minutes=30),
        count=2,
        channel="phone",
        lead_source="referral",
        status="COMPLETED",
        last_result="completed",
        reason="PARTIAL_INTAKE",
    )

    # Queue endpoint should list only pending callbacks, ordered by last_seen.
    queue_resp = client.get("/v1/owner/callbacks")
    assert queue_resp.status_code == 200
    queue_body = queue_resp.json()
    items = queue_body["items"]
    assert len(items) == 1
    item = items[0]
    assert item["phone"] == "+15550000001"
    assert item["lead_source"] == "web"
    assert item["status"] == "PENDING"
    assert item["reason"] == "MISSED_CALL"

    # Summary endpoint should include counts and per-lead-source breakdown.
    summary_resp = client.get("/v1/owner/callbacks/summary")
    assert summary_resp.status_code == 200
    summary = summary_resp.json()

    assert summary["total_callbacks"] == 2
    assert summary["pending"] == 1
    assert summary["completed"] == 1
    assert summary["unreachable"] == 0
    assert summary["missed_callbacks"] == 1
    assert summary["partial_intake_callbacks"] == 1

    lead_sources = {ls["lead_source"]: ls for ls in summary["lead_sources"]}
    assert lead_sources["web"]["total"] == 1
    assert lead_sources["web"]["pending"] == 1
    assert lead_sources["referral"]["total"] == 1
    assert lead_sources["referral"]["completed"] == 1

    # Clear a callback and ensure it is removed from the queue.
    del_resp = client.delete("/v1/owner/callbacks/+15550000001")
    assert del_resp.status_code == 204

    queue_resp2 = client.get("/v1/owner/callbacks")
    assert queue_resp2.status_code == 200
    assert queue_resp2.json()["items"] == []


def test_owner_service_mix_ignores_cancelled_and_uses_unspecified_bucket() -> None:
    _reset_appointments_and_customers()

    customer = customers_repo.upsert(
        name="Unspecified Service Customer",
        phone="+15557654321",
        business_id="default_business",
    )
    now = datetime.now(UTC)

    # Appointment with no explicit service_type should fall into "unspecified".
    start_unspecified = now - timedelta(days=1)
    end_unspecified = start_unspecified + timedelta(hours=1)
    appointments_repo.create(
        customer_id=customer.id,
        start_time=start_unspecified,
        end_time=end_unspecified,
        service_type=None,
        is_emergency=False,
        description="No explicit service type",
        business_id="default_business",
        calendar_event_id=None,
    )

    # Cancelled appointment within the window should be ignored by service-mix.
    start_cancelled = now - timedelta(days=2)
    end_cancelled = start_cancelled + timedelta(hours=1)
    appt_cancelled = appointments_repo.create(
        customer_id=customer.id,
        start_time=start_cancelled,
        end_time=end_cancelled,
        service_type="cancelled_service",
        is_emergency=False,
        description="Cancelled job",
        business_id="default_business",
        calendar_event_id=None,
    )
    # Explicitly set the status to CANCELLED so it should be filtered out.
    appointments_repo.update(appt_cancelled.id, status="CANCELLED")

    resp = client.get("/v1/owner/service-mix?days=7")
    assert resp.status_code == 200
    body = resp.json()

    # Only the unspecified appointment should be counted.
    assert body["total_appointments_30d"] == 1
    assert body["emergency_appointments_30d"] == 0

    svc_counts = body["service_type_counts_30d"]
    assert svc_counts["unspecified"] == 1
    assert "cancelled_service" not in svc_counts


def test_update_owner_callback_404_when_item_missing() -> None:
    metrics.callbacks_by_business.clear()

    resp = client.patch(
        "/v1/owner/callbacks/+15559999999",
        json={"status": "COMPLETED"},
    )
    assert resp.status_code == 404


def test_update_owner_callback_valid_statuses_and_results() -> None:
    metrics.callbacks_by_business.clear()
    biz_id = "default_business"
    now = datetime.now(UTC)

    queue = metrics.callbacks_by_business.setdefault(biz_id, {})
    queue["+15550000003"] = CallbackItem(
        phone="+15550000003",
        first_seen=now - timedelta(hours=3),
        last_seen=now - timedelta(hours=1),
        count=1,
        channel="phone",
        lead_source="web",
        status="PENDING",
        last_result=None,
        reason="MISSED_CALL",
    )

    # Invalid status should be rejected with 400.
    bad_resp = client.patch(
        "/v1/owner/callbacks/+15550000003",
        json={"status": "INVALID"},
    )
    assert bad_resp.status_code == 400

    # Mark as completed without providing an explicit result; default should be used.
    completed_resp = client.patch(
        "/v1/owner/callbacks/+15550000003",
        json={"status": "COMPLETED"},
    )
    assert completed_resp.status_code == 200
    completed_body = completed_resp.json()
    assert completed_body["status"] == "COMPLETED"
    assert completed_body["last_result"] == "completed"

    # Mark as unreachable with an explicit custom result to override the default.
    unreachable_resp = client.patch(
        "/v1/owner/callbacks/+15550000003",
        json={"status": "UNREACHABLE", "result": "left_voicemail"},
    )
    assert unreachable_resp.status_code == 200
    unreachable_body = unreachable_resp.json()
    assert unreachable_body["status"] == "UNREACHABLE"
    assert unreachable_body["last_result"] == "left_voicemail"

    # Summary should now reflect a single completed or unreachable callback and no pending.
    summary_resp = client.get("/v1/owner/callbacks/summary")
    assert summary_resp.status_code == 200
    summary = summary_resp.json()
    assert summary["total_callbacks"] == 1
    assert summary["pending"] == 0
    # Depending on the final status, one of these should be non-zero.
    assert summary["completed"] + summary["unreachable"] == 1
