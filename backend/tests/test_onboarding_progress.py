from datetime import datetime

from fastapi.testclient import TestClient

from app.main import app
from app.repositories import customers_repo, appointments_repo
from app.db import SQLALCHEMY_AVAILABLE, SessionLocal
from app.db_models import BusinessDB


client = TestClient(app)


def test_onboarding_profile_and_update():
    # clean minimal repos used in onboarding context calculations
    for repo in (customers_repo, appointments_repo):
        if hasattr(repo, "_by_id"):
            repo._by_id.clear()  # type: ignore[attr-defined]
        if hasattr(repo, "_by_business"):
            repo._by_business.clear()  # type: ignore[attr-defined]
    # reset onboarding flags
    if SQLALCHEMY_AVAILABLE and SessionLocal is not None:
        session = SessionLocal()
        try:
            row = session.get(BusinessDB, "default_business")
            if row:
                row.onboarding_step = None
                row.onboarding_completed = False
                session.add(row)
                session.commit()
        finally:
            session.close()

    resp = client.get("/v1/owner/onboarding/profile")
    assert resp.status_code == 200
    data = resp.json()
    assert data["business_id"] == "default_business"
    assert data["onboarding_step"] is None
    assert data["onboarding_completed"] is False

    patch = client.patch(
        "/v1/owner/onboarding/profile",
        json={
            "owner_name": "Test Owner",
            "service_tier": "20",
            "onboarding_step": "data",
            "onboarding_completed": False,
        },
    )
    assert patch.status_code == 200
    updated = patch.json()
    assert updated["owner_name"] == "Test Owner"
    assert updated["service_tier"] == "20"
    assert updated["onboarding_step"] == "data"
    assert updated["onboarding_completed"] is False

    complete = client.patch(
        "/v1/owner/onboarding/profile",
        json={
            "onboarding_step": "complete",
            "onboarding_completed": True,
        },
    )
    assert complete.status_code == 200
    done = complete.json()
    assert done["onboarding_completed"] is True
    assert done["onboarding_step"] == "complete"
