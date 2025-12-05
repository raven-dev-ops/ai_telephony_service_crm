from __future__ import annotations

from .config import get_settings
from .db import SQLALCHEMY_AVAILABLE, SessionLocal
from .db_models import Business


def get_calendar_id_for_business(business_id: str) -> str:
    """Return the calendar ID to use for a given business/tenant.

    - If DB support is available and the Business row has a calendar_id, use it.
    - Otherwise, fall back to the global calendar_id from settings.
    """
    settings = get_settings()
    default_calendar_id = settings.calendar.calendar_id

    if not SQLALCHEMY_AVAILABLE or SessionLocal is None:
        return default_calendar_id

    session = SessionLocal()
    try:
        row = session.get(Business, business_id)
        if row and getattr(row, "calendar_id", None):
            return row.calendar_id  # type: ignore[return-value]
        return default_calendar_id
    finally:
        session.close()


def get_language_for_business(business_id: str | None) -> str:
    """Return the language code for a given business/tenant.

    Falls back to the default language from settings when no per-tenant
    override is configured or when database support is unavailable.
    """
    settings = get_settings()
    default_language = getattr(settings, "default_language_code", "en")

    if not business_id or not (SQLALCHEMY_AVAILABLE and SessionLocal is not None):
        return default_language

    session = SessionLocal()
    try:
        row = session.get(Business, business_id)
        if row and getattr(row, "language_code", None):
            return row.language_code  # type: ignore[return-value]
        return default_language
    finally:
        session.close()


def get_vertical_for_business(business_id: str | None) -> str:
    """Return the business vertical (e.g., plumbing, hvac) for a tenant.

    Falls back to the default vertical from settings when no per-tenant
    override is configured or when database support is unavailable.
    """
    settings = get_settings()
    default_vertical = getattr(settings, "default_vertical", "plumbing")

    if not business_id or not (SQLALCHEMY_AVAILABLE and SessionLocal is not None):
        return default_vertical

    session = SessionLocal()
    try:
        row = session.get(Business, business_id)
        if row and getattr(row, "vertical", None):
            return row.vertical  # type: ignore[return-value]
        return default_vertical
    finally:
        session.close()
