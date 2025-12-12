from __future__ import annotations

import os
from functools import lru_cache
import logging

from pydantic import BaseModel


class AuthSettings(BaseModel):
    secret: str = "dev-auth-secret"
    algorithm: str = "HS256"
    access_token_expires_minutes: int = 60
    refresh_token_expires_minutes: int = 60 * 24 * 7
    failed_attempt_limit: int = 5
    lockout_minutes: int = 15
    reset_token_expires_minutes: int = 30


class CalendarSettings(BaseModel):
    calendar_id: str = "primary"
    credentials_file: str | None = None
    use_stub: bool = True
    # Default business hours (UTC) used when tenant-specific
    # configuration is not available.
    default_open_hour: int = 8
    default_close_hour: int = 17
    default_closed_days: str = ""


class SpeechSettings(BaseModel):
    provider: str = "stub"  # "stub" or "openai"
    openai_api_key: str | None = None
    openai_api_base: str = "https://api.openai.com/v1"
    openai_tts_model: str = "gpt-4o-mini"
    openai_tts_voice: str = "alloy"
    openai_stt_model: str = "gpt-4o-mini-transcribe"
    openai_chat_model: str = "gpt-4o-mini"


class NluSettings(BaseModel):
    intent_provider: str = os.getenv("NLU_PROVIDER", "heuristic")
    intent_confidence_threshold: float = float(
        os.getenv("NLU_INTENT_THRESHOLD") or "0.4"
    )


class OAuthSettings(BaseModel):
    redirect_base: str = os.getenv("OAUTH_REDIRECT_BASE", "http://localhost:8000/auth")
    state_secret: str = os.getenv("AUTH_STATE_SECRET", "dev-secret")
    linkedin_client_id: str | None = os.getenv("LINKEDIN_CLIENT_ID")
    linkedin_client_secret: str | None = os.getenv("LINKEDIN_CLIENT_SECRET")
    linkedin_scopes: str = os.getenv(
        "LINKEDIN_SCOPES", "r_liteprofile r_emailaddress w_member_social"
    )
    google_client_id: str | None = os.getenv("GOOGLE_CLIENT_ID")
    google_client_secret: str | None = os.getenv("GOOGLE_CLIENT_SECRET")
    gmail_scopes: str = os.getenv(
        "GMAIL_SCOPES",
        "openid email profile https://www.googleapis.com/auth/gmail.send",
    )
    gcalendar_scopes: str = os.getenv(
        "GCALENDAR_SCOPES",
        "openid email profile https://www.googleapis.com/auth/calendar.events.readonly",
    )


class EmailSettings(BaseModel):
    provider: str = os.getenv("EMAIL_PROVIDER", "stub")  # "stub", "gmail", "sendgrid"
    from_email: str | None = os.getenv("EMAIL_FROM")
    sendgrid_api_key: str | None = os.getenv("SENDGRID_API_KEY")


class SmsSettings(BaseModel):
    provider: str = "stub"  # "stub" or "twilio"
    from_number: str | None = None
    owner_number: str | None = None
    twilio_account_sid: str | None = None
    twilio_auth_token: str | None = None
    verify_twilio_signatures: bool = False
    replay_protection_seconds: int = 300
    enable_voicemail: bool = True
    # Optional TwiML <Say> language codes for voice prompts.
    # When unset, Twilio's default language for the chosen voice is used.
    twilio_say_language_default: str | None = None
    twilio_say_language_es: str | None = "es-US"


class TelephonySettings(BaseModel):
    twilio_streaming_enabled: bool = (
        os.getenv("TWILIO_STREAMING_ENABLED", "false").lower() == "true"
    )
    twilio_stream_base_url: str | None = os.getenv(
        "TWILIO_STREAM_BASE_URL",
        "wss://ai-telephony-backend-tcmgy2pf2a-uc.a.run.app/v1/twilio/voice-stream",
    )


