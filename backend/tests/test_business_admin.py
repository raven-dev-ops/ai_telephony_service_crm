from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.db import SQLALCHEMY_AVAILABLE, SessionLocal
from app.db_models import (
    AppointmentDB,
    BusinessDB,
    ConversationDB,
    ConversationMessageDB,
)
from app.main import app
from app.metrics import BusinessTwilioMetrics, metrics


client = TestClient(app)


pytestmark = pytest.mark.skipif(
    not SQLALCHEMY_AVAILABLE,
    reason="Admin tenant usage endpoints require database support",
)


def test_demo_tenant_usage_has_expected_counts():
    # Seed demo tenants and capture one business ID.
    resp = client.post("/v1/admin/demo-tenants")
    assert resp.status_code == 200
    payload = resp.json()
    assert "businesses" in payload and payload["businesses"]

    tenant = payload["businesses"][0]
    business_id = tenant["id"]

    usage_resp = client.get(f"/v1/admin/businesses/{business_id}/usage")
    assert usage_resp.status_code == 200
    usage = usage_resp.json()

    assert usage["id"] == business_id
    assert usage["name"] == tenant["name"]

    # Seeded data includes two customers and two appointments,
    # with one of the appointments marked as an emergency. All are
    # created "now", so they fall into the last 7/30 days windows.
    assert usage["total_customers"] == 2
    assert usage["total_appointments"] == 2
    assert usage["emergency_appointments"] == 1
    assert usage["sms_opt_out_customers"] == 0
    assert usage["appointments_last_7_days"] == 2
    assert usage["appointments_last_30_days"] == 2
    assert usage["emergencies_last_7_days"] == 1
    assert usage["emergencies_last_30_days"] == 1


def test_list_business_usage_includes_demo_tenants():
    # Ensure demo tenants exist.
    resp = client.post("/v1/admin/demo-tenants")
    assert resp.status_code == 200
    tenants = resp.json()["businesses"]
    tenant_ids = {t["id"] for t in tenants}

    list_resp = client.get("/v1/admin/businesses/usage")
    assert list_resp.status_code == 200
    all_usage = list_resp.json()

    # There should be entries for each demo tenant.
    usage_ids = {u["id"] for u in all_usage}
    assert tenant_ids.issubset(usage_ids)


def test_business_usage_csv_export():
    # Ensure demo tenants exist.
    resp = client.post("/v1/admin/demo-tenants")
    assert resp.status_code == 200

    csv_resp = client.get("/v1/admin/businesses/usage.csv")
    assert csv_resp.status_code == 200
    assert csv_resp.headers.get("content-type", "").startswith("text/csv")

    text = csv_resp.text
    lines = [line for line in text.splitlines() if line.strip()]
    # Header plus at least one data row.
    assert len(lines) >= 2
    header = lines[0].split(",")
    assert header[0] == "id"
    assert "total_customers" in header
    assert "sms_owner_messages" in header


def test_rotate_widget_token_changes_token_and_preserves_api_key():
    # Ensure demo tenants exist.
    resp = client.post("/v1/admin/demo-tenants")
    assert resp.status_code == 200

    # Pick the first business and fetch its details.
    list_resp = client.get("/v1/admin/businesses")
    assert list_resp.status_code == 200
    businesses = list_resp.json()
    assert businesses
    b = businesses[0]
    business_id = b["id"]

    original_api_key = b["api_key"]
    original_widget_token = b.get("widget_token")

    rotate_resp = client.post(f"/v1/admin/businesses/{business_id}/rotate-widget-token")
    assert rotate_resp.status_code == 200
    rotated = rotate_resp.json()

    assert rotated["id"] == business_id
    # API key should not change when rotating the widget token.
    assert rotated["api_key"] == original_api_key
    # Widget token should exist and be different from any previous value.
    assert rotated["widget_token"]
    if original_widget_token:
        assert rotated["widget_token"] != original_widget_token


def test_get_business_via_widget_token_allows_crm_access():
    # Seed demo tenants to ensure we have at least one business and data.
    resp = client.post("/v1/admin/demo-tenants")
    assert resp.status_code == 200
    tenants = resp.json()["businesses"]
    assert tenants
    business_id = tenants[0]["id"]

    # Rotate widget token to ensure it is populated.
    rotate_resp = client.post(f"/v1/admin/businesses/{business_id}/rotate-widget-token")
    assert rotate_resp.status_code == 200
    widget_token = rotate_resp.json()["widget_token"]
    assert widget_token

    # Access a tenant-scoped CRM route using only X-Widget-Token.
    crm_resp = client.get("/v1/crm/customers", headers={"X-Widget-Token": widget_token})
    assert crm_resp.status_code == 200
    customers = crm_resp.json()
    # Demo tenants seed at least two customers per tenant.
    assert isinstance(customers, list)
    assert len(customers) >= 2


