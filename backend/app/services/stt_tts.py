from __future__ import annotations

import base64
from abc import ABC, abstractmethod

import httpx

from ..config import SpeechSettings, get_settings


class SpeechProvider(ABC):
    """Provider interface for speech-to-text and text-to-speech engines."""

    @abstractmethod
    async def transcribe(self, audio: str | None) -> str: ...

    @abstractmethod
    async def synthesize(self, text: str, voice: str | None = None) -> str: ...


class StubSpeechProvider(SpeechProvider):
    """No-op provider used for local dev and tests."""

    async def transcribe(self, audio: str | None) -> str:
        if not audio or audio.startswith("audio://"):
            return ""
        return ""

    async def synthesize(self, text: str, voice: str | None = None) -> str:
        return "audio://placeholder"


class OpenAISpeechProvider(SpeechProvider):
    """OpenAI-backed STT/TTS implementation."""

    def __init__(self, settings: SpeechSettings) -> None:
        self._settings = settings

    async def transcribe(self, audio: str | None) -> str:
        if not audio or audio.startswith("audio://"):
            return ""

        try:
            audio_bytes = base64.b64decode(audio, validate=True)
        except Exception:
            # If decoding fails, fall back silently.
            return ""

        url = f"{self._settings.openai_api_base}/audio/transcriptions"
        headers = {
            "Authorization": f"Bearer {self._settings.openai_api_key}",
        }
        data = {"model": self._settings.openai_stt_model}
        files = {"file": ("audio.wav", audio_bytes, "audio/wav")}

        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.post(url, headers=headers, data=data, files=files)
                resp.raise_for_status()
                data = resp.json()
        except Exception:
            return ""

        text = data.get("text")
        return text or ""

    async def synthesize(self, text: str, voice: str | None = None) -> str:
        # Stub behaviour.
        if self._settings.provider != "openai" or not self._settings.openai_api_key:
            return "audio://placeholder"

        # OpenAI TTS integration using HTTPX.
        url = f"{self._settings.openai_api_base}/audio/speech"
        headers = {
            "Authorization": f"Bearer {self._settings.openai_api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self._settings.openai_tts_model,
            "voice": voice or self._settings.openai_tts_voice,
            "input": text,
        }
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.post(url, headers=headers, json=payload)
                resp.raise_for_status()
                audio_bytes = resp.content
        except Exception:
            # Fall back to placeholder if the external call fails.
            return "audio://placeholder"

        # Return base64-encoded audio so callers can decode/playback or pass it
        # on to another system (e.g. telephony or web client).
        return base64.b64encode(audio_bytes).decode("ascii")


class SpeechService:
    """Abstraction for STT/TTS integrations with pluggable providers.

    Defaults to stub mode. When configured with SPEECH_PROVIDER=openai and
    OPENAI_API_KEY, it will delegate to an OpenAI-backed provider. A custom
    provider may be injected (useful for tests).
    """

    def __init__(
        self,
        settings: SpeechSettings | None = None,
        provider: SpeechProvider | None = None,
    ) -> None:
        self._settings = settings or get_settings().speech
        self._provider_override = provider

    def _select_provider(self) -> SpeechProvider:
        if self._provider_override is not None:
            return self._provider_override
        if (
            self._settings.provider == "openai"
            and self._settings.openai_api_key
        ):
            return OpenAISpeechProvider(self._settings)
        return StubSpeechProvider()

    async def transcribe(self, audio: str | None) -> str:
        """Transcribe audio into text via the configured provider."""
        provider = self._select_provider()
        return await provider.transcribe(audio)

    async def synthesize(self, text: str, voice: str | None = None) -> str:
        """Convert text to speech via the configured provider."""
        provider = self._select_provider()
        return await provider.synthesize(text, voice=voice)

    def override_provider(self, provider: SpeechProvider | None) -> None:
        """Swap in a provider (or None to revert to settings-based selection)."""
        self._provider_override = provider


speech_service = SpeechService()
