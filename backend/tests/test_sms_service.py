import asyncio

from app.services.sms import sms_service


def run(coro):
    return asyncio.run(coro)


def test_sms_service_records_messages_in_stub_mode():
    # Ensure starting from a clean slate
    sms_service._sent.clear()  # type: ignore[attr-defined]

    run(sms_service.send_sms("+15550001111", "Test message"))

    sent = sms_service.sent_messages
    assert len(sent) == 1
    assert sent[0].to == "+15550001111"
    assert sent[0].body == "Test message"
