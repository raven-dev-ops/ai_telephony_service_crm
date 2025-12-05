from __future__ import annotations

import os
from typing import Generator, Optional

try:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session, declarative_base, sessionmaker

    SQLALCHEMY_AVAILABLE = True
except Exception:  # pragma: no cover - optional dependency
    create_engine = None  # type: ignore[assignment]
    sessionmaker = None  # type: ignore[assignment]
    declarative_base = None  # type: ignore[assignment]
    Session = object  # type: ignore[assignment]
    SQLALCHEMY_AVAILABLE = False


DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./app.db")

if SQLALCHEMY_AVAILABLE:
    connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
    engine = create_engine(DATABASE_URL, connect_args=connect_args)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base = declarative_base()
else:
    engine = None
    SessionLocal = None

    class _BaseStub:
        metadata = type("Meta", (), {"create_all": staticmethod(lambda bind=None: None)})

    Base = _BaseStub  # type: ignore[assignment]


def get_db() -> Generator["Session", None, None]:
    if not SQLALCHEMY_AVAILABLE or SessionLocal is None:
        raise RuntimeError("Database support is not available (SQLAlchemy not installed).")
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    if not SQLALCHEMY_AVAILABLE or engine is None:
        # Skip DB initialization when SQLAlchemy is unavailable (e.g., minimal test environments).
        return
    # Importing here avoids circular imports at module load time.
    from sqlalchemy import text  # type: ignore

    from .db_models import Business  # noqa: F401
    from .config import get_settings

    Base.metadata.create_all(bind=engine)

    # Lightweight, best-effort schema migration for new Business columns when
    # using SQLite in dev/test. This avoids manual migrations while keeping
    # production-friendly behaviour on other databases.
    try:  # pragma: no cover - exercised indirectly in tests
        url = str(engine.url)
        if url.startswith("sqlite"):
            with engine.connect() as conn:
                cols = []
                result = conn.exec_driver_sql("PRAGMA table_info(businesses)")
                for row in result:
                    # PRAGMA table_info columns: cid, name, type, notnull, dflt_value, pk
                    cols.append(str(row[1]))
                if "retention_enabled" not in cols:
                    conn.exec_driver_sql(
                        "ALTER TABLE businesses ADD COLUMN retention_enabled BOOLEAN DEFAULT 1"
                    )
                if "retention_sms_template" not in cols:
                    conn.exec_driver_sql(
                        "ALTER TABLE businesses ADD COLUMN retention_sms_template TEXT"
                    )
                conn.commit()
    except Exception:
        # Schema drift should not prevent the app from starting; any issues
        # will surface when the new fields are actually used.
        pass

    # Ensure a default business row exists for single-tenant operation.
    session = SessionLocal()
    try:
        settings = get_settings()
        default_calendar_id = settings.calendar.calendar_id
        if not session.get(Business, "default_business"):
            default_api_key = os.getenv("DEFAULT_BUSINESS_API_KEY")
            default_widget_token = os.getenv("DEFAULT_BUSINESS_WIDGET_TOKEN")
            if not default_widget_token:
                import secrets

                default_widget_token = secrets.token_hex(16)
            session.add(
                Business(
                    id="default_business",
                    name="Default Business",
                    api_key=default_api_key,
                    calendar_id=default_calendar_id,
                    status="ACTIVE",
                    widget_token=default_widget_token,
                )
            )
            session.commit()
    finally:
        session.close()
