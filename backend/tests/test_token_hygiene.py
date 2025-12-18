import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import app.main as main
from app import deps
from app.db import SQLALCHEMY_AVAILABLE, SessionLocal
from app.db_models import BusinessDB
from app.metrics import metrics


def _require_db() -> None:
    if not (SQLALCHEMY_AVAILABLE and SessionLocal is not None):
        pytest.skip("SQLAlchemy not available for token hygiene tests")


def test_widget_token_expiry_and_last_used_tracking() -> None:
    _require_db()
    settings = main.get_settings()
    settings.require_business_api_key = False
    main.create_app()

    session = SessionLocal()
    try:
        row = session.get(BusinessDB, deps.DEFAULT_BUSINESS_ID)
        assert row is not None
        token = row.widget_token or "temp-token"
        row.widget_token = token
        row.widget_token_expires_at = datetime.now(UTC) + timedelta(minutes=5)
        session.add(row)
        session.commit()
    finally:
        session.close()

    asyncio.run(
        deps.get_business_id(
            x_widget_token=token,
            x_api_key=None,
            x_business_id=None,
            authorization=None,
        )
    )

    session = SessionLocal()
    try:
        refreshed = session.get(BusinessDB, deps.DEFAULT_BUSINESS_ID)
        assert refreshed is not None
        assert refreshed.widget_token_last_used_at is not None
        refreshed.widget_token_expires_at = datetime.now(UTC) - timedelta(minutes=1)
        session.add(refreshed)
        session.commit()
    finally:
        session.close()

    with pytest.raises(HTTPException):
        asyncio.run(
            deps.get_business_id(
                x_widget_token=token,
                x_api_key=None,
                x_business_id=None,
                authorization=None,
            )
        )


def test_admin_owner_token_rotation_and_usage_endpoint() -> None:
    _require_db()
    settings = main.get_settings()
    prev_admin = settings.admin_api_key
    prev_owner = settings.owner_dashboard_token
    try:
        settings.admin_api_key = "admin-old"
        settings.owner_dashboard_token = "owner-old"
        metrics.admin_token_last_used_at = None
        metrics.owner_token_last_used_at = None

        app = main.create_app()
        client = TestClient(app)

        rotate_admin_resp = client.post(
            "/v1/admin/tokens/admin/rotate",
            headers={"X-Admin-API-Key": "admin-old"},
        )
        assert rotate_admin_resp.status_code == 200
        new_admin = rotate_admin_resp.json()["token"]
        assert new_admin

        rotate_owner_resp = client.post(
            "/v1/admin/tokens/owner/rotate",
            headers={"X-Admin-API-Key": new_admin},
        )
        assert rotate_owner_resp.status_code == 200
        new_owner = rotate_owner_resp.json()["token"]
        assert new_owner

        admin_list_resp = client.get(
            "/v1/admin/businesses", headers={"X-Admin-API-Key": new_admin}
        )
        assert admin_list_resp.status_code == 200
        assert metrics.admin_token_last_used_at

        usage_resp = client.get(
            "/v1/admin/tokens/usage", headers={"X-Admin-API-Key": new_admin}
        )
        assert usage_resp.status_code == 200
        usage = usage_resp.json()
        assert usage["business_tokens"]

        owner_resp = client.get(
            "/v1/owner/onboarding/profile", headers={"X-Owner-Token": new_owner}
        )
        assert owner_resp.status_code == 200
        assert metrics.owner_token_last_used_at
    finally:
        settings.admin_api_key = prev_admin
        settings.owner_dashboard_token = prev_owner
