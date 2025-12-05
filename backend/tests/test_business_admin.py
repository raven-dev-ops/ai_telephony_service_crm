from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.db import SQLALCHEMY_AVAILABLE
from app.main import app


client = TestClient(app)


pytestmark = pytest.mark.skipif(
    not SQLALCHEMY_AVAILABLE, reason="Admin tenant usage endpoints require database support"
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