def test_admin_environment_uses_env_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "staging")
    resp = client.get("/v1/admin/environment")
    assert resp.status_code == 200
    body = resp.json()
    assert body["environment"] == "staging"


def test_admin_governance_summary_includes_tenants() -> None:
    # Seed demo tenants and then fetch governance summary.
    resp = client.post("/v1/admin/demo-tenants")
    assert resp.status_code == 200
    tenants = resp.json()["businesses"]
    assert tenants

    gov_resp = client.get("/v1/admin/governance")
    assert gov_resp.status_code == 200
    summary = gov_resp.json()

    assert isinstance(summary["multi_tenant_mode"], bool)
    assert summary["business_count"] >= len(tenants)
    assert isinstance(summary["require_business_api_key"], bool)
    assert isinstance(summary["verify_twilio_signatures"], bool)

    tenant_summaries = summary["tenants"]
    assert isinstance(tenant_summaries, list)
    assert tenant_summaries
    first = tenant_summaries[0]
    assert "id" in first and "name" in first and "status" in first


def test_admin_audit_endpoint_returns_events() -> None:
    # Trigger a few requests so audit events are recorded.
    resp = client.get("/healthz")
    assert resp.status_code == 200
    resp = client.get("/v1/admin/environment")
    assert resp.status_code == 200

    audit_resp = client.get("/v1/admin/audit?limit=10")
    assert audit_resp.status_code == 200
    events = audit_resp.json()
    # There should be at least one recent audit event.
    assert isinstance(events, list)
    assert events
    first = events[0]
    assert "id" in first
    assert "path" in first
    assert "method" in first
    assert "status_code" in first


def test_admin_audit_filters_by_business_and_actor_and_time_window() -> None:
    # Create an audit event associated with a specific business via headers.
    resp = client.get("/healthz", headers={"X-Business-ID": "audit-biz"})
    assert resp.status_code == 200

    # Filter by business_id and actor_type; events should be recent.
    audit_resp = client.get(
        "/v1/admin/audit",
        params={
            "business_id": "audit-biz",
            "actor_type": "anonymous",
            "since_minutes": 10,
            "limit": 50,
        },
    )
    assert audit_resp.status_code == 200
    events = audit_resp.json()
    assert isinstance(events, list)
    assert events
    for ev in events:
        assert ev["business_id"] == "audit-biz"
        assert ev["actor_type"] == "anonymous"


def test_admin_twilio_health_reflects_config_and_metrics() -> None:
    # Seed global and per-business Twilio metrics.
    metrics.twilio_voice_requests = 5
    metrics.twilio_voice_errors = 1
    metrics.twilio_sms_requests = 7
    metrics.twilio_sms_errors = 2
    metrics.twilio_by_business.clear()
    metrics.twilio_by_business["biz-twilio"] = BusinessTwilioMetrics(
        voice_requests=3,
        voice_errors=1,
        sms_requests=4,
        sms_errors=0,
    )

    resp = client.get("/v1/admin/twilio/health")
    assert resp.status_code == 200
    body = resp.json()

    # Global aggregates.
    assert body["twilio_voice_requests"] == metrics.twilio_voice_requests
    assert body["twilio_voice_errors"] == metrics.twilio_voice_errors
    assert body["twilio_sms_requests"] == metrics.twilio_sms_requests
    assert body["twilio_sms_errors"] == metrics.twilio_sms_errors

    per_map = {b["business_id"]: b for b in body["per_business"]}
    assert "biz-twilio" in per_map
    biz_stats = per_map["biz-twilio"]
    assert biz_stats["voice_requests"] == 3
    assert biz_stats["voice_errors"] == 1
    assert biz_stats["sms_requests"] == 4
    assert biz_stats["sms_errors"] == 0

    cfg = body["config"]
    # Shape checks for Twilio configuration status.
    assert "provider" in cfg
    assert isinstance(cfg["from_number_set"], bool)
    assert isinstance(cfg["owner_number_set"], bool)
    assert isinstance(cfg["account_sid_set"], bool)
    assert isinstance(cfg["auth_token_set"], bool)
    assert isinstance(cfg["verify_signatures"], bool)


