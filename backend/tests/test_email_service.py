import asyncio
import types

from app import config
from app.services.email_service import email_service, EmailResult
from app.services.oauth_tokens import oauth_store


class DummyResponse:
    def __init__(self, status_code: int = 200, text: str = "{}"):
        self.status_code = status_code
        self.text = text

    def json(self):
        return {"id": "msg_123"}


class DummyClient:
    def __init__(self, responses=None, *args, **kwargs):
        self.responses = responses or [DummyResponse()]
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url, headers=None, json=None, data=None):
        self.calls.append({"url": url, "headers": headers, "json": json, "data": data})
        idx = min(len(self.responses) - 1, len(self.calls) - 1)
        return self.responses[idx]


def test_send_email_without_tokens_uses_stub(monkeypatch):
    email_service._sent.clear()
    monkeypatch.setenv("EMAIL_PROVIDER", "gmail")
    config.get_settings.cache_clear()
    # No tokens stored for this tenant.
    result = asyncio.run(
        email_service.send_email(
            to="owner@example.com",
            subject="Test",
            body="Hello",
            business_id="biz_none",
        )
    )
    assert isinstance(result, EmailResult)
    assert result.sent is False
    assert result.provider == "stub"


def test_send_email_with_tokens(monkeypatch):
    email_service._sent.clear()
    monkeypatch.setenv("EMAIL_PROVIDER", "gmail")
    config.get_settings.cache_clear()
    # Seed tokens for this tenant.
    oauth_store.save_tokens(
        "gmail",
        "biz1",
        access_token="access",
        refresh_token="refresh",
        expires_in=3600,
    )

    # Monkeypatch httpx.AsyncClient to avoid real network calls.
    import app.services.email_service as email_mod

    monkeypatch.setattr(
        email_mod,
        "httpx",
        types.SimpleNamespace(AsyncClient=lambda *a, **k: DummyClient()),
    )

    result = asyncio.run(
        email_service.send_email(
            to="owner@example.com",
            subject="Test Send",
            body="Hello world",
            business_id="biz1",
            from_email="owner@example.com",
        )
    )
    assert result.sent is True
    assert result.provider == "gmail"


def test_sendgrid_send_success(monkeypatch):
    email_service._sent.clear()
    monkeypatch.setenv("EMAIL_PROVIDER", "sendgrid")
    monkeypatch.setenv("SENDGRID_API_KEY", "sg_key")
    monkeypatch.setenv("EMAIL_FROM", "noreply@example.com")
    config.get_settings.cache_clear()

    import app.services.email_service as email_mod

    monkeypatch.setattr(
        email_mod,
        "httpx",
        types.SimpleNamespace(
            AsyncClient=lambda *a, **k: DummyClient([DummyResponse(status_code=202)])
        ),
    )

    result = asyncio.run(
        email_service.send_email(
            to="dest@example.com",
            subject="SendGrid Test",
            body="Hello SG",
            business_id=None,
        )
    )
    assert result.sent is True
    assert result.provider == "sendgrid"


def test_sendgrid_send_failure(monkeypatch):
    email_service._sent.clear()
    monkeypatch.setenv("EMAIL_PROVIDER", "sendgrid")
    monkeypatch.setenv("SENDGRID_API_KEY", "sg_key")
    config.get_settings.cache_clear()

    import app.services.email_service as email_mod

    monkeypatch.setattr(
        email_mod,
        "httpx",
        types.SimpleNamespace(
            AsyncClient=lambda *a, **k: DummyClient([DummyResponse(status_code=500)])
        ),
    )

    result = asyncio.run(
        email_service.send_email(
            to="dest@example.com",
            subject="SendGrid Fail",
            body="Hello SG",
            business_id=None,
        )
    )
    assert result.sent is False
    assert result.provider == "sendgrid"


def test_sendgrid_missing_key_returns_stub(monkeypatch):
    email_service._sent.clear()
    monkeypatch.setenv("EMAIL_PROVIDER", "sendgrid")
    monkeypatch.delenv("SENDGRID_API_KEY", raising=False)
    config.get_settings.cache_clear()

    result = asyncio.run(
        email_service.send_email(
            to="dest@example.com",
            subject="SendGrid Missing",
            body="Hello SG",
            business_id=None,
        )
    )
    assert result.sent is False
    assert result.provider == "stub"
