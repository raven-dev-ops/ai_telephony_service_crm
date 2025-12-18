from __future__ import annotations

import pytest

from app.db import SQLALCHEMY_AVAILABLE, SessionLocal
from app.db_models import BusinessDB
from app.deps import DEFAULT_BUSINESS_ID
from app.services.oauth_tokens import oauth_store


def _reset_default_business_schedule_settings() -> None:
    if not (SQLALCHEMY_AVAILABLE and SessionLocal is not None):
        return
    session = SessionLocal()
    try:
        row = session.get(BusinessDB, DEFAULT_BUSINESS_ID)
        if not row:
            return
        row.closed_days = None
        row.open_hour = 8
        row.close_hour = 17
        row.max_jobs_per_day = None
        row.reserve_mornings_for_emergencies = False
        row.travel_buffer_minutes = None
        row.service_duration_config = None
        session.add(row)
        session.commit()
    finally:
        session.close()


@pytest.fixture(autouse=True)
def _isolate_global_state():
    oauth_store._tokens.clear()  # type: ignore[attr-defined]
    _reset_default_business_schedule_settings()
    yield
    oauth_store._tokens.clear()  # type: ignore[attr-defined]
    _reset_default_business_schedule_settings()
