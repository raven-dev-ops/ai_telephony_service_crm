import base64

import pytest

from app.config import SpeechSettings
from app.services.stt_tts import SpeechService, StubSpeechProvider


@pytest.mark.anyio
async def test_transcribe_returns_empty_for_placeholder_audio() -> None:
    service = SpeechService(settings=SpeechSettings())
    result = await service.transcribe("audio://placeholder")
    assert result == ""


@pytest.mark.anyio
async def test_transcribe_returns_empty_when_not_configured() -> None:
    service = SpeechService(settings=SpeechSettings(provider="stub"))
    result = await service.transcribe(base64.b64encode(b"audio-bytes").decode("ascii"))
    assert result == ""


@pytest.mark.anyio
async def test_transcribe_handles_invalid_base64(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = SpeechSettings(provider="openai", openai_api_key="test-key")
    service = SpeechService(settings=settings)

    result = await service.transcribe("not-valid-base64")
    assert result == ""


@pytest.mark.anyio
async def test_transcribe_returns_text_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = SpeechSettings(provider="openai", openai_api_key="test-key")
    service = SpeechService(settings=settings)

    class FakeResponse:
        def __init__(self) -> None:
            self._data = {"text": "hello world"}

        def raise_for_status(self) -> None:  # pragma: no cover - trivial
            return None

        def json(self) -> dict:
            return self._data

    class FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self) -> "FakeClient":
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def post(self, *args, **kwargs) -> FakeResponse:
            return FakeResponse()

    monkeypatch.setattr("app.services.stt_tts.httpx.AsyncClient", FakeClient)

    audio_b64 = base64.b64encode(b"audio-bytes").decode("ascii")
    result = await service.transcribe(audio_b64)
    assert result == "hello world"


@pytest.mark.anyio
async def test_synthesize_returns_placeholder_when_not_configured() -> None:
    service = SpeechService(settings=SpeechSettings(provider="stub"))
    result = await service.synthesize("Hello world")
    assert result == "audio://placeholder"


@pytest.mark.anyio
async def test_synthesize_returns_placeholder_on_error(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = SpeechSettings(provider="openai", openai_api_key="test-key")
    service = SpeechService(settings=settings)

    class FailingClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self) -> "FailingClient":
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def post(self, *args, **kwargs):
            raise RuntimeError("network error")

    monkeypatch.setattr("app.services.stt_tts.httpx.AsyncClient", FailingClient)

    result = await service.synthesize("Hello world")
    assert result == "audio://placeholder"


@pytest.mark.anyio
async def test_synthesize_returns_base64_audio_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = SpeechSettings(provider="openai", openai_api_key="test-key")
    service = SpeechService(settings=settings)

    class FakeResponse:
        def __init__(self) -> None:
            self.content = b"binary-audio"

        def raise_for_status(self) -> None:  # pragma: no cover - trivial
            return None

    class FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self) -> "FakeClient":
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def post(self, *args, **kwargs) -> FakeResponse:
            return FakeResponse()

    monkeypatch.setattr("app.services.stt_tts.httpx.AsyncClient", FakeClient)

    result = await service.synthesize("Hello world")
    decoded = base64.b64decode(result.encode("ascii"))
    assert decoded == b"binary-audio"


@pytest.mark.anyio
async def test_provider_override_can_be_injected() -> None:
    class EchoProvider(StubSpeechProvider):
        async def synthesize(self, text: str, voice: str | None = None) -> str:
            return f"echo:{text}"

    service = SpeechService(
        settings=SpeechSettings(provider="openai", openai_api_key="test-key"),
        provider=EchoProvider(),
    )
    assert await service.synthesize("hi") == "echo:hi"

