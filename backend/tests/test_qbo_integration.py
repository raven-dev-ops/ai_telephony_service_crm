from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from app.main import app
from app.repositories import customers_repo
from app.db import SessionLocal
from app.db_models import BusinessDB


client = TestClient(app)


class DummyQuickBooks:
    def __init__(self) -> None:
        self.client_id = "dummy"
        self.client_secret = "secret"
        self.redirect_uri = "https://example.com/callback"
        self.scopes = "com.intuit.quickbooks.accounting"
        self.sandbox = True
        self.authorize_base = "https://sandbox.qbo.intuit.com/connect/oauth2"
        self.token_base = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"


class DummySettings:
    def __init__(self) -> None:
        self.quickbooks = DummyQuickBooks()


def test_qbo_callback_stub_when_not_configured(monkeypatch):
    from app.routers import qbo_integration

    class NoCreds:
        def __init__(self) -> None:
            self.client_id = None
            self.client_secret = None
            self.redirect_uri = None
            self.scopes = "scope"
            self.sandbox = True
            self.authorize_base = "https://example.com/auth"
            self.token_base = "https://example.com/token"

    class SettingsNoCreds:
        def __init__(self) -> None:
            self.quickbooks = NoCreds()

    monkeypatch.setattr(qbo_integration, "get_settings", lambda: SettingsNoCreds())
    resp = client.get(
        "/v1/integrations/qbo/callback",
        params={"code": "stubcode", "state": "default_business"},
    )
    assert resp.status_code == 200
    assert resp.json()["connected"] is True


def test_qbo_sync_real_flow_with_mocks(monkeypatch):
    from app.routers import qbo_integration

    # Reset repos and set dummy settings with credentials.
    if hasattr(customers_repo, "_by_id"):
        customers_repo._by_id.clear()  # type: ignore[attr-defined]
        customers_repo._by_phone.clear()  # type: ignore[attr-defined]
        customers_repo._by_business.clear()  # type: ignore[attr-defined]
    from app.repositories import appointments_repo

    if hasattr(appointments_repo, "_by_id"):
        appointments_repo._by_id.clear()  # type: ignore[attr-defined]
        appointments_repo._by_business.clear()  # type: ignore[attr-defined]
        appointments_repo._by_customer.clear()  # type: ignore[attr-defined]

    monkeypatch.setattr(qbo_integration, "get_settings", lambda: DummySettings())

    # Seed a customer + appointment.
    cust = customers_repo.upsert(
        name="QBO User",
        phone="+15557779999",
        email="qbo@test.com",
        business_id="default_business",
    )
    start = datetime.now(UTC) + timedelta(days=1)
    end = start + timedelta(hours=1)
    appointments_repo.create(
        customer_id=cust.id,
        start_time=start,
        end_time=end,
        service_type="Install",
        is_emergency=False,
        description="Install sink",
        business_id="default_business",
        calendar_event_id="evt_qbo",
    )

    class DummyResp:
        def __init__(self, status_code=200, body=None, text="{}"):
            self.status_code = status_code
            self._body = body or {}
            self.text = text

        def json(self):
            return self._body

    calls = []

    class DummyClient:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def request(self, method, url, headers=None, json=None):
            calls.append({"method": method, "url": url, "json": json})
            if "customer" in url:
                return DummyResp(body={"Customer": {"Id": "100"}})
            return DummyResp(body={"SalesReceipt": {"Id": "200"}})

        def post(self, url, data=None, auth=None):
            # Token exchange
            return DummyResp(
                body={"access_token": "a", "refresh_token": "r", "expires_in": 3600}
            )

    monkeypatch.setattr(
        qbo_integration, "httpx", type("X", (), {"Client": DummyClient})
    )

    # Connect to seed tokens/realm.
    cb_resp = client.get(
        "/v1/integrations/qbo/callback",
        params={"code": "abc123", "realmId": "realm-1", "state": "default_business"},
    )
    assert cb_resp.status_code == 200

    sync_resp = client.post("/v1/integrations/qbo/sync")
    assert sync_resp.status_code == 200
    body = sync_resp.json()
    assert body["customers_pushed"] == 1
    assert body["receipts_pushed"] == 1
    assert calls and any("salesreceipt" in c["url"] for c in calls)


