import base64
import time

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.main import app
from app import config, deps
from app.repositories import conversations_repo
from app.metrics import metrics
from app.deps import DEFAULT_BUSINESS_ID
from app.services.stt_tts import speech_service
from app.services.twilio_state import twilio_state_store


client = TestClient(app)


def test_twilio_streaming_canary(monkeypatch):
    monkeypatch.setenv("TWILIO_STREAMING_ENABLED", "true")
    config.get_settings.cache_clear()
    deps.get_settings.cache_clear()
    metrics.twilio_voice_requests = 0

    start = client.post(
        "/v1/twilio/voice-stream",
        json={
            "call_sid": "CS123",
            "stream_sid": "SS1",
            "event": "start",
            "business_id": "default_business",
            "from_number": "+15550001111",
        },
    )
    assert start.status_code == 200
    start_body = start.json()
    assert start_body["status"] == "ok"
    session_id = start_body["session_id"]
    assert session_id
    assert start_body["reply_text"]

    media = client.post(
        "/v1/twilio/voice-stream",
        json={
            "call_sid": "CS123",
            "stream_sid": "SS1",
            "event": "media",
            "business_id": "default_business",
            "transcript": "I need to book an appointment tomorrow morning",
        },
    )
    assert media.status_code == 200
    media_body = media.json()
    assert media_body["reply_text"]
    conv = conversations_repo.get_by_session(session_id)
    assert conv is not None
    assert any(msg.role == "assistant" for msg in conv.messages)
    assert conv.intent in {"schedule", "faq", "greeting", "other"}


def test_twilio_stream_stop_enqueues_partial_callback(monkeypatch):
    metrics.callbacks_by_business.clear()
    metrics.twilio_by_business.clear()
    monkeypatch.setenv("TWILIO_STREAMING_ENABLED", "true")
    config.get_settings.cache_clear()
    deps.get_settings.cache_clear()

    start = client.post(
        "/v1/twilio/voice-stream",
        json={
            "call_sid": "CS_STOP1",
            "stream_sid": "SS_STOP1",
            "event": "start",
            "business_id": DEFAULT_BUSINESS_ID,
            "from_number": "+15550002222",
        },
    )
    assert start.status_code == 200

    stop = client.post(
        "/v1/twilio/voice-stream",
        json={
            "call_sid": "CS_STOP1",
            "stream_sid": "SS_STOP1",
            "event": "stop",
            "business_id": DEFAULT_BUSINESS_ID,
            "from_number": "+15550002222",
        },
    )
    assert stop.status_code == 200
    queue = metrics.callbacks_by_business.get(DEFAULT_BUSINESS_ID, {})
    assert "+15550002222" in queue
    item = queue["+15550002222"]
    assert item.reason == "PARTIAL_INTAKE"
    assert item.status == "PENDING"


def test_twilio_stream_silence_triggers_callback(monkeypatch):
    metrics.callbacks_by_business.clear()
    metrics.twilio_by_business.clear()
    monkeypatch.setenv("TWILIO_STREAMING_ENABLED", "true")
    config.get_settings.cache_clear()
    deps.get_settings.cache_clear()

    phone = "+15550003333"
    start = client.post(
        "/v1/twilio/voice-stream",
        json={
            "call_sid": "CS_SILENCE1",
            "stream_sid": "SS_SILENCE1",
            "event": "start",
            "business_id": DEFAULT_BUSINESS_ID,
            "from_number": phone,
        },
    )
    assert start.status_code == 200

    first = client.post(
        "/v1/twilio/voice-stream",
        json={
            "call_sid": "CS_SILENCE1",
            "stream_sid": "SS_SILENCE1",
            "event": "media",
            "business_id": DEFAULT_BUSINESS_ID,
            "from_number": phone,
            "transcript": "",
        },
    )
    assert first.status_code == 200
    first_body = first.json()
    assert first_body["reply_text"]
    assert "trouble hearing you" in first_body["reply_text"].lower()

    second = client.post(
        "/v1/twilio/voice-stream",
        json={
            "call_sid": "CS_SILENCE1",
            "stream_sid": "SS_SILENCE1",
            "event": "media",
            "business_id": DEFAULT_BUSINESS_ID,
            "from_number": phone,
            "transcript": "",
        },
    )
    assert second.status_code == 200
    second_body = second.json()
    assert second_body["completed"] is True
    queue = metrics.callbacks_by_business.get(DEFAULT_BUSINESS_ID, {})
    assert phone in queue
    assert queue[phone].reason == "NO_INPUT"


def test_twilio_streaming_websocket_ingest(monkeypatch):
    metrics.callbacks_by_business.clear()
    metrics.twilio_by_business.clear()
    monkeypatch.setenv("TWILIO_STREAMING_ENABLED", "true")
    monkeypatch.setenv("TWILIO_STREAM_MIN_SECONDS", "0.01")
    monkeypatch.setenv("TWILIO_STREAM_TOKEN", "test-stream-token")
    config.get_settings.cache_clear()
    deps.get_settings.cache_clear()

    called = {"count": 0}

    async def fake_transcribe(_: str | None) -> str:
        called["count"] += 1
        return "I need service tomorrow morning"

    monkeypatch.setattr(speech_service, "transcribe", fake_transcribe)

    with client.websocket_connect(
        "/v1/twilio/voice-stream?call_sid=CS_WS1&business_id=default_business"
        "&stream_token=test-stream-token"
    ) as ws:
        ws.send_json(
            {
                "event": "start",
                "start": {
                    "callSid": "CS_WS1",
                    "streamSid": "SS_WS1",
                    "mediaFormat": {
                        "encoding": "audio/x-mulaw",
                        "sampleRate": 8000,
                    },
                },
            }
        )
        payload = base64.b64encode(b"\xff" * 160).decode("ascii")
        ws.send_json(
            {
                "event": "media",
                "media": {
                    "track": "inbound",
                    "payload": payload,
                },
            }
        )

        link = twilio_state_store.get_call_session("CS_WS1")
        assert link is not None

        ws.send_json({"event": "stop"})
        for _ in range(50):
            if called["count"] > 0:
                break
            time.sleep(0.01)
        assert called["count"] > 0


def test_twilio_streaming_websocket_requires_token(monkeypatch):
    monkeypatch.setenv("TWILIO_STREAMING_ENABLED", "true")
    monkeypatch.setenv("TWILIO_STREAM_TOKEN", "required-token")
    config.get_settings.cache_clear()
    deps.get_settings.cache_clear()

    with client.websocket_connect(
        "/v1/twilio/voice-stream?call_sid=CS_WS_NO_TOKEN&business_id=default_business"
    ) as ws:
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_text()
    assert exc.value.code == 1008
