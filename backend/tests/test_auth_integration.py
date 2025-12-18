from fastapi.testclient import TestClient

from app.db import SessionLocal, SQLALCHEMY_AVAILABLE
from app.db_models import BusinessDB
from app.main import app


client = TestClient(app)


def _get_default_business_id() -> str:
    if not SQLALCHEMY_AVAILABLE or SessionLocal is None:
        return "default_business"
    session = SessionLocal()
    try:
        row = session.get(BusinessDB, "default_business")
        if row is not None:
            return row.id
    finally:
        session.close()
    return "default_business"


def test_auth_start_returns_stub_authorization_url() -> None:
    business_id = _get_default_business_id()
    resp = client.get(f"/auth/linkedin/start?business_id={business_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["provider"] == "linkedin"
    assert body["authorization_url"] == (
        f"https://example.com/oauth/linkedin?state={business_id}"
    )
    assert "Replace authorization_url" in body["note"]


def test_auth_start_rejects_unsupported_provider() -> None:
    business_id = _get_default_business_id()
    resp = client.get(f"/auth/unknown/start?business_id={business_id}")
    assert resp.status_code == 404


def test_auth_callback_marks_integration_connected() -> None:
    business_id = _get_default_business_id()

    resp = client.get(f"/auth/linkedin/callback?state={business_id}&code=dummy-code")
    assert resp.status_code == 200
    body = resp.json()
    assert body["provider"] == "linkedin"
    assert body["business_id"] == business_id
    assert body["connected"] is True
    assert body["redirect_url"] == "/dashboard/onboarding.html"

    if SQLALCHEMY_AVAILABLE and SessionLocal is not None:
        session = SessionLocal()
        try:
            row = session.get(BusinessDB, business_id)
            if row is not None:
                assert getattr(row, "integration_linkedin_status", None) == "connected"
        finally:
            session.close()


def test_auth_callback_returns_404_for_missing_business() -> None:
    resp = client.get("/auth/openai/callback?state=nonexistent-business&code=dummy")
    assert resp.status_code == 404


def test_auth_callback_rejects_unsupported_provider() -> None:
    resp = client.get("/auth/unknown/callback?state=default_business&code=dummy")
    assert resp.status_code == 404


def test_auth_start_uses_signed_state_when_not_testing(monkeypatch) -> None:
    from app.routers import auth_integration
    from app.services.oauth_state import decode_state

    class DummyOAuth:
        def __init__(self) -> None:
            self.state_secret = "secret"
            self.redirect_base = "https://example.com/auth"
            self.linkedin_client_id = None
            self.linkedin_scopes = "r_liteprofile"
            self.google_client_id = None
            self.google_client_secret = None
            self.gmail_scopes = "gmail.readonly"
            self.gcalendar_scopes = "calendar"

    class DummySettings:
        def __init__(self) -> None:
            self.oauth = DummyOAuth()

    monkeypatch.setattr(auth_integration, "_is_testing_mode", lambda: False)
    monkeypatch.setattr(auth_integration, "get_settings", lambda: DummySettings())

    business_id = _get_default_business_id()
    resp = client.get(f"/auth/gcalendar/start?business_id={business_id}")
    assert resp.status_code == 200
    authorization_url = resp.json()["authorization_url"]
    assert "state=" in authorization_url
    state = authorization_url.split("state=", 1)[1]
    assert state != business_id

    decoded_business_id, decoded_provider = decode_state(state, "secret")
    assert decoded_business_id == business_id
    assert decoded_provider == "gcalendar"


def test_auth_callback_rejects_invalid_state_when_not_testing(monkeypatch) -> None:
    from app.routers import auth_integration

    class DummyOAuth:
        def __init__(self) -> None:
            self.state_secret = "secret"
            self.redirect_base = "https://example.com/auth"
            self.linkedin_client_id = None
            self.linkedin_scopes = "r_liteprofile"
            self.google_client_id = None
            self.google_client_secret = None
            self.gmail_scopes = "gmail.readonly"
            self.gcalendar_scopes = "calendar"

    class DummySettings:
        def __init__(self) -> None:
            self.oauth = DummyOAuth()

    monkeypatch.setattr(auth_integration, "_is_testing_mode", lambda: False)
    monkeypatch.setattr(auth_integration, "get_settings", lambda: DummySettings())

    resp = client.get("/auth/gcalendar/callback?state=invalid&code=dummy")
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Invalid state"


def test_auth_callback_rejects_provider_mismatch(monkeypatch) -> None:
    from app.routers import auth_integration
    from app.services.oauth_state import encode_state

    class DummyOAuth:
        def __init__(self) -> None:
            self.state_secret = "secret"
            self.redirect_base = "https://example.com/auth"
            self.linkedin_client_id = None
            self.linkedin_scopes = "r_liteprofile"
            self.google_client_id = None
            self.google_client_secret = None
            self.gmail_scopes = "gmail.readonly"
            self.gcalendar_scopes = "calendar"

    class DummySettings:
        def __init__(self) -> None:
            self.oauth = DummyOAuth()

    monkeypatch.setattr(auth_integration, "_is_testing_mode", lambda: False)
    monkeypatch.setattr(auth_integration, "get_settings", lambda: DummySettings())

    business_id = _get_default_business_id()
    state = encode_state(business_id, "gmail", "secret")
    resp = client.get(f"/auth/gcalendar/callback?state={state}&code=dummy")
    assert resp.status_code == 400
    assert resp.json()["detail"] == "State provider mismatch"
