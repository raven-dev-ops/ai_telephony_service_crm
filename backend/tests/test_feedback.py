import os
import tempfile
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.main import app
from app.services.feedback_store import feedback_store, FeedbackEntry


client = TestClient(app)


def test_submit_feedback_records_entry(monkeypatch):
    # Use a temp file to avoid polluting real logs.
    tmp = tempfile.NamedTemporaryFile(delete=False)
    tmp.close()
    monkeypatch.setattr(feedback_store, "_path", tmp.name)
    feedback_store._entries.clear()  # type: ignore[attr-defined]

    resp = client.post(
        "/v1/feedback",
        json={"summary": "Beta bug", "category": "bug", "expected": "works", "actual": "fails"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["submitted"] is True
    rows = feedback_store.list()
    assert rows
    assert rows[0]["summary"] == "Beta bug"


def test_admin_export_feedback(monkeypatch):
    monkeypatch.setenv("ADMIN_API_KEY", "admin-key")
    # Seed a feedback entry
    feedback_store._entries.append(  # type: ignore[attr-defined]
        FeedbackEntry(
            created_at=datetime.now(UTC),
            business_id="default_business",
            category="bug",
            summary="Export test",
            steps=None,
            expected=None,
            actual=None,
            call_sid=None,
            contact=None,
            url=None,
            user_agent=None,
        )
    )

    resp = client.get(
        "/v1/admin/feedback",
        headers={"X-Admin-API-Key": "admin-key"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "feedback" in data
    assert any(item["summary"] == "Export test" for item in data["feedback"])
