from __future__ import annotations

from datetime import UTC, datetime, timedelta
import base64

import pytest

from app.config import SpeechSettings
from app.services.stt_tts import GoogleCloudSpeechProvider


class FakeCreds:
    def __init__(self, token: str | None, expiry: datetime | None) -> None:
        self.token = token
        self.expiry = expiry
        self.refresh_calls = 0

    def refresh(self, _request) -> None:
        self.refresh_calls += 1
        self.token = "refreshed-token"
        self.expiry = datetime.now(UTC) + timedelta(hours=1)


def _wav_bytes(sample_rate_hz: int) -> bytes:
    data = bytearray(28)
    data[0:4] = b"RIFF"
    data[8:12] = b"WAVE"
    data[24:28] = int(sample_rate_hz).to_bytes(4, "little")
    return bytes(data)


def test_gcp_parse_wav_sample_rate() -> None:
    provider = GoogleCloudSpeechProvider(SpeechSettings(provider="gcp"))
    assert provider._parse_wav_sample_rate(_wav_bytes(16000)) == 16000
    assert provider._parse_wav_sample_rate(b"short") is None
    assert provider._parse_wav_sample_rate(b"NOPE" * 10) is None


@pytest.mark.anyio
async def test_gcp_access_token_returns_existing_token() -> None:
    provider = GoogleCloudSpeechProvider(SpeechSettings(provider="gcp"))
    provider._credentials = FakeCreds(
        token="existing-token",
        expiry=datetime.now(UTC) + timedelta(hours=1),
    )
    token = await provider._access_token()
    assert token == "existing-token"
    assert provider._credentials.refresh_calls == 0


@pytest.mark.anyio
async def test_gcp_access_token_refreshes_when_expired() -> None:
    provider = GoogleCloudSpeechProvider(SpeechSettings(provider="gcp"))
    provider._credentials = FakeCreds(
        token=None,
        expiry=datetime.now(UTC) - timedelta(seconds=1),
    )
    token = await provider._access_token()
    assert token == "refreshed-token"
    assert provider._credentials.refresh_calls == 1


@pytest.mark.anyio
async def test_gcp_transcribe_parses_results_and_uses_sample_rate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = GoogleCloudSpeechProvider(SpeechSettings(provider="gcp"))
    provider._credentials = FakeCreds(
        token="existing-token",
        expiry=datetime.now(UTC) + timedelta(hours=1),
    )

    captured: dict = {}

    class FakeResp:
        def raise_for_status(self) -> None:  # pragma: no cover - trivial
            return None

        def json(self) -> dict:
            return {
                "results": [
                    {"alternatives": [{"transcript": "hello"}]},
                    {"alternatives": [{"transcript": "world"}]},
                ]
            }

    class FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self) -> "FakeClient":
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def post(self, url: str, headers=None, json=None):
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json
            return FakeResp()

    monkeypatch.setattr("app.services.stt_tts.httpx.AsyncClient", FakeClient)

    audio_b64 = base64.b64encode(_wav_bytes(16000)).decode("ascii")
    text = await provider.transcribe(audio_b64)
    assert text == "hello world"
    assert captured["json"]["config"]["sampleRateHertz"] == 16000


@pytest.mark.anyio
async def test_gcp_transcribe_detects_mp3_and_omits_sample_rate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = GoogleCloudSpeechProvider(SpeechSettings(provider="gcp"))
    provider._credentials = FakeCreds(
        token="existing-token",
        expiry=datetime.now(UTC) + timedelta(hours=1),
    )

    captured: dict = {}

    class FakeResp:
        def raise_for_status(self) -> None:  # pragma: no cover - trivial
            return None

        def json(self) -> dict:
            return {"results": [{"alternatives": [{"transcript": "ok"}]}]}

    class FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self) -> "FakeClient":
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def post(self, url: str, headers=None, json=None):
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json
            return FakeResp()

    monkeypatch.setattr("app.services.stt_tts.httpx.AsyncClient", FakeClient)

    # Minimal bytes with an ID3 header are sufficient for encoding detection.
    audio_b64 = base64.b64encode(b"ID3" + b"\x00" * 64).decode("ascii")
    text = await provider.transcribe(audio_b64)
    assert text == "ok"
    assert captured["json"]["config"]["encoding"] == "MP3"
    assert "sampleRateHertz" not in captured["json"]["config"]


@pytest.mark.anyio
async def test_gcp_synthesize_returns_audio_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = GoogleCloudSpeechProvider(SpeechSettings(provider="gcp"))
    provider._credentials = FakeCreds(
        token="existing-token",
        expiry=datetime.now(UTC) + timedelta(hours=1),
    )

    class FakeResp:
        def raise_for_status(self) -> None:  # pragma: no cover - trivial
            return None

        def json(self) -> dict:
            return {"audioContent": "abc123"}

    class FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self) -> "FakeClient":
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def post(self, *args, **kwargs):
            return FakeResp()

    monkeypatch.setattr("app.services.stt_tts.httpx.AsyncClient", FakeClient)

    assert await provider.synthesize("hi") == "abc123"
    assert await provider.synthesize("   ") == "audio://placeholder"


@pytest.mark.anyio
async def test_gcp_synthesize_raises_when_audio_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = GoogleCloudSpeechProvider(SpeechSettings(provider="gcp"))
    provider._credentials = FakeCreds(
        token="existing-token",
        expiry=datetime.now(UTC) + timedelta(hours=1),
    )

    class FakeResp:
        def raise_for_status(self) -> None:  # pragma: no cover - trivial
            return None

        def json(self) -> dict:
            return {}

    class FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self) -> "FakeClient":
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def post(self, *args, **kwargs):
            return FakeResp()

    monkeypatch.setattr("app.services.stt_tts.httpx.AsyncClient", FakeClient)

    with pytest.raises(RuntimeError):
        await provider.synthesize("hello")


@pytest.mark.anyio
async def test_gcp_healthcheck_credentials_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = GoogleCloudSpeechProvider(SpeechSettings(provider="gcp"))

    async def fail_token() -> str:
        raise RuntimeError("no creds")

    monkeypatch.setattr(provider, "_access_token", fail_token)

    result = await provider.healthcheck()
    assert result["healthy"] is False
    assert result["reason"] == "credentials_unavailable"


@pytest.mark.anyio
async def test_gcp_healthcheck_success(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = GoogleCloudSpeechProvider(SpeechSettings(provider="gcp"))
    provider._credentials = FakeCreds(
        token="existing-token",
        expiry=datetime.now(UTC) + timedelta(hours=1),
    )

    class FakeResp:
        def raise_for_status(self) -> None:  # pragma: no cover - trivial
            return None

    class FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self) -> "FakeClient":
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def get(self, *args, **kwargs):
            return FakeResp()

    monkeypatch.setattr("app.services.stt_tts.httpx.AsyncClient", FakeClient)

    result = await provider.healthcheck()
    assert result["healthy"] is True
    assert result["provider"] == "gcp"
