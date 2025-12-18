from fastapi.testclient import TestClient

from app.main import app
from app.repositories import conversations_repo, customers_repo
from app.services import sessions


client = TestClient(app)


def _clear_inmemory_repos() -> None:
    if hasattr(customers_repo, "_by_id"):
        customers_repo._by_id.clear()  # type: ignore[attr-defined]
        customers_repo._by_phone.clear()  # type: ignore[attr-defined]
        customers_repo._by_business.clear()  # type: ignore[attr-defined]
    if hasattr(conversations_repo, "_by_id"):
        conversations_repo._by_id.clear()  # type: ignore[attr-defined]
        conversations_repo._by_session.clear()  # type: ignore[attr-defined]
        conversations_repo._by_business.clear()  # type: ignore[attr-defined]


def test_widget_chat_keeps_session_state_across_turns() -> None:
    _clear_inmemory_repos()

    start_resp = client.post("/v1/widget/start", json={"customer_phone": "555-9901"})
    assert start_resp.status_code == 200
    start = start_resp.json()
    conv_id = start["conversation_id"]
    assert conv_id
    assert "to get started" in start["reply_text"].lower()

    conv = conversations_repo.get(conv_id)
    assert conv is not None
    assert conv.session_id
    session = sessions.session_store.get(conv.session_id)
    assert session is not None
    assert session.stage == "ASK_NAME"

    msg1 = client.post(f"/v1/widget/{conv_id}/message", json={"text": "Pat"})
    assert msg1.status_code == 200
    assert "service address" in msg1.json()["reply_text"].lower()
    assert session.stage == "ASK_ADDRESS"

    msg2 = client.post(
        f"/v1/widget/{conv_id}/message", json={"text": "12 Pine Rd, KC MO"}
    )
    assert msg2.status_code == 200
    assert "describe" in msg2.json()["reply_text"].lower()
    assert session.stage == "ASK_PROBLEM"


def test_voice_and_widget_greetings_match_for_new_caller() -> None:
    _clear_inmemory_repos()

    phone = "555-9902"
    voice_start = client.post("/v1/voice/session/start", json={"caller_phone": phone})
    assert voice_start.status_code == 200
    session_id = voice_start.json()["session_id"]

    voice_greet = client.post(f"/v1/voice/session/{session_id}/input", json={})
    assert voice_greet.status_code == 200
    voice_text = voice_greet.json()["reply_text"]

    widget_start = client.post("/v1/widget/start", json={"customer_phone": phone})
    assert widget_start.status_code == 200
    widget_text = widget_start.json()["reply_text"]

    assert widget_text == voice_text
    assert "worked with you before" not in widget_text.lower()


def test_emergency_confirmation_is_consumed_in_both_channels() -> None:
    _clear_inmemory_repos()

    phrase = "There is a sewer smell in the basement"

    # Voice: greet then provide ambiguous emergency phrase.
    phone = "555-9903"
    voice_start = client.post("/v1/voice/session/start", json={"caller_phone": phone})
    assert voice_start.status_code == 200
    session_id = voice_start.json()["session_id"]
    client.post(f"/v1/voice/session/{session_id}/input", json={})

    voice_emergency = client.post(
        f"/v1/voice/session/{session_id}/input", json={"text": phrase}
    )
    assert voice_emergency.status_code == 200
    assert "is this an emergency" in voice_emergency.json()["reply_text"].lower()
    assert (
        voice_emergency.json()["session_state"]["emergency_confirmation_pending"]
        is True
    )

    voice_no = client.post(f"/v1/voice/session/{session_id}/input", json={"text": "no"})
    assert voice_no.status_code == 200
    assert "didn't catch your name" in voice_no.json()["reply_text"].lower()

    # Widget: same behavior.
    widget_start = client.post("/v1/widget/start", json={"customer_phone": phone})
    assert widget_start.status_code == 200
    conv_id = widget_start.json()["conversation_id"]

    widget_emergency = client.post(
        f"/v1/widget/{conv_id}/message", json={"text": phrase}
    )
    assert widget_emergency.status_code == 200
    assert "is this an emergency" in widget_emergency.json()["reply_text"].lower()

    conv = conversations_repo.get(conv_id)
    assert conv is not None and conv.session_id
    session = sessions.session_store.get(conv.session_id)
    assert session is not None
    assert session.emergency_confirmation_pending is True

    widget_no = client.post(f"/v1/widget/{conv_id}/message", json={"text": "no"})
    assert widget_no.status_code == 200
    assert "didn't catch your name" in widget_no.json()["reply_text"].lower()
