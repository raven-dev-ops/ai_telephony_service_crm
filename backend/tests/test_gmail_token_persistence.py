import time

from fastapi.testclient import TestClient

from app.main import app
from app.db import SessionLocal
from app.db_models import BusinessDB
from app.services.email_service import email_service
from app.services.oauth_tokens import oauth_store


client = TestClient(app)


def test_gmail_tokens_saved_to_db_and_reloaded(monkeypatch):
    # Simulate Gmail callback storing tokens in DB.
    session = SessionLocal()
    try:
        row = session.get(BusinessDB, "default_business")
        row.gmail_access_token = "db_access"  # type: ignore[assignment]
        row.gmail_refresh_token = "db_refresh"  # type: ignore[assignment]
        row.gmail_token_expires_at = None  # type: ignore[assignment]
        session.add(row)
        session.commit()
    finally:
        session.close()

    import asyncio

    loop = asyncio.new_event_loop()
    try:
        tok = loop.run_until_complete(
            email_service._refresh_token_if_needed("default_business", None, None)
        )
    finally:
        loop.close()
    assert tok is not None
    assert tok.access_token == "db_access"
    assert tok.refresh_token == "db_refresh"
