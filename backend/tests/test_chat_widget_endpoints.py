from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_chat_widget_start_and_message():
    start_resp = client.post("/v1/widget/start", json={})
    assert start_resp.status_code == 200
    data = start_resp.json()
    conv_id = data["conversation_id"]
    assert conv_id
    assert "assistant" in data["reply_text"].lower()

    msg_resp = client.post(f"/v1/widget/{conv_id}/message", json={"text": "Hello"})
    assert msg_resp.status_code == 200
    body = msg_resp.json()
    assert body["conversation_id"] == conv_id
    assert isinstance(body["reply_text"], str)

