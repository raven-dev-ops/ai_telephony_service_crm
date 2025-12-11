import pytest

from app.services import nlu


@pytest.mark.anyio
async def test_llm_only_overrides_when_confidence_low(monkeypatch):
    # Force provider to openai for the classifier.
    class DummySpeech:
        provider = "openai"
        openai_api_key = "key"
        openai_chat_model = "gpt-4o-mini"
        openai_api_base = "https://api.openai.com/v1"

    class DummySettings:
        def __init__(self) -> None:
            self.nlu = type("X", (), {"intent_provider": "openai"})()
            self.speech = DummySpeech()

    # Fake LLM always returns "schedule".
    async def fake_llm(text, history=None):
        return "schedule"

    monkeypatch.setattr(nlu, "get_settings", lambda: DummySettings())
    monkeypatch.setattr(nlu, "_classify_with_llm", fake_llm)

    # High-confidence heuristic emergency should stay emergency.
    meta_em = await nlu.classify_intent_with_metadata("burst pipe emergency")
    assert meta_em["intent"] == "emergency"
    assert meta_em["provider"] == "heuristic"

    # Low-confidence heuristic "other" can be upgraded by LLM.
    meta_low = await nlu.classify_intent_with_metadata("i need something maybe")
    assert meta_low["intent"] == "schedule"
    assert meta_low["provider"] == "openai"

    # Confident heuristic schedule should remain heuristic.
    meta_sched = await nlu.classify_intent_with_metadata("book appointment tomorrow")
    assert meta_sched["intent"] == "schedule"
    assert meta_sched["provider"] == "heuristic"
