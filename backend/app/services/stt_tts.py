from __future__ import annotations

import base64
from typing import Optional

import httpx

from ..config import get_settings


class SpeechService:
    """Abstraction for STT/TTS integrations.

    By default this operates in stub mode and returns placeholder values. If
    configured with SPEECH_PROVIDER=openai and OPENAI_API_KEY, it will attempt
    to call OpenAI's audio speech endpoint for TTS.
    """

    def __init__(self) -> None:
        self._settings = get_settings().speech

    async def transcribe(self, audio: str | None) -> str:
        """Transcribe audio into text when a real STT provider is configured.

        The `audio` argument is expected to be a base64-encoded audio payload,
        or a placeholder token such as `audio://...` when no real audio is
        available.
        """
        if not audio or audio.startswith("audio://"):
            return ""

        # Only attempt real STT when configured for OpenAI.
        if self._settings.provider != "openai" or not self._settings.openai_api_key:
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
        files = {
            "file": ("audio.wav", audio_bytes, "audio/wav"),
            "model": (None, self._settings.openai_stt_model),
        }

        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.post(url, headers=headers, files=files)
                resp.raise_for_status()
                data = resp.json()
        except Exception:
            return ""

        text = data.get("text")
        return text or ""

    async def synthesize(self, text: str) -> str:
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
            "voice": self._settings.openai_tts_voice,
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


speech_service = SpeechService()
