from __future__ import annotations

import base64
from abc import ABC, abstractmethod
import logging
import time
from typing import Any

import httpx

from ..config import SpeechSettings, get_settings

logger = logging.getLogger(__name__)


class SpeechProvider(ABC):
    """Provider interface for speech-to-text and text-to-speech engines."""

    name: str = "base"

    @abstractmethod
    async def transcribe(self, audio: str | None) -> str: ...

    @abstractmethod
    async def synthesize(self, text: str, voice: str | None = None) -> str: ...


class StubSpeechProvider(SpeechProvider):
    """No-op provider used for local dev and tests."""

    name = "stub"

    async def transcribe(self, audio: str | None) -> str:
        if not audio or audio.startswith("audio://"):
            return ""
        return ""

    async def synthesize(self, text: str, voice: str | None = None) -> str:
        return "audio://placeholder"


class OpenAISpeechProvider(SpeechProvider):
    """OpenAI-backed STT/TTS implementation."""

    name = "openai"

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
            timeout = httpx.Timeout(12.0, connect=6.0)
            async with httpx.AsyncClient(timeout=timeout) as client:
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
            timeout = httpx.Timeout(12.0, connect=6.0)
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(url, headers=headers, json=payload)
                resp.raise_for_status()
                audio_bytes = resp.content
        except Exception:
            # Fall back to placeholder if the external call fails.
            return "audio://placeholder"

        # Return base64-encoded audio so callers can decode/playback or pass it
        # on to another system (e.g. telephony or web client).
        return base64.b64encode(audio_bytes).decode("ascii")

    async def healthcheck(self) -> dict[str, Any]:
        """Lightweight provider health check."""
        if not self._settings.openai_api_key:
            return {
                "healthy": False,
                "provider": self.name,
                "reason": "missing_api_key",
            }
        url = f"{self._settings.openai_api_base}/models"
        headers = {"Authorization": f"Bearer {self._settings.openai_api_key}"}
        try:
            timeout = httpx.Timeout(4.0, connect=2.0)
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.get(url, headers=headers, params={"limit": 1})
                resp.raise_for_status()
            return {"healthy": True, "provider": self.name}
        except Exception as exc:  # pragma: no cover - network dependent
            return {
                "healthy": False,
                "provider": self.name,
                "reason": "unreachable",
                "detail": str(exc),
            }


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
        self._circuit_open_until: float | None = None
        self._last_error: str | None = None
        self._last_provider: str | None = None
        self._last_used_fallback: bool = False

    def _circuit_open(self) -> bool:
        if self._circuit_open_until is None:
            return False
        return time.time() < self._circuit_open_until

    def _trip_circuit(self, cooldown_seconds: int = 60) -> None:
        self._circuit_open_until = time.time() + cooldown_seconds
        from ..metrics import metrics

        metrics.speech_circuit_trips += 1

    def _select_provider(self) -> SpeechProvider:
        if self._provider_override is not None:
            return self._provider_override
        if self._settings.provider == "openai" and self._settings.openai_api_key:
            return OpenAISpeechProvider(self._settings)
        return StubSpeechProvider()

    def _fallback_provider(self) -> SpeechProvider:
        # For now, the fallback is always stub to preserve deterministic flows.
        return StubSpeechProvider()

    def _record_error(
        self, provider_name: str, action: str, exc: Exception | None
    ) -> None:
        self._last_error = (
            f"{provider_name}:{action}:{exc}" if exc else f"{provider_name}:{action}"
        )
        self._last_provider = provider_name
        self._last_used_fallback = False

    async def transcribe(self, audio: str | None) -> str:
        """Transcribe audio into text via the configured provider."""
        if self._circuit_open():
            return ""

        provider = self._select_provider()
        self._last_provider = provider.name
        try:
            return await provider.transcribe(audio)
        except Exception as exc:
            self._record_error(provider.name, "transcribe", exc)
            if not isinstance(provider, StubSpeechProvider):
                fallback = self._fallback_provider()
                self._last_used_fallback = True
                try:
                    return await fallback.transcribe(audio)
                except Exception:
                    logger.warning(
                        "speech_fallback_transcribe_failed",
                        exc_info=True,
                        extra={"provider": provider.name, "fallback": fallback.name},
                    )
            self._trip_circuit()
            return ""

    async def synthesize(self, text: str, voice: str | None = None) -> str:
        """Convert text to speech via the configured provider."""
        if self._circuit_open():
            return "audio://placeholder"

        provider = self._select_provider()
        self._last_provider = provider.name
        try:
            return await provider.synthesize(text, voice=voice)
        except Exception as exc:
            self._record_error(provider.name, "synthesize", exc)
            if not isinstance(provider, StubSpeechProvider):
                fallback = self._fallback_provider()
                self._last_used_fallback = True
                try:
                    return await fallback.synthesize(text, voice=voice)
                except Exception:
                    logger.warning(
                        "speech_fallback_synthesize_failed",
                        exc_info=True,
                        extra={"provider": provider.name, "fallback": fallback.name},
                    )
            self._trip_circuit()
            return "audio://placeholder"

    async def health(self) -> dict[str, Any]:
        """Return a lightweight provider health snapshot."""
        provider = self._select_provider()
        if hasattr(provider, "healthcheck"):
            try:
                result = await provider.healthcheck()
                result.setdefault("provider", provider.name)
                return result
            except Exception:
                return {
                    "healthy": False,
                    "provider": provider.name,
                    "reason": "healthcheck_error",
                }
        return {"healthy": True, "provider": provider.name}

    def diagnostics(self) -> dict[str, Any]:
        """Expose recent provider usage and fallback state for dashboards/tests."""
        return {
            "last_provider": self._last_provider,
            "last_error": self._last_error,
            "used_fallback": self._last_used_fallback,
            "circuit_open": self._circuit_open(),
        }

    def override_provider(self, provider: SpeechProvider | None) -> None:
        """Swap in a provider (or None to revert to settings-based selection)."""
        self._provider_override = provider


speech_service = SpeechService()
