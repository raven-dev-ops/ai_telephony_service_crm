from fastapi.testclient import TestClient

from app.db import SQLALCHEMY_AVAILABLE, SessionLocal
from app.db_models import BusinessDB
from app.main import app


client = TestClient(app)


def test_chat_widget_start_and_message() -> None:
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


def test_chat_widget_message_404_for_unknown_conversation() -> None:
    resp = client.post(
        "/v1/widget/nonexistent-conversation/message",
        json={"text": "Hello"},
    )
    assert resp.status_code == 404


def test_widget_business_returns_default_business_id() -> None:
    resp = client.get("/v1/widget/business")
    assert resp.status_code == 200
    body = resp.json()
    # Default tenant ID is used when no explicit business header is provided.
    assert body["id"] == "default_business"
    assert isinstance(body["name"], str)
    assert body["name"]
    assert isinstance(body["language_code"], str)
    assert body["language_code"]


def test_widget_business_uses_db_name_when_available() -> None:
    if not SQLALCHEMY_AVAILABLE or SessionLocal is None:
        return

    biz_id = "widget_business_test"
    session = SessionLocal()
    try:
        row = session.get(BusinessDB, biz_id)
        if row is None:
            row = BusinessDB(  # type: ignore[call-arg]
                id=biz_id,
                name="Widget Business Test",
            )
            session.add(row)
        else:
            row.name = "Widget Business Test"
        row.language_code = "es"
        session.commit()
    finally:
        session.close()

    resp = client.get("/v1/widget/business", headers={"X-Business-ID": biz_id})
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == biz_id
    assert body["name"] == "Widget Business Test"
    assert body["language_code"] == "es"
