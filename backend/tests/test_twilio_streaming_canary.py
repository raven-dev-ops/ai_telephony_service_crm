from fastapi.testclient import TestClient

from app.main import app
from app import config, deps
from app.repositories import conversations_repo
from app.metrics import metrics


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
