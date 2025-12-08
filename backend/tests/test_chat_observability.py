from fastapi.testclient import TestClient

from app.main import app
from app.metrics import metrics
from app.services.owner_assistant import OwnerAssistantAnswer

client = TestClient(app, raise_server_exceptions=False)


class DummyAnswer:
    async def __call__(self, question: str, business_context=None):
        return OwnerAssistantAnswer(
            answer="observability test answer", used_model="stub-model"
        )


def test_chat_headers_and_latency_buckets(monkeypatch):
    from app.routers import chat_api

    monkeypatch.setattr(chat_api.owner_assistant_service, "answer", DummyAnswer())

    # Reset chat latency metrics.
    metrics.chat_latency_ms_total = 0
    metrics.chat_latency_ms_max = 0
    metrics.chat_latency_samples = 0
    metrics.chat_latency_values.clear()
    metrics.chat_latency_bucket_counts.clear()

    resp = client.post("/v1/chat", json={"text": "hello"})
    assert resp.status_code == 200
    assert resp.headers.get("X-Conversation-ID")

    # Latency counters should be populated.
    assert metrics.chat_latency_samples == 1
    assert metrics.chat_latency_ms_total > 0
    assert metrics.chat_latency_ms_max > 0
    # Bucket counts should have at least one bucket incremented.
    assert sum(metrics.chat_latency_bucket_counts.values()) >= 1

    # Prometheus output should include percentiles and histogram buckets.
    prom = client.get("/metrics/prometheus")
    assert prom.status_code == 200
    text = prom.text
    assert "ai_telephony_chat_latency_p95_ms" in text
    assert 'ai_telephony_chat_latency_bucket{le="+Inf"}' in text
