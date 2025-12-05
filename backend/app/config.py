from __future__ import annotations

import os
from functools import lru_cache

from pydantic import BaseModel


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


class SmsSettings(BaseModel):
    provider: str = "stub"  # "stub" or "twilio"
    from_number: str | None = None
    owner_number: str | None = None
    twilio_account_sid: str | None = None
    twilio_auth_token: str | None = None
    verify_twilio_signatures: bool = False
    # Optional TwiML <Say> language codes for voice prompts.
    # When unset, Twilio's default language for the chosen voice is used.
    twilio_say_language_default: str | None = None
    twilio_say_language_es: str | None = "es-US"


class AppSettings(BaseModel):
    calendar: CalendarSettings = CalendarSettings()
    speech: SpeechSettings = SpeechSettings()
    sms: SmsSettings = SmsSettings()
    admin_api_key: str | None = None
    default_vertical: str = "plumbing"
    require_business_api_key: bool = False
    owner_dashboard_token: str | None = None
    session_store_backend: str = "memory"
    default_language_code: str = "en"

    @classmethod
    def from_env(cls) -> "AppSettings":
        """Load settings from environment variables with safe defaults."""
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
        )
        sms = SmsSettings(
            provider=os.getenv("SMS_PROVIDER", "stub"),
            from_number=os.getenv("SMS_FROM_NUMBER"),
            owner_number=os.getenv("SMS_OWNER_NUMBER"),
            twilio_account_sid=os.getenv("TWILIO_ACCOUNT_SID"),
            twilio_auth_token=os.getenv("TWILIO_AUTH_TOKEN"),
            verify_twilio_signatures=os.getenv("VERIFY_TWILIO_SIGNATURES", "false").lower()
            == "true",
            twilio_say_language_default=os.getenv("TWILIO_SAY_LANGUAGE_DEFAULT"),
            twilio_say_language_es=os.getenv("TWILIO_SAY_LANGUAGE_ES", "es-US"),
        )
        admin_api_key = os.getenv("ADMIN_API_KEY")
        default_vertical = os.getenv("DEFAULT_VERTICAL", "plumbing")
        default_language_code = os.getenv("DEFAULT_LANGUAGE_CODE", "en")
        require_business_api_key = (
            os.getenv("REQUIRE_BUSINESS_API_KEY", "false").lower() == "true"
        )
        owner_dashboard_token = os.getenv("OWNER_DASHBOARD_TOKEN") or os.getenv(
            "DASHBOARD_OWNER_TOKEN"
        )
        session_store_backend = os.getenv("SESSION_STORE_BACKEND", "memory")
        return cls(
            calendar=calendar,
            speech=speech,
            sms=sms,
            admin_api_key=admin_api_key,
            default_vertical=default_vertical,
            require_business_api_key=require_business_api_key,
            owner_dashboard_token=owner_dashboard_token,
            session_store_backend=session_store_backend,
            default_language_code=default_language_code,
        )


@lru_cache(maxsize=1)
def get_settings() -> AppSettings:
    """Return application settings loaded from the environment.

    The result is cached for the lifetime of the process so configuration
    is stable and we avoid repeatedly parsing environment variables.
    """
    return AppSettings.from_env()
