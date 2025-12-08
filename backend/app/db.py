from __future__ import annotations

import logging
import os
from typing import Generator

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


def _build_database_url() -> str:
    """Prefer explicit DATABASE_URL; otherwise construct one for Cloud SQL."""
    explicit_url = os.getenv("DATABASE_URL")
    if explicit_url:
        return explicit_url

    conn_name = os.getenv("DB_CONNECTION_NAME")
    user = os.getenv("DB_USER")
    password = os.getenv("DB_PASSWORD")
    db_name = os.getenv("DB_NAME", "postgres")
    host = os.getenv("DB_HOST")
    port = os.getenv("DB_PORT", "5432")

    if conn_name and user and password:
        if host:
            return f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{db_name}"
        socket_host = f"/cloudsql/{conn_name}"
        return f"postgresql+psycopg2://{user}:{password}@/{db_name}?host={socket_host}"

    return "sqlite:///./app.db"


DATABASE_URL = _build_database_url()

if SQLALCHEMY_AVAILABLE:
    connect_args = (
        {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
    )
    engine = create_engine(DATABASE_URL, connect_args=connect_args)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base = declarative_base()
else:
    engine = None
    SessionLocal = None

    class _BaseStub:
        metadata = type(
            "Meta", (), {"create_all": staticmethod(lambda bind=None: None)}
        )

    Base = _BaseStub  # type: ignore[assignment]


def get_db() -> Generator["Session", None, None]:
    if not SQLALCHEMY_AVAILABLE or SessionLocal is None:
        raise RuntimeError(
            "Database support is not available (SQLAlchemy not installed)."
        )
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
    from .db_models import BusinessDB  # noqa: F401
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
                if "zip_code" not in cols:
                    conn.exec_driver_sql(
                        "ALTER TABLE businesses ADD COLUMN zip_code VARCHAR(255) NULL"
                    )
                if "median_household_income" not in cols:
                    conn.exec_driver_sql(
                        "ALTER TABLE businesses ADD COLUMN median_household_income INTEGER NULL"
                    )
                if "owner_name" not in cols:
                    conn.exec_driver_sql(
                        "ALTER TABLE businesses ADD COLUMN owner_name VARCHAR(255) NULL"
                    )
                if "owner_email" not in cols:
                    conn.exec_driver_sql(
                        "ALTER TABLE businesses ADD COLUMN owner_email VARCHAR(255) NULL"
                    )
                if "owner_profile_image_url" not in cols:
                    conn.exec_driver_sql(
                        "ALTER TABLE businesses ADD COLUMN owner_profile_image_url VARCHAR(1024) NULL"
                    )
                if "service_tier" not in cols:
                    conn.exec_driver_sql(
                        "ALTER TABLE businesses ADD COLUMN service_tier VARCHAR(64) NULL"
                    )
                if "tts_voice" not in cols:
                    conn.exec_driver_sql(
                        "ALTER TABLE businesses ADD COLUMN tts_voice VARCHAR(64) NULL"
                    )
                if "terms_accepted_at" not in cols:
                    conn.exec_driver_sql(
                        "ALTER TABLE businesses ADD COLUMN terms_accepted_at TIMESTAMP NULL"
                    )
                if "privacy_accepted_at" not in cols:
                    conn.exec_driver_sql(
                        "ALTER TABLE businesses ADD COLUMN privacy_accepted_at TIMESTAMP NULL"
                    )
                if "integration_linkedin_status" not in cols:
                    conn.exec_driver_sql(
                        "ALTER TABLE businesses ADD COLUMN integration_linkedin_status VARCHAR(32) NULL"
                    )
                if "integration_gmail_status" not in cols:
                    conn.exec_driver_sql(
                        "ALTER TABLE businesses ADD COLUMN integration_gmail_status VARCHAR(32) NULL"
                    )
                if "integration_gcalendar_status" not in cols:
                    conn.exec_driver_sql(
                        "ALTER TABLE businesses ADD COLUMN integration_gcalendar_status VARCHAR(32) NULL"
                    )
                if "integration_openai_status" not in cols:
                    conn.exec_driver_sql(
                        "ALTER TABLE businesses ADD COLUMN integration_openai_status VARCHAR(32) NULL"
                    )
                if "integration_twilio_status" not in cols:
                    conn.exec_driver_sql(
                        "ALTER TABLE businesses ADD COLUMN integration_twilio_status VARCHAR(32) NULL"
                    )
                if "integration_qbo_status" not in cols:
                    conn.exec_driver_sql(
                        "ALTER TABLE businesses ADD COLUMN integration_qbo_status VARCHAR(32) NULL"
                    )
                if "qbo_realm_id" not in cols:
                    conn.exec_driver_sql(
                        "ALTER TABLE businesses ADD COLUMN qbo_realm_id VARCHAR(128) NULL"
                    )
                if "qbo_access_token" not in cols:
                    conn.exec_driver_sql(
                        "ALTER TABLE businesses ADD COLUMN qbo_access_token TEXT NULL"
                    )
                if "qbo_refresh_token" not in cols:
                    conn.exec_driver_sql(
                        "ALTER TABLE businesses ADD COLUMN qbo_refresh_token TEXT NULL"
                    )
                if "qbo_token_expires_at" not in cols:
                    conn.exec_driver_sql(
                        "ALTER TABLE businesses ADD COLUMN qbo_token_expires_at TIMESTAMP NULL"
                    )
                if "onboarding_step" not in cols:
                    conn.exec_driver_sql(
                        "ALTER TABLE businesses ADD COLUMN onboarding_step VARCHAR(64) NULL"
                    )
                if "onboarding_completed" not in cols:
                    conn.exec_driver_sql(
                        "ALTER TABLE businesses ADD COLUMN onboarding_completed BOOLEAN DEFAULT 0"
                    )
                if "stripe_customer_id" not in cols:
                    conn.exec_driver_sql(
                        "ALTER TABLE businesses ADD COLUMN stripe_customer_id VARCHAR(255) NULL"
                    )
                if "stripe_subscription_id" not in cols:
                    conn.exec_driver_sql(
                        "ALTER TABLE businesses ADD COLUMN stripe_subscription_id VARCHAR(255) NULL"
                    )
                if "subscription_status" not in cols:
                    conn.exec_driver_sql(
                        "ALTER TABLE businesses ADD COLUMN subscription_status VARCHAR(64) NULL"
                    )
                if "subscription_current_period_end" not in cols:
                    conn.exec_driver_sql(
                        "ALTER TABLE businesses ADD COLUMN subscription_current_period_end TIMESTAMP NULL"
                    )
                conn.commit()
            # Create user and business_users tables if missing (SQLite only).
            existing_tables = {
                str(row[0])
                for row in conn.exec_driver_sql(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            if "users" not in existing_tables:
                conn.exec_driver_sql(
                    """
                    CREATE TABLE users (
                        id VARCHAR(255) PRIMARY KEY,
                        email VARCHAR(255) UNIQUE NOT NULL,
                        password_hash VARCHAR(255) NULL,
                        name VARCHAR(255) NULL,
                        active_business_id VARCHAR(255) NULL,
                        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
            if "business_users" not in existing_tables:
                conn.exec_driver_sql(
                    """
                    CREATE TABLE business_users (
                        id VARCHAR(255) PRIMARY KEY,
                        business_id VARCHAR(255) NOT NULL,
                        user_id VARCHAR(255) NOT NULL,
                        role VARCHAR(64) NOT NULL DEFAULT 'owner'
                    )
                    """
                )
            conn.commit()
    except Exception:
        # Schema drift should not prevent the app from starting; any issues
        # will surface when the new fields are actually used.
        logging.getLogger(__name__).exception("db_schema_migration_failed")

    # Ensure a default business row exists for single-tenant operation.
    session = SessionLocal()
    try:
        settings = get_settings()
        default_calendar_id = settings.calendar.calendar_id
        if not session.get(BusinessDB, "default_business"):
            default_api_key = os.getenv("DEFAULT_BUSINESS_API_KEY")
            default_widget_token = os.getenv("DEFAULT_BUSINESS_WIDGET_TOKEN")
            if not default_widget_token:
                import secrets

                default_widget_token = secrets.token_hex(16)
            session.add(
                BusinessDB(
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
