from __future__ import annotations

import base64
from abc import ABC, abstractmethod
import logging
import time
from typing import Any

import anyio
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

        timeout = httpx.Timeout(12.0, connect=6.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, headers=headers, data=data, files=files)
            resp.raise_for_status()
            data = resp.json()

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
        timeout = httpx.Timeout(12.0, connect=6.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            audio_bytes = resp.content

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


class GoogleCloudSpeechProvider(SpeechProvider):
    """Google Cloud Speech-to-Text + Text-to-Speech via REST APIs.

    This uses Application Default Credentials (ADC). In Cloud Run / GCE, ADC
    is provided automatically; in local development set GOOGLE_APPLICATION_CREDENTIALS.
    """

    name = "gcp"

    _SCOPE = "https://www.googleapis.com/auth/cloud-platform"

    def __init__(self, settings: SpeechSettings) -> None:
        self._settings = settings
        self._credentials = None
        self._token_lock = anyio.Lock()

    def _ensure_credentials(self) -> None:
        if self._credentials is not None:
            return
        try:
            import google.auth
        except Exception as exc:  # pragma: no cover - optional dependency
            raise RuntimeError(
                "google-auth is required for GCP speech provider"
            ) from exc
        creds, _project = google.auth.default(scopes=[self._SCOPE])
        self._credentials = creds

    async def _access_token(self) -> str:
        self._ensure_credentials()
        creds = self._credentials
        if creds is None:  # pragma: no cover - defensive
            raise RuntimeError("GCP credentials unavailable")

        expiry = getattr(creds, "expiry", None)
        token = getattr(creds, "token", None)
        now = time.time()
        # Refresh when token is missing or expiring soon (best effort).
        exp_ts = None
        if expiry is not None:
            try:
                exp_ts = expiry.timestamp()
            except Exception:
                exp_ts = None
        if token and exp_ts and exp_ts - now > 60:
            return str(token)

        async with self._token_lock:
            expiry = getattr(creds, "expiry", None)
            token = getattr(creds, "token", None)
            exp_ts = None
            if expiry is not None:
                try:
                    exp_ts = expiry.timestamp()
                except Exception:
                    exp_ts = None
            if token and exp_ts and exp_ts - time.time() > 60:
                return str(token)

            def _refresh_sync() -> str:
                try:
                    from google.auth.transport.requests import Request as AuthRequest
                except Exception:
                    try:
                        from google.auth.transport.urllib3 import Request as AuthRequest
                    except Exception as exc:
                        raise RuntimeError(
                            "google-auth transport is required for GCP speech provider"
                        ) from exc
                creds.refresh(AuthRequest())
                new_token = getattr(creds, "token", None)
                if not new_token:
                    raise RuntimeError("Unable to refresh GCP access token")
                return str(new_token)

            return await anyio.to_thread.run_sync(_refresh_sync)

    def _stt_language_code(self) -> str:
        return (self._settings.gcp_language_code or "en-US").strip() or "en-US"

    def _tts_language_code(self) -> str:
        return (self._settings.gcp_language_code or "en-US").strip() or "en-US"

    def _parse_wav_sample_rate(self, audio_bytes: bytes) -> int | None:
        if len(audio_bytes) < 28:
            return None
        if not (audio_bytes[0:4] == b"RIFF" and audio_bytes[8:12] == b"WAVE"):
            return None
        # Sample rate is at byte offset 24 (little-endian uint32) for PCM WAV.
        try:
            return int.from_bytes(audio_bytes[24:28], "little")
        except Exception:
            return None

    def _detect_audio_encoding(self, audio_bytes: bytes) -> tuple[str, int | None]:
        """Best-effort audio encoding detection for Google STT.

        We primarily expect WAV/LINEAR16 from callers, but the validation harness
        and some clients may provide MP3/FLAC/OGG audio.
        """
        wav_rate = self._parse_wav_sample_rate(audio_bytes)
        if wav_rate:
            return ("LINEAR16", wav_rate)

        if (
            len(audio_bytes) >= 12
            and audio_bytes[0:4] == b"RIFF"
            and audio_bytes[8:12] == b"WAVE"
        ):
            # WAV header present but sample rate couldn't be parsed; fall back.
            return ("LINEAR16", 8000)

        if len(audio_bytes) >= 4 and audio_bytes[0:4] == b"fLaC":
            return ("FLAC", None)

        is_mp3 = False
        if len(audio_bytes) >= 3 and audio_bytes[0:3] == b"ID3":
            is_mp3 = True
        elif (
            len(audio_bytes) >= 2
            and audio_bytes[0] == 0xFF
            and (audio_bytes[1] & 0xE0) == 0xE0
        ):
            # Frame sync bits set (common for MP3 without ID3 tags).
            is_mp3 = True
        if is_mp3:
            return ("MP3", None)

        if len(audio_bytes) >= 4 and audio_bytes[0:4] == b"OggS":
            # Best-effort: treat OGG as Opus; sample rate is encoded in the stream.
            return ("OGG_OPUS", None)

        return ("LINEAR16", 8000)

    async def transcribe(self, audio: str | None) -> str:
        if not audio or audio.startswith("audio://"):
            return ""
        try:
            audio_bytes = base64.b64decode(audio, validate=True)
        except Exception:
            return ""

        token = await self._access_token()
        url = "https://speech.googleapis.com/v1/speech:recognize"
        encoding, sample_rate = self._detect_audio_encoding(audio_bytes)
        config: dict[str, Any] = {
            "encoding": encoding,
            "languageCode": self._stt_language_code(),
            "enableAutomaticPunctuation": True,
            "model": (self._settings.gcp_stt_model or "default"),
        }
        if sample_rate:
            config["sampleRateHertz"] = int(sample_rate)
        payload = {
            "config": config,
            "audio": {"content": audio},
        }
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        timeout = httpx.Timeout(self._settings.gcp_timeout_seconds, connect=6.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()

        transcripts: list[str] = []
        for result in data.get("results") or []:
            alternatives = result.get("alternatives") or []
            if not alternatives:
                continue
            top = alternatives[0] or {}
            text = (top.get("transcript") or "").strip()
            if text:
                transcripts.append(text)
        return " ".join(transcripts).strip()

    async def synthesize(self, text: str, voice: str | None = None) -> str:
        cleaned = (text or "").strip()
        if not cleaned:
            return "audio://placeholder"

        token = await self._access_token()
        url = "https://texttospeech.googleapis.com/v1/text:synthesize"
        language_code = self._tts_language_code()
        voice_name = (voice or self._settings.gcp_tts_voice or "").strip() or None
        voice_obj: dict[str, Any] = {"languageCode": language_code}
        if voice_name:
            voice_obj["name"] = voice_name
        payload = {
            "input": {"text": cleaned},
            "voice": voice_obj,
            "audioConfig": {"audioEncoding": self._settings.gcp_tts_audio_encoding},
        }
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        timeout = httpx.Timeout(self._settings.gcp_timeout_seconds, connect=6.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
        audio_content = data.get("audioContent")
        if not audio_content:
            raise RuntimeError("GCP TTS returned empty audioContent")
        return str(audio_content)

    async def healthcheck(self) -> dict[str, Any]:
        try:
            token = await self._access_token()
        except Exception as exc:
            return {
                "healthy": False,
                "provider": self.name,
                "reason": "credentials_unavailable",
                "detail": str(exc),
            }
        url = "https://texttospeech.googleapis.com/v1/voices"
        headers = {"Authorization": f"Bearer {token}"}
        try:
            timeout = httpx.Timeout(4.0, connect=2.0)
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.get(
                    url,
                    headers=headers,
                    params={"languageCode": self._tts_language_code()},
                )
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
        if self._settings.provider == "gcp":
            return GoogleCloudSpeechProvider(self._settings)
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
                    result = await fallback.transcribe(audio)
                    self._trip_circuit()
                    return result
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
                    result = await fallback.synthesize(text, voice=voice)
                    self._trip_circuit()
                    return result
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
