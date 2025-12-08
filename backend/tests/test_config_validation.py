import logging
import warnings

from app.config import AppSettings, QuickBooksSettings, get_settings


def _reset_settings_cache() -> None:
    try:
        get_settings.cache_clear()  # type: ignore[attr-defined]
    except Exception:
        pass


def test_config_validation_emits_warnings_for_misconfig(monkeypatch, caplog):
    caplog.set_level(logging.WARNING)
    # Force non-stub providers without required secrets.
    monkeypatch.setenv("SMS_PROVIDER", "twilio")
    monkeypatch.delenv("TWILIO_ACCOUNT_SID", raising=False)
    monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("STRIPE_USE_STUB", "false")
    monkeypatch.delenv("STRIPE_API_KEY", raising=False)
    monkeypatch.setenv("SPEECH_PROVIDER", "openai")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("QBO_CLIENT_ID", "id-only")
    monkeypatch.delenv("QBO_CLIENT_SECRET", raising=False)

    _reset_settings_cache()
    _ = get_settings()

    warning_details = [
        getattr(rec, "detail", rec.message)
        for rec in caplog.records
        if rec.levelno >= logging.WARNING
    ]
    assert any(
        "Twilio provider requires TWILIO_ACCOUNT_SID" in m for m in warning_details
    )
    assert any("STRIPE_API_KEY is required" in m for m in warning_details)
    assert any("OPENAI_API_KEY is required" in m for m in warning_details)
    assert any("QBO_CLIENT_SECRET is missing" in m for m in warning_details)


def test_config_validation_sanitized_log(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    # Use stub providers; should not emit warnings.
    monkeypatch.setenv("SMS_PROVIDER", "stub")
    monkeypatch.setenv("STRIPE_USE_STUB", "true")
    monkeypatch.setenv("SPEECH_PROVIDER", "stub")
    monkeypatch.delenv("QBO_CLIENT_ID", raising=False)
    monkeypatch.delenv("QBO_CLIENT_SECRET", raising=False)

    _reset_settings_cache()
    _ = get_settings()

    warning_msgs = [rec for rec in caplog.records if rec.levelno >= logging.WARNING]
    assert not warning_msgs, "No warnings expected for stub configuration"


def test_quickbooks_urls_switch_with_sandbox_flag() -> None:
    qb_sandbox = QuickBooksSettings()
    assert qb_sandbox.authorize_base.endswith("/sandbox.qbo.intuit.com/connect/oauth2")
    assert qb_sandbox.token_base.endswith("/oauth2/v1/tokens/bearer")

    qb_live = QuickBooksSettings(sandbox=False)
    assert qb_live.authorize_base.endswith("/appcenter.intuit.com/connect/oauth2")
    assert qb_live.token_base.endswith("/oauth2/v1/tokens/bearer")


def test_from_env_handles_invalid_business_hours(monkeypatch) -> None:
    monkeypatch.setenv("BUSINESS_OPEN_HOUR", "not-a-number")
    monkeypatch.setenv("BUSINESS_CLOSE_HOUR", "also-bad")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        settings = AppSettings.from_env()
    assert settings.calendar.default_open_hour == 8
    assert settings.calendar.default_close_hour == 17
