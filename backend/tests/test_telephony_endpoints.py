from fastapi.testclient import TestClient

from app.main import app


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