def test_qbo_refresh_uses_live_tokens(monkeypatch):
    from app.routers import qbo_integration

    class DummyQB:
        def __init__(self) -> None:
            self.client_id = "id"
            self.client_secret = "secret"
            self.redirect_uri = "https://redirect"
            self.scopes = "scope"
            self.sandbox = True
            self.authorize_base = "https://example.com/auth"
            self.token_base = "https://example.com/token"

    class Settings:
        def __init__(self) -> None:
            self.quickbooks = DummyQB()

    class DummyResp:
        def __init__(self, body=None, status_code=200):
            self._body = body or {
                "access_token": "new_access",
                "refresh_token": "new_refresh",
                "expires_in": 1200,
            }
            self.status_code = status_code
            self.text = "ok"

        def json(self):
            return self._body

    class DummyClient:
        def __init__(self, timeout=None):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, data=None, auth=None):
            assert "refresh_token" in data
            return DummyResp()

        def request(self, method, url, headers=None, json=None):
            if "customer" in url:
                return DummyResp(body={"Customer": {"Id": "1"}})
            return DummyResp(body={"SalesReceipt": {"Id": "2"}})

    monkeypatch.setattr(qbo_integration, "get_settings", lambda: Settings())
    monkeypatch.setattr(
        qbo_integration, "httpx", type("X", (), {"Client": DummyClient})
    )

    session = SessionLocal()
    try:
        row = session.get(BusinessDB, "default_business")
        row.integration_qbo_status = "connected"  # type: ignore[assignment]
        row.qbo_realm_id = "realm-123"  # type: ignore[assignment]
        row.qbo_access_token = "old_access"  # type: ignore[assignment]
        row.qbo_refresh_token = "old_refresh"  # type: ignore[assignment]
        row.qbo_token_expires_at = datetime.now(UTC) - timedelta(minutes=1)  # type: ignore[assignment]
        session.add(row)
        session.commit()
    finally:
        session.close()

    resp = client.post("/v1/integrations/qbo/sync")
    assert resp.status_code == 200
    data = resp.json()
    assert data["note"] == "QuickBooks sync completed."
    session = SessionLocal()
    try:
        row = session.get(BusinessDB, "default_business")
        assert row.qbo_access_token == "new_access"
    finally:
        session.close()


def test_qbo_sync_stub_without_creds(monkeypatch):
    from app.routers import qbo_integration

    class NoCreds:
        def __init__(self) -> None:
            self.client_id = None
            self.client_secret = None
            self.redirect_uri = None
            self.scopes = "scope"
            self.sandbox = True
            self.authorize_base = "https://example.com/auth"
            self.token_base = "https://example.com/token"

    class SettingsNoCreds:
        def __init__(self) -> None:
            self.quickbooks = NoCreds()

    monkeypatch.setattr(qbo_integration, "get_settings", lambda: SettingsNoCreds())
    resp = client.post("/v1/integrations/qbo/sync")
    assert resp.status_code in {200, 400}
    if resp.status_code == 200:
        assert "Stubbed QuickBooks export" in resp.json().get("note", "")


def test_qbo_callback_failure_when_configured(monkeypatch):
    from app.routers import qbo_integration

    class DummyQB:
        def __init__(self) -> None:
            self.client_id = "id"
            self.client_secret = "secret"
            self.redirect_uri = "https://redirect"
            self.scopes = "scope"
            self.sandbox = True
            self.authorize_base = "https://example.com/auth"
            self.token_base = "https://example.com/token"

    class Settings:
        def __init__(self) -> None:
            self.quickbooks = DummyQB()

    class DummyResp:
        status_code = 500

        def json(self):
            return {}

        @property
        def text(self):
            return "error"

    class DummyClient:
        def __init__(self, timeout=None):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, data=None, auth=None):
            return DummyResp()

    monkeypatch.setattr(qbo_integration, "get_settings", lambda: Settings())
    monkeypatch.setattr(
        qbo_integration, "httpx", type("X", (), {"Client": DummyClient})
    )

    resp = client.get(
        "/v1/integrations/qbo/callback",
        params={"code": "abc", "state": "default_business", "realmId": "1"},
    )
    assert resp.status_code == 502
    assert "token exchange failed" in resp.json().get("detail", "")
