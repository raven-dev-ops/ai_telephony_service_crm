import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.metrics import metrics
from app.deps import DEFAULT_BUSINESS_ID
from app.services import conversation


client = TestClient(app)


def test_voice_session_lifecycle():
    # Start a session.
    start_resp = client.post("/v1/voice/session/start", json={"caller_phone": "555-2222"})
    assert start_resp.status_code == 200
    session_id = start_resp.json()["session_id"]
    assert session_id

    # Initial input (empty) should trigger greeting.
    input_resp = client.post(f"/v1/voice/session/{session_id}/input", json={})
    assert input_resp.status_code == 200
    body = input_resp.json()
    assert "assistant" in body["reply_text"].lower()
    assert body["session_state"]["stage"] == "ASK_NAME"
    assert body["audio"] is not None

    # End session.
    end_resp = client.post(f"/v1/voice/session/{session_id}/end")
    assert end_resp.status_code == 200
    assert "ended" in end_resp.json()["status"]


def test_voice_session_metrics_increment():
    # Reset voice session metrics.
    metrics.voice_session_requests = 0
    metrics.voice_session_errors = 0
    metrics.voice_sessions_by_business.clear()

    start_resp = client.post("/v1/voice/session/start", json={"caller_phone": "555-3333"})
    assert start_resp.status_code == 200
    session_id = start_resp.json()["session_id"]

    input_resp = client.post(f"/v1/voice/session/{session_id}/input", json={})
    assert input_resp.status_code == 200

    # Both start and input endpoints should count as voice session requests.
    assert metrics.voice_session_requests == 2
    per_tenant = metrics.voice_sessions_by_business[DEFAULT_BUSINESS_ID]
    assert per_tenant.requests == 2
    assert metrics.voice_session_errors == 0
    assert per_tenant.errors == 0


def test_voice_session_error_increments_metrics(monkeypatch):
    # Reset voice session metrics.
    metrics.voice_session_requests = 0
    metrics.voice_session_errors = 0
    metrics.voice_sessions_by_business.clear()

    async def failing_handle_input(session, text):
        raise RuntimeError("forced voice session error")

    monkeypatch.setattr(
        conversation.conversation_manager, "handle_input", failing_handle_input
    )

    start_resp = client.post("/v1/voice/session/start", json={"caller_phone": "555-4444"})
    assert start_resp.status_code == 200
    session_id = start_resp.json()["session_id"]

    with pytest.raises(RuntimeError):
        client.post(f"/v1/voice/session/{session_id}/input", json={"text": "hi"})

    assert metrics.voice_session_requests == 2
    assert metrics.voice_session_errors == 1
    per_tenant = metrics.voice_sessions_by_business[DEFAULT_BUSINESS_ID]
    assert per_tenant.requests == 2
    assert per_tenant.errors == 1
