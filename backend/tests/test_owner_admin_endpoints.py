from fastapi.testclient import TestClient

from app import deps
from app.main import app
from app.routers import owner as owner_router


client = TestClient(app)


def _apply_owner_overrides(business_id: str = "biz-admin") -> None:
    app.dependency_overrides[deps.require_owner_dashboard_auth] = lambda: None
    app.dependency_overrides[deps.ensure_business_active] = lambda: business_id


def test_owner_environment_reflects_env(monkeypatch):
    _apply_owner_overrides()
    monkeypatch.setenv("ENVIRONMENT", "staging")
    try:
        resp = client.get("/v1/owner/environment")
        assert resp.status_code == 200
        assert resp.json()["environment"] == "staging"
    finally:
        app.dependency_overrides.clear()


def test_owner_tenant_data_delete_rejects_wrong_confirm(monkeypatch):
    _apply_owner_overrides()
    try:
        resp = client.delete("/v1/owner/tenant-data?confirm=WRONG")
        assert resp.status_code == 400
    finally:
        app.dependency_overrides.clear()


def test_owner_tenant_data_delete_503_when_db_unavailable(monkeypatch):
    _apply_owner_overrides()
    monkeypatch.setattr(owner_router, "SQLALCHEMY_AVAILABLE", False)
    monkeypatch.setattr(owner_router, "SessionLocal", None)
    try:
        resp = client.delete("/v1/owner/tenant-data?confirm=DELETE")
        assert resp.status_code == 503
    finally:
        app.dependency_overrides.clear()
