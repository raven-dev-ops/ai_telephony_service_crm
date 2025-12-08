from fastapi.testclient import TestClient

from app.main import app
from app.services.owner_assistant import OwnerAssistantAnswer

client = TestClient(app, raise_server_exceptions=False)


class DummyAnswer:
    async def __call__(self, question: str, business_context=None):
        return OwnerAssistantAnswer(
            answer="streaming reply chunks", used_model="stub-model"
        )


def test_chat_stream_sends_sse(monkeypatch):
    # Force deterministic answer for streaming.
    from app.routers import chat_api

    monkeypatch.setattr(chat_api.owner_assistant_service, "answer", DummyAnswer())

    resp = client.post(
        "/v1/chat/stream",
        json={"text": "Hello"},
        headers={"Accept": "text/event-stream"},
    )
    assert resp.status_code == 200
    assert resp.headers.get("content-type", "").startswith("text/event-stream")

    body = resp.text
    # Should include metadata event with conversation_id and a data chunk.
    assert "event: meta" in body
    assert "data: streaming reply chunks" in body or "reply" in body
    # Conversation id should be present in meta JSON.
    metas = [
        line
        for line in body.splitlines()
        if line.startswith("data:") and "conversation_id" in line
    ]
    assert metas
