import uuid

import pytest
from fastapi.testclient import TestClient

from app.db import SQLALCHEMY_AVAILABLE
from app.main import app


client = TestClient(app)

pytestmark = pytest.mark.skipif(
    not SQLALCHEMY_AVAILABLE,
    reason="Admin business management endpoints require database support",
)


def _create_business() -> dict:
    biz_id = f"biz-mgmt-{uuid.uuid4().hex[:8]}"
    resp = client.post(
        "/v1/admin/businesses",
        json={"id": biz_id, "name": "Mgmt Biz", "calendar_id": "cal-123"},
    )
    assert resp.status_code == 201
    return resp.json()


def test_create_patch_rotate_business_and_manage_technicians() -> None:
    created = _create_business()
    business_id = created["id"]
    original_api_key = created["api_key"]

    patch_resp = client.patch(
        f"/v1/admin/businesses/{business_id}",
        json={
            "owner_name": "Pat Owner",
            "owner_email": "pat@example.com",
            "reserve_mornings_for_emergencies": True,
            "travel_buffer_minutes": 20,
            "twilio_missed_statuses": "no-answer, busy",
            "language_code": "en",
        },
    )
    assert patch_resp.status_code == 200
    patched = patch_resp.json()
    assert patched["owner_name"] == "Pat Owner"
    assert patched["reserve_mornings_for_emergencies"] is True
    assert patched["travel_buffer_minutes"] == 20
    assert "no-answer" in patched["twilio_missed_statuses"]

    rotate_resp = client.post(f"/v1/admin/businesses/{business_id}/rotate-key")
    assert rotate_resp.status_code == 200
    rotated = rotate_resp.json()
    assert rotated["api_key"]
    assert rotated["api_key"] != original_api_key

    tech_resp = client.post(
        f"/v1/admin/businesses/{business_id}/technicians",
        json={"name": "Tech One", "color": "#fff"},
    )
    assert tech_resp.status_code == 201
    tech = tech_resp.json()
    tech_id = tech["id"]
    assert tech["is_active"] is True

    list_resp = client.get(f"/v1/admin/businesses/{business_id}/technicians")
    assert list_resp.status_code == 200
    technicians = list_resp.json()
    assert any(t["id"] == tech_id for t in technicians)

    update_resp = client.patch(
        f"/v1/admin/businesses/{business_id}/technicians/{tech_id}",
        json={"name": "Tech Updated", "is_active": False},
    )
    assert update_resp.status_code == 200
    updated = update_resp.json()
    assert updated["name"] == "Tech Updated"
    assert updated["is_active"] is False