def test_admin_gcp_storage_health_uses_service_result(monkeypatch) -> None:
    from app.routers import business_admin as admin_module

    class DummyHealth:
        configured = True
        project_id = "proj-123"
        bucket_name = "bucket-abc"
        library_available = True
        can_connect = True
        error = None

    monkeypatch.setattr(admin_module, "get_gcs_health", lambda: DummyHealth())

    resp = client.get("/v1/admin/gcp/storage-health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["configured"] is True
    assert body["project_id"] == "proj-123"
    assert body["bucket_name"] == "bucket-abc"
    assert body["library_available"] is True
    assert body["can_connect"] is True
    assert body["error"] is None


def test_admin_stripe_health_includes_config_and_usage(monkeypatch) -> None:
    # Seed subscription metrics.
    metrics.subscription_activations = 2
    metrics.subscription_failures = 1
    metrics.billing_webhook_failures = 3

    # Seed a couple of businesses with Stripe fields.
    if SQLALCHEMY_AVAILABLE and SessionLocal is not None:
        session = SessionLocal()
        try:
            session.query(BusinessDB).filter(
                BusinessDB.id.in_(["stripe_a", "stripe_b"])
            ).delete(synchronize_session=False)
            session.add(
                BusinessDB(  # type: ignore[arg-type]
                    id="stripe_a",
                    name="Stripe A",
                    status="ACTIVE",
                    stripe_customer_id="cus_A",
                    stripe_subscription_id="sub_A",
                    subscription_status="active",
                )
            )
            session.add(
                BusinessDB(  # type: ignore[arg-type]
                    id="stripe_b",
                    name="Stripe B",
                    status="ACTIVE",
                    stripe_customer_id=None,
                    stripe_subscription_id="sub_B",
                    subscription_status="past_due",
                )
            )
            session.commit()
        finally:
            session.close()

    resp = client.get("/v1/admin/stripe/health")
    assert resp.status_code == 200
    body = resp.json()

    cfg = body["config"]
    assert "api_key_set" in cfg and "publishable_key_set" in cfg
    assert "webhook_secret_set" in cfg and "price_basic_set" in cfg
    assert "verify_signatures" in cfg and "use_stub" in cfg

    assert body["subscription_activations"] == metrics.subscription_activations
    assert body["subscription_failures"] == metrics.subscription_failures
    assert body["billing_webhook_failures"] == metrics.billing_webhook_failures

    subs = body["subscriptions_by_status"]
    assert subs.get("active", 0) >= 1
    assert subs.get("past_due", 0) >= 1
    assert body["customers_with_stripe_id"] >= 1
    assert body["businesses_with_subscription"] >= 1


def test_admin_retention_prune_deletes_old_data() -> None:
    assert SessionLocal is not None
    session = SessionLocal()
    try:
        biz_id = "retention_test"
        row = session.get(BusinessDB, biz_id)
        if row is None:
            row = BusinessDB(  # type: ignore[call-arg]
                id=biz_id,
                name="Retention Test",
                appointment_retention_days=30,
                conversation_retention_days=30,
            )
            session.add(row)
            session.commit()
            session.refresh(row)

        # Create old appointment and conversation + message that should be pruned.
        old_time = datetime.now(UTC) - timedelta(days=60)
        appt = AppointmentDB(  # type: ignore[call-arg]
            id="appt-retention",
            customer_id="cust-retention",
            business_id=biz_id,
            start_time=old_time,
            end_time=old_time,
            service_type="Old Job",
            is_emergency=False,
        )
        session.add(appt)

        conv = ConversationDB(  # type: ignore[call-arg]
            id="conv-retention",
            business_id=biz_id,
            customer_id="cust-retention",
            channel="sms",
            created_at=old_time,
        )
        session.add(conv)

        msg = ConversationMessageDB(  # type: ignore[call-arg]
            id="msg-retention",
            conversation_id="conv-retention",
            role="user",
            text="old message",
            timestamp=old_time,
        )
        session.add(msg)

        session.commit()
    finally:
        session.close()

    resp = client.post("/v1/admin/retention/prune")
    assert resp.status_code == 200
    body = resp.json()
    # All of the seeded rows should be counted as deleted.
    assert body["appointments_deleted"] >= 1
    assert body["conversations_deleted"] >= 1
    assert body["conversation_messages_deleted"] >= 1
