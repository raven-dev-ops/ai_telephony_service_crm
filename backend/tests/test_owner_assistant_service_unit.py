import pytest

from app.services.owner_assistant import OwnerAssistantService


@pytest.mark.anyio
async def test_owner_assistant_prompts_when_question_empty() -> None:
    svc = OwnerAssistantService()
    answer = await svc.answer("")
    assert "please type a question" in answer.answer.lower()


@pytest.mark.anyio
async def test_owner_assistant_returns_stub_when_not_configured(monkeypatch) -> None:
    svc = OwnerAssistantService()
    # Force a non-OpenAI configuration to hit the fallback path.
    monkeypatch.setattr(
        svc,
        "_speech",
        type("Cfg", (), {"provider": "stub", "openai_api_key": None})(),
    )
    answer = await svc.answer("How do I view my metrics?")
    assert "not fully configured" in answer.answer
