import asyncio
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from app.main import app
from app.repositories import appointments_repo, customers_repo
from app.deps import DEFAULT_BUSINESS_ID
from app.services.calendar import calendar_service, _load_gcal_tokens
from app.services.oauth_tokens import oauth_store
from app.db import SessionLocal
from app.db_models import BusinessDB
from app import config


client = TestClient(app)


def _reset_inmemory_repos() -> None:
    if hasattr(customers_repo, "_by_id"):
        customers_repo._by_id.clear()  # type: ignore[attr-defined]
    if hasattr(customers_repo, "_by_phone"):
        customers_repo._by_phone.clear()  # type: ignore[attr-defined]
    if hasattr(customers_repo, "_by_business"):
        customers_repo._by_business.clear()  # type: ignore[attr-defined]
    if hasattr(appointments_repo, "_by_id"):
        appointments_repo._by_id.clear()  # type: ignore[attr-defined]
    if hasattr(appointments_repo, "_by_business"):
        appointments_repo._by_business.clear()  # type: ignore[attr-defined]
    if hasattr(appointments_repo, "_by_customer"):
        appointments_repo._by_customer.clear()  # type: ignore[attr-defined]


def test_calendar_webhook_updates_appointment():
    _reset_inmemory_repos()

    cust = customers_repo.upsert(
        name="Cal Tester",
        phone="+15550123456",
        business_id=DEFAULT_BUSINESS_ID,
    )
    start = datetime.now(UTC) + timedelta(days=1)
    end = start + timedelta(hours=1)
    appt = appointments_repo.create(
        customer_id=cust.id,
        start_time=start,
        end_time=end,
        service_type="Install",
        is_emergency=False,
        description="Install sink",
        business_id=DEFAULT_BUSINESS_ID,
        calendar_event_id="evt_123",
    )

    resp = client.post(
        "/v1/calendar/google/webhook",
        json={
            "business_id": DEFAULT_BUSINESS_ID,
            "event_id": "evt_123",
            "status": "cancelled",
            "start": (start + timedelta(hours=2)).isoformat(),
            "end": (end + timedelta(hours=2)).isoformat(),
            "summary": "Updated install",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["processed"] is True

    updated = appointments_repo.get(appt.id)
    assert updated is not None
    assert updated.status == "CANCELLED"
    assert updated.job_stage == "Cancelled"
    assert updated.start_time == start + timedelta(hours=2)
    assert updated.service_type == "Updated install"


def test_calendar_webhook_missing_event_returns_ok():
    resp = client.post(
        "/v1/calendar/google/webhook",
        json={
            "business_id": DEFAULT_BUSINESS_ID,
            "event_id": "unknown_evt",
            "status": "cancelled",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["processed"] is False


def test_calendar_webhook_normalizes_dst_offsets_to_utc():
    _reset_inmemory_repos()

    cust = customers_repo.upsert(
        name="DST Tester",
        phone="+15550999999",
        business_id=DEFAULT_BUSINESS_ID,
    )
    # Appointment stored in UTC.
    start_utc = datetime(2025, 3, 9, 7, 30, tzinfo=UTC)
    end_utc = datetime(2025, 3, 9, 8, 30, tzinfo=UTC)
    appt = appointments_repo.create(
        customer_id=cust.id,
        start_time=start_utc,
        end_time=end_utc,
        service_type="Inspection",
        is_emergency=False,
        description="DST check",
        business_id=DEFAULT_BUSINESS_ID,
        calendar_event_id="evt_dst",
    )

    # DST start in many US zones: offset changes from -06:00 to -05:00.
    resp = client.post(
        "/v1/calendar/google/webhook",
        json={
            "business_id": DEFAULT_BUSINESS_ID,
            "event_id": "evt_dst",
            "status": "confirmed",
            "start": "2025-03-09T01:30:00-06:00",
            "end": "2025-03-09T03:30:00-05:00",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["processed"] is True

    updated = appointments_repo.get(appt.id)
    assert updated is not None
    assert updated.start_time == start_utc
    assert updated.end_time == end_utc
    assert updated.start_time.tzinfo == UTC
    assert updated.end_time.tzinfo == UTC


def test_calendar_webhook_naive_timestamps_assume_utc_and_mark_rescheduled():
    _reset_inmemory_repos()

    cust = customers_repo.upsert(
        name="Naive TZ Tester",
        phone="+15550888888",
        business_id=DEFAULT_BUSINESS_ID,
    )
    original_start = datetime(2025, 12, 1, 9, 0, tzinfo=UTC)
    original_end = datetime(2025, 12, 1, 10, 0, tzinfo=UTC)
    appt = appointments_repo.create(
        customer_id=cust.id,
        start_time=original_start,
        end_time=original_end,
        service_type="Repair",
        is_emergency=False,
        description="Naive update check",
        business_id=DEFAULT_BUSINESS_ID,
        calendar_event_id="evt_naive",
    )

    resp = client.post(
        "/v1/calendar/google/webhook",
        json={
            "business_id": DEFAULT_BUSINESS_ID,
            "event_id": "evt_naive",
            "start": "2025-12-01T10:00:00",
            "end": "2025-12-01T11:00:00",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["processed"] is True

    updated = appointments_repo.get(appt.id)
    assert updated is not None
    assert updated.start_time == datetime(2025, 12, 1, 10, 0, tzinfo=UTC)
    assert updated.end_time == datetime(2025, 12, 1, 11, 0, tzinfo=UTC)
    assert updated.job_stage == "Rescheduled"


def test_calendar_webhook_ignores_invalid_time_range():
    _reset_inmemory_repos()

    cust = customers_repo.upsert(
        name="Invalid Range Tester",
        phone="+15550777777",
        business_id=DEFAULT_BUSINESS_ID,
    )
    start = datetime(2025, 12, 2, 9, 0, tzinfo=UTC)
    end = datetime(2025, 12, 2, 10, 0, tzinfo=UTC)
    appt = appointments_repo.create(
        customer_id=cust.id,
        start_time=start,
        end_time=end,
        service_type="Install",
        is_emergency=False,
        description="Invalid range check",
        business_id=DEFAULT_BUSINESS_ID,
        calendar_event_id="evt_invalid",
    )

    resp = client.post(
        "/v1/calendar/google/webhook",
        json={
            "business_id": DEFAULT_BUSINESS_ID,
            "event_id": "evt_invalid",
            "start": "2025-12-02T12:00:00Z",
            "end": "2025-12-02T11:00:00Z",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["processed"] is True

    updated = appointments_repo.get(appt.id)
    assert updated is not None
    assert updated.start_time == start
    assert updated.end_time == end


def test_find_slots_prefers_oauth_user_client(monkeypatch):
    oauth_store.save_tokens(
        "gcalendar",
        "biz_gcal",
        access_token="access",
        refresh_token="refresh",
        expires_in=3600,
    )
    original_use_stub = calendar_service._settings.use_stub
    calendar_service._settings.use_stub = False
    calendar_service._client = None

    calls = []

    class DummyClient:
        def freebusy(self):
            return self

        def query(self, body=None):
            return self

        def execute(self):
            return {"calendars": {"primary": {"busy": []}}}

    def fake_user_client(business_id):
        calls.append(business_id)
        return DummyClient()

    monkeypatch.setattr(calendar_service, "_build_user_client", fake_user_client)
    monkeypatch.setattr(
        calendar_service, "_resolve_calendar_id", lambda *a, **k: "primary"
    )

    slots = asyncio.run(
        calendar_service.find_slots(
            duration_minutes=60,
            calendar_id=None,
            business_id="biz_gcal",
        )
    )
    assert calls == ["biz_gcal"]
    assert slots

    calendar_service._settings.use_stub = original_use_stub


def test_gcalendar_tokens_persist_and_load(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "csecret")
    monkeypatch.setenv("OAUTH_REDIRECT_BASE", "https://example.com/auth")
    config.get_settings.cache_clear()

    start = client.get(f"/auth/gcalendar/start?business_id={DEFAULT_BUSINESS_ID}")
    assert start.status_code == 200
    state = start.json()["authorization_url"].split("state=")[-1]

    async def fake_exchange(code, redirect_uri, scopes):
        return "access-live", "refresh-live", 1200

    from app.routers import auth_integration

    monkeypatch.setattr(
        auth_integration, "_exchange_google_code_for_tokens", fake_exchange
    )

    resp = client.get(f"/auth/gcalendar/callback?state={state}&code=abc")
    assert resp.status_code == 200
    tok = oauth_store.get_tokens("gcalendar", DEFAULT_BUSINESS_ID)
    assert tok is not None
    session = SessionLocal()
    try:
        row = session.get(BusinessDB, DEFAULT_BUSINESS_ID)
        assert getattr(row, "gcalendar_access_token", None) == "access-live"
        assert getattr(row, "gcalendar_refresh_token", None) == "refresh-live"
        loaded = _load_gcal_tokens(DEFAULT_BUSINESS_ID)
        assert loaded is not None
        assert loaded.access_token == "access-live"
    finally:
        session.close()
    config.get_settings.cache_clear()


def test_gcalendar_refresh_updates_db_and_store(monkeypatch):
    from app.services import calendar as calendar_service

    class DummyOAuth:
        def __init__(self) -> None:
            self.google_client_id = "cid"
            self.google_client_secret = "secret"
            self.redirect_base = "https://app.local/auth"
            self.gmail_scopes = "scope"
            self.gcalendar_scopes = "scope"

    class DummySettings:
        def __init__(self) -> None:
            self.oauth = DummyOAuth()

    class DummyResp:
        status_code = 200
        text = "ok"

        def json(self):
            return {
                "access_token": "new_access",
                "refresh_token": "new_refresh",
                "expires_in": 1800,
            }

    class DummyClient:
        def __init__(self, timeout=None):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, data=None, auth=None):
            return DummyResp()

    monkeypatch.setattr(
        calendar_service, "httpx", type("X", (), {"Client": DummyClient})
    )
    monkeypatch.setattr(calendar_service, "get_settings", lambda: DummySettings())
    oauth_store.save_tokens(
        "gcalendar",
        DEFAULT_BUSINESS_ID,
        access_token="old_access",
        refresh_token="old_refresh",
        expires_in=3600,
    )
    session = SessionLocal()
    try:
        row = session.get(BusinessDB, DEFAULT_BUSINESS_ID)
        row.gcalendar_access_token = "old_access"  # type: ignore[assignment]
        row.gcalendar_refresh_token = "old_refresh"  # type: ignore[assignment]
        row.gcalendar_token_expires_at = datetime.now(UTC) - timedelta(minutes=1)  # type: ignore[assignment]
        session.add(row)
        session.commit()
    finally:
        session.close()

    tok = calendar_service._refresh_gcal_tokens(DEFAULT_BUSINESS_ID, DummySettings())
    assert tok is not None
    assert tok.access_token == "new_access"
    assert tok.refresh_token == "new_refresh"

    session = SessionLocal()
    try:
        row = session.get(BusinessDB, DEFAULT_BUSINESS_ID)
        assert row.gcalendar_access_token == "new_access"  # type: ignore[union-attr]
        assert row.gcalendar_refresh_token == "new_refresh"  # type: ignore[union-attr]
    finally:
        session.close()