class QuickBooksSettings(BaseModel):
    client_id: str | None = None
    client_secret: str | None = None
    redirect_uri: str | None = None
    scopes: str = "com.intuit.quickbooks.accounting openid profile email phone address"
    sandbox: bool = True

    @property
    def authorize_base(self) -> str:
        return (
            "https://appcenter.intuit.com/connect/oauth2"
            if not self.sandbox
            else "https://sandbox.qbo.intuit.com/connect/oauth2"
        )

    @property
    def token_base(self) -> str:
        return (
            "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"
            if not self.sandbox
            else "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"
        )


class StripeSettings(BaseModel):
    api_key: str | None = None
    publishable_key: str | None = None
    webhook_secret: str | None = None
    price_basic: str | None = None
    price_growth: str | None = None
    price_scale: str | None = None
    payment_link_url: str | None = None
    billing_portal_url: str | None = None
    billing_portal_return_url: str | None = None
    checkout_success_url: str = (
        "https://example.com/billing/success?session_id={CHECKOUT_SESSION_ID}"
    )
    checkout_cancel_url: str = "https://example.com/billing/canceled"
    use_stub: bool = False
    verify_signatures: bool = True
    replay_protection_seconds: int = 300


class AppSettings(BaseModel):
    auth: AuthSettings = AuthSettings()
    calendar: CalendarSettings = CalendarSettings()
    speech: SpeechSettings = SpeechSettings()
    nlu: NluSettings = NluSettings()
    oauth: OAuthSettings = OAuthSettings()
    email: EmailSettings = EmailSettings()
    sms: SmsSettings = SmsSettings()
    telephony: TelephonySettings = TelephonySettings()
    quickbooks: QuickBooksSettings = QuickBooksSettings()
    stripe: StripeSettings = StripeSettings()
    admin_api_key: str | None = None
    default_vertical: str = "plumbing"
    require_business_api_key: bool = False
    owner_dashboard_token: str | None = None
    session_store_backend: str = "memory"
    default_language_code: str = "en"
    enforce_subscription: bool = False
    subscription_grace_days: int = 5
    subscription_reminder_hours: int = 12
    rate_limit_per_minute: int = 120
    rate_limit_burst: int = 20
    rate_limit_whitelist_ips: list[str] = []
    retention_purge_interval_hours: int = 24
    capture_transcripts: bool = True
    security_headers_enabled: bool = True
    security_csp: str = (
        "default-src 'self'; "
        "img-src 'self' data:; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        "font-src 'self'; "
        "connect-src 'self'; "
        "frame-ancestors 'none'"
    )
    security_hsts_enabled: bool = True
    security_hsts_max_age: int = 31536000  # 1 year

    @classmethod
    def from_env(cls) -> "AppSettings":
        """Load settings from environment variables with safe defaults."""
        auth = AuthSettings(
            secret=os.getenv("AUTH_SECRET", "dev-auth-secret"),
            algorithm=os.getenv("AUTH_ALGORITHM", "HS256"),
            access_token_expires_minutes=int(
                os.getenv("AUTH_ACCESS_TOKEN_EXPIRES_MINUTES", "60")
            ),
            refresh_token_expires_minutes=int(
                os.getenv("AUTH_REFRESH_TOKEN_EXPIRES_MINUTES", str(60 * 24 * 7))
            ),
            failed_attempt_limit=int(os.getenv("AUTH_FAILED_ATTEMPT_LIMIT", "5")),
            lockout_minutes=int(os.getenv("AUTH_LOCKOUT_MINUTES", "15")),
            reset_token_expires_minutes=int(
                os.getenv("AUTH_RESET_TOKEN_EXPIRES_MINUTES", "30")
            ),
        )
        # Calendar and business-hours defaults.
        raw_open = os.getenv("BUSINESS_OPEN_HOUR", "8")
        raw_close = os.getenv("BUSINESS_CLOSE_HOUR", "17")
        try:
            default_open_hour = int(raw_open)
        except ValueError:
            default_open_hour = 8
        try:
            default_close_hour = int(raw_close)
        except ValueError:
            default_close_hour = 17
        default_closed_days = os.getenv("BUSINESS_CLOSED_DAYS", "")

        calendar = CalendarSettings(
            calendar_id=os.getenv("GOOGLE_CALENDAR_ID", "primary"),
            credentials_file=os.getenv("GOOGLE_CALENDAR_CREDENTIALS_FILE"),
            use_stub=os.getenv("CALENDAR_USE_STUB", "true").lower() != "false",
            default_open_hour=default_open_hour,
            default_close_hour=default_close_hour,
            default_closed_days=default_closed_days,
        )
        speech = SpeechSettings(
            provider=os.getenv("SPEECH_PROVIDER", "stub"),
            openai_api_key=os.getenv("OPENAI_API_KEY"),
            openai_api_base=os.getenv("OPENAI_API_BASE", "https://api.openai.com/v1"),
            openai_tts_model=os.getenv("OPENAI_TTS_MODEL", "gpt-4o-mini"),
            openai_tts_voice=os.getenv("OPENAI_TTS_VOICE", "alloy"),
            openai_stt_model=os.getenv("OPENAI_STT_MODEL", "gpt-4o-mini-transcribe"),
            openai_chat_model=os.getenv("OPENAI_CHAT_MODEL", "gpt-4o-mini"),
        )
        nlu = NluSettings(
            intent_provider=os.getenv("NLU_PROVIDER", "heuristic"),
            intent_confidence_threshold=float(
                os.getenv("NLU_INTENT_THRESHOLD") or "0.35"
            ),
        )
        oauth = OAuthSettings(
            redirect_base=os.getenv(
                "OAUTH_REDIRECT_BASE", "http://localhost:8000/auth"
            ),
            state_secret=os.getenv("AUTH_STATE_SECRET", "dev-secret"),
            linkedin_client_id=os.getenv("LINKEDIN_CLIENT_ID"),
            linkedin_client_secret=os.getenv("LINKEDIN_CLIENT_SECRET"),
            linkedin_scopes=os.getenv(
                "LINKEDIN_SCOPES", "r_liteprofile r_emailaddress w_member_social"
            ),
            google_client_id=os.getenv("GOOGLE_CLIENT_ID"),
            google_client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
            gmail_scopes=os.getenv(
                "GMAIL_SCOPES",
                "openid email profile https://www.googleapis.com/auth/gmail.send",
            ),
            gcalendar_scopes=os.getenv(
                "GCALENDAR_SCOPES",
                "openid email profile https://www.googleapis.com/auth/calendar.events.readonly",
            ),
        )
        email = EmailSettings(
            provider=os.getenv("EMAIL_PROVIDER", "stub"),
            from_email=os.getenv("EMAIL_FROM"),
            sendgrid_api_key=os.getenv("SENDGRID_API_KEY"),
        )
        sms = SmsSettings(
            provider=os.getenv("SMS_PROVIDER", "stub"),
            from_number=os.getenv("SMS_FROM_NUMBER"),
            owner_number=os.getenv("SMS_OWNER_NUMBER"),
            twilio_account_sid=os.getenv("TWILIO_ACCOUNT_SID"),
            twilio_auth_token=os.getenv("TWILIO_AUTH_TOKEN"),
            verify_twilio_signatures=os.getenv(
                "VERIFY_TWILIO_SIGNATURES", "false"
            ).lower()
            == "true",
            replay_protection_seconds=int(
                os.getenv("TWILIO_REPLAY_PROTECTION_SECONDS", "300")
            ),
            enable_voicemail=os.getenv("TWILIO_ENABLE_VOICEMAIL", "true").lower()
            == "true",
            twilio_say_language_default=os.getenv("TWILIO_SAY_LANGUAGE_DEFAULT"),
            twilio_say_language_es=os.getenv("TWILIO_SAY_LANGUAGE_ES", "es-US"),
        )
        telephony = TelephonySettings(
            twilio_streaming_enabled=os.getenv(
                "TWILIO_STREAMING_ENABLED", "false"
            ).lower()
            == "true",
            twilio_stream_base_url=os.getenv("TWILIO_STREAM_BASE_URL"),
        )
        quickbooks = QuickBooksSettings(
            client_id=os.getenv("QBO_CLIENT_ID"),
            client_secret=os.getenv("QBO_CLIENT_SECRET"),
            redirect_uri=os.getenv("QBO_REDIRECT_URI"),
            scopes=os.getenv(
                "QBO_SCOPES",
                "com.intuit.quickbooks.accounting openid profile email phone address",
            ),
            sandbox=os.getenv("QBO_SANDBOX", "true").lower() != "false",
        )
        # Default to live Stripe in non-test environments; allow stubbing during tests/dev
        # unless explicitly overridden via STRIPE_USE_STUB.
        stripe_use_stub_default = (
            "true"
            if (
                os.getenv("PYTEST_CURRENT_TEST")
                or os.getenv("TESTING", "false").lower() == "true"
            )
            else "false"
        )
        stripe = StripeSettings(
            api_key=os.getenv("STRIPE_API_KEY"),
            publishable_key=os.getenv("STRIPE_PUBLISHABLE_KEY"),
            webhook_secret=os.getenv("STRIPE_WEBHOOK_SECRET"),
            price_basic=os.getenv("STRIPE_PRICE_BASIC"),
            price_growth=os.getenv("STRIPE_PRICE_GROWTH"),
            price_scale=os.getenv("STRIPE_PRICE_SCALE"),
            payment_link_url=os.getenv("STRIPE_PAYMENT_LINK_URL"),
            billing_portal_url=os.getenv("STRIPE_BILLING_PORTAL_URL"),
            billing_portal_return_url=os.getenv("STRIPE_BILLING_PORTAL_RETURN_URL"),
            checkout_success_url=os.getenv(
                "STRIPE_CHECKOUT_SUCCESS_URL",
                "https://example.com/billing/success?session_id={CHECKOUT_SESSION_ID}",
            ),
            checkout_cancel_url=os.getenv(
                "STRIPE_CHECKOUT_CANCEL_URL",
                "https://example.com/billing/canceled",
            ),
            use_stub=os.getenv("STRIPE_USE_STUB", stripe_use_stub_default).lower()
            == "true",
            verify_signatures=os.getenv("STRIPE_VERIFY_SIGNATURES", "true").lower()
            == "true",
            replay_protection_seconds=int(
                os.getenv("STRIPE_REPLAY_PROTECTION_SECONDS", "300")
            ),
        )
        admin_api_key = os.getenv("ADMIN_API_KEY")
        default_vertical = os.getenv("DEFAULT_VERTICAL", "plumbing")
        default_language_code = os.getenv("DEFAULT_LANGUAGE_CODE", "en")
        require_business_api_key = (
            os.getenv("REQUIRE_BUSINESS_API_KEY", "false").lower() == "true"
        )
        enforce_subscription = (
            os.getenv("ENFORCE_SUBSCRIPTION", "false").lower() == "true"
        )
        subscription_grace_days = int(os.getenv("SUBSCRIPTION_GRACE_DAYS", "5"))
        subscription_reminder_hours = int(
            os.getenv("SUBSCRIPTION_REMINDER_HOURS", "12")
        )
        # OWNER_DASHBOARD_TOKEN is the canonical env var; DASHBOARD_OWNER_TOKEN
        # is accepted as a legacy alias for backward compatibility.
        owner_dashboard_token = os.getenv("OWNER_DASHBOARD_TOKEN") or os.getenv(
            "DASHBOARD_OWNER_TOKEN"
        )
        session_store_backend = os.getenv("SESSION_STORE_BACKEND", "memory")
        rate_limit_per_minute = int(os.getenv("RATE_LIMIT_PER_MINUTE", "120"))
        rate_limit_burst = int(os.getenv("RATE_LIMIT_BURST", "20"))
        rate_limit_whitelist_ips = [
            ip.strip()
            for ip in (os.getenv("RATE_LIMIT_WHITELIST_IPS", "") or "").split(",")
            if ip.strip()
        ]
        retention_purge_interval_hours = int(
            os.getenv("RETENTION_PURGE_INTERVAL_HOURS", "24")
        )
        capture_transcripts = (
            os.getenv("CAPTURE_TRANSCRIPTS", "true").lower() != "false"
        )
        security_headers_enabled = (
            os.getenv("SECURITY_HEADERS_ENABLED", "true").lower() == "true"
        )
        security_csp = os.getenv(
            "SECURITY_CSP",
            "default-src 'self'; "
            "img-src 'self' data:; "
            "script-src 'self'; "
            "style-src 'self' 'unsafe-inline'; "
            "font-src 'self'; "
            "connect-src 'self'; "
            "frame-ancestors 'none'",
        )
        security_hsts_enabled = (
            os.getenv("SECURITY_HSTS_ENABLED", "true").lower() == "true"
        )
        security_hsts_max_age = int(os.getenv("SECURITY_HSTS_MAX_AGE", "31536000"))
        return cls(
            auth=auth,
            calendar=calendar,
            speech=speech,
            nlu=nlu,
            oauth=oauth,
            email=email,
            sms=sms,
            telephony=telephony,
            quickbooks=quickbooks,
            stripe=stripe,
            admin_api_key=admin_api_key,
            default_vertical=default_vertical,
            require_business_api_key=require_business_api_key,
            owner_dashboard_token=owner_dashboard_token,
            session_store_backend=session_store_backend,
            default_language_code=default_language_code,
            enforce_subscription=enforce_subscription,
            subscription_grace_days=subscription_grace_days,
            subscription_reminder_hours=subscription_reminder_hours,
            rate_limit_per_minute=rate_limit_per_minute,
            rate_limit_burst=rate_limit_burst,
            rate_limit_whitelist_ips=rate_limit_whitelist_ips,
            retention_purge_interval_hours=retention_purge_interval_hours,
            capture_transcripts=capture_transcripts,
            security_headers_enabled=security_headers_enabled,
            security_csp=security_csp,
            security_hsts_enabled=security_hsts_enabled,
            security_hsts_max_age=security_hsts_max_age,
        )

    def validate_combinations(self) -> None:
        """Warn when non-stub providers are misconfigured to avoid runtime surprises."""
        logger = logging.getLogger(__name__)
        warnings: list[str] = []

        if self.sms.provider == "twilio":
            if not (self.sms.twilio_account_sid and self.sms.twilio_auth_token):
                warnings.append(
                    "Twilio provider requires TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN."
                )
        if not self.stripe.use_stub and not self.stripe.api_key:
            warnings.append("STRIPE_API_KEY is required when STRIPE_USE_STUB=false.")
        if self.speech.provider == "openai" and not self.speech.openai_api_key:
            warnings.append("OPENAI_API_KEY is required when SPEECH_PROVIDER=openai.")
        if self.quickbooks.client_id and not self.quickbooks.client_secret:
            warnings.append("QBO_CLIENT_SECRET is missing while QBO_CLIENT_ID is set.")
        if getattr(self, "email", None):
            if self.email.provider == "sendgrid" and not self.email.sendgrid_api_key:
                warnings.append(
                    "SENDGRID_API_KEY is required when EMAIL_PROVIDER=sendgrid."
                )
        if warnings:
            for msg in warnings:
                logger.warning("configuration_warning", extra={"detail": msg})


@lru_cache(maxsize=1)
def get_settings() -> AppSettings:
    """Return application settings loaded from the environment.

    The result is cached for the lifetime of the process so configuration
    is stable and we avoid repeatedly parsing environment variables.
    """
    settings = AppSettings.from_env()
    settings.validate_combinations()
    return settings
