from fastapi.testclient import TestClient

from app.main import app
from app.services import conversation, sessions


client = TestClient(app)


def test_telephony_inbound_and_audio_flow():
    # Simulate inbound call.
    inbound_resp = client.post("/telephony/inbound", json={"caller_phone": "555-3333"})
    assert inbound_resp.status_code == 200
    inbound_body = inbound_resp.json()
    session_id = inbound_body["session_id"]
    assert session_id
    assert "assistant" in inbound_body["reply_text"].lower()
    assert inbound_body["session_state"]["stage"] == "ASK_NAME"
    assert inbound_body["audio"] is not None

    # Provide name via telephony audio endpoint (as text).
    audio_resp = client.post(
        "/telephony/audio",
        json={"session_id": session_id, "text": "Jane Caller"},
    )
    assert audio_resp.status_code == 200
    audio_body = audio_resp.json()
    assert "service address" in audio_body["reply_text"].lower()
    assert audio_body["session_state"]["caller_name"] == "Jane Caller"

    # End call.
    end_resp = client.post("/telephony/end", json={"session_id": session_id})
    assert end_resp.status_code == 200
    assert "ended" in end_resp.json()["status"]


def test_telephony_inbound_handles_exception(monkeypatch):
    # Force conversation handler to raise to exercise fail-safe branch.
    monkeypatch.setattr(
        conversation.conversation_manager,
        "handle_input",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    monkeypatch.setattr(
        conversation.speech_service,
        "synthesize",
        lambda *args, **kwargs: "audio://placeholder",
    )

    resp = client.post("/telephony/inbound", json={"caller_phone": "999"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["session_state"]["status"] == "FAILED"
    assert "trouble" in body["reply_text"].lower()
    assert body["audio"] == "audio://placeholder"


def test_telephony_audio_404_on_missing_session():
    resp = client.post("/telephony/audio", json={"session_id": "missing", "text": "hi"})
    assert resp.status_code == 404


def test_telephony_audio_transcribe_path(monkeypatch):
    # Create a session directly.
    session = sessions.session_store.create(business_id="biz-telephony")

    class DummyResult:
        def __init__(self):
            self.reply_text = "Acknowledged"
            self.new_state = {"stage": "NEXT"}

    async def fake_transcribe(audio):
        return "hello there"

    async def fake_handle_input(sess, text):
        return DummyResult()

    async def fake_synthesize(text, voice=None):
        return "audio://ok"

    monkeypatch.setattr(conversation.speech_service, "transcribe", fake_transcribe)
    monkeypatch.setattr(
        conversation.conversation_manager, "handle_input", fake_handle_input
    )
    monkeypatch.setattr(conversation.speech_service, "synthesize", fake_synthesize)

    resp = client.post(
        "/telephony/audio", json={"session_id": session.id, "audio": "b64://fake"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["reply_text"] == "Acknowledged"
    assert body["session_state"]["stage"] == "NEXT"
    assert body["audio"] == "audio://ok"
