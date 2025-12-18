from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_admin_audit_includes_recent_requests_and_filters():
    # Trigger an auditable request with a tenant header so it's captured.
    biz_id = "audit-biz"
    health = client.get("/healthz", headers={"X-Business-ID": biz_id})
    assert health.status_code == 200

    # Fetch audit events, filtered by business and path substring.
    audit = client.get(
        "/v1/admin/audit",
        params={"business_id": biz_id, "path_contains": "healthz", "limit": 10},
    )
    assert audit.status_code == 200
    events = audit.json()
    assert isinstance(events, list)
    assert any("/healthz" in ev.get("path", "") for ev in events)
    sample = events[0]
    assert {
        "id",
        "created_at",
        "actor_type",
        "business_id",
        "path",
        "method",
        "status_code",
    } <= set(sample.keys())
