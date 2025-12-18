import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.metrics import metrics
from app.deps import DEFAULT_BUSINESS_ID
from app.services import conversation
from app.routers import voice as voice_router


client = TestClient(app)


def test_voice_session_lifecycle():
    # Start a session.
    start_resp = client.post(
        "/v1/voice/session/start", json={"caller_phone": "555-2222"}
    )
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

    start_resp = client.post(
        "/v1/voice/session/start", json={"caller_phone": "555-3333"}
    )
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

    start_resp = client.post(
        "/v1/voice/session/start", json={"caller_phone": "555-4444"}
    )
    assert start_resp.status_code == 200
    session_id = start_resp.json()["session_id"]

    with pytest.raises(RuntimeError):
        client.post(f"/v1/voice/session/{session_id}/input", json={"text": "hi"})

    assert metrics.voice_session_requests == 2
    assert metrics.voice_session_errors == 1
    per_tenant = metrics.voice_sessions_by_business[DEFAULT_BUSINESS_ID]
    assert per_tenant.requests == 2
    assert per_tenant.errors == 1


def test_voice_session_input_returns_404_when_session_missing():
    resp = client.post("/v1/voice/session/nope/input", json={"text": "hi"})
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Session not found"


def test_voice_session_input_transcribes_audio_when_text_missing(monkeypatch):
    async def fake_transcribe(audio: str) -> str:
        assert audio == "audio://blob"
        return "hello from audio"

    async def fake_handle_input(session, text):
        assert text == "hello from audio"
        return type(
            "R",
            (),
            {"reply_text": "ok", "new_state": {"stage": "DONE"}},
        )()

    async def fake_synthesize(text: str, voice: str | None = None) -> str:
        assert text == "ok"
        return "audio://ok"

    monkeypatch.setattr(conversation.speech_service, "transcribe", fake_transcribe)
    monkeypatch.setattr(
        conversation.conversation_manager, "handle_input", fake_handle_input
    )
    monkeypatch.setattr(conversation.speech_service, "synthesize", fake_synthesize)

    start_resp = client.post(
        "/v1/voice/session/start", json={"caller_phone": "555-5555"}
    )
    assert start_resp.status_code == 200
    session_id = start_resp.json()["session_id"]

    input_resp = client.post(
        f"/v1/voice/session/{session_id}/input",
        json={"audio": "audio://blob"},
    )
    assert input_resp.status_code == 200
    data = input_resp.json()
    assert data["reply_text"] == "ok"
    assert data["audio"] == "audio://ok"
    assert data["session_state"]["stage"] == "DONE"


@pytest.mark.anyio
async def test_voice_session_input_returns_fallback_when_not_testing(monkeypatch):
    from app.services.sessions import CallSession

    original_getenv = voice_router.os.getenv

    def fake_getenv(key: str, default: str | None = None):
        if key == "PYTEST_CURRENT_TEST":
            return None
        if key == "TESTING":
            return "false"
        return original_getenv(key, default)

    async def failing_handle_input(session, text):
        raise RuntimeError("forced voice session error")

    session_id = "sess_fallback_stage"
    session = CallSession(
        id=session_id, stage="GREETING", business_id=DEFAULT_BUSINESS_ID
    )

    monkeypatch.setattr(voice_router.os, "getenv", fake_getenv)
    monkeypatch.setattr(
        conversation.conversation_manager, "handle_input", failing_handle_input
    )
    monkeypatch.setattr(voice_router.sessions.session_store, "get", lambda _: session)

    payload = voice_router.SessionInputRequest(text="hi")
    result = await voice_router.session_input(
        session_id=session_id,
        payload=payload,
        business_id=DEFAULT_BUSINESS_ID,
    )
    assert "trouble speaking" in result.reply_text.lower()
    assert result.audio == "audio://placeholder"
    assert result.session_state == {"stage": "GREETING"}


@pytest.mark.anyio
async def test_voice_session_input_fallback_prefers_session_state_dict(monkeypatch):
    original_getenv = voice_router.os.getenv

    def fake_getenv(key: str, default: str | None = None):
        if key == "PYTEST_CURRENT_TEST":
            return None
        if key == "TESTING":
            return "false"
        return original_getenv(key, default)

    async def failing_handle_input(session, text):
        raise RuntimeError("forced voice session error")

    class DummySession:
        def __init__(self) -> None:
            self.state = {"stage": "CUSTOM"}
            self.stage = "IGNORED"

    session_id = "sess_fallback_dict"

    monkeypatch.setattr(voice_router.os, "getenv", fake_getenv)
    monkeypatch.setattr(
        conversation.conversation_manager, "handle_input", failing_handle_input
    )
    monkeypatch.setattr(
        voice_router.sessions.session_store, "get", lambda _: DummySession()
    )

    payload = voice_router.SessionInputRequest(text="hi")
    result = await voice_router.session_input(
        session_id=session_id,
        payload=payload,
        business_id=DEFAULT_BUSINESS_ID,
    )
    assert result.session_state == {"stage": "CUSTOM"}
