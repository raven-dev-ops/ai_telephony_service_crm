import logging

import pytest

from app import db
import app.config as app_config
from app.logging_config import configure_logging


def test_configure_logging_adds_stdout_handler_and_is_idempotent() -> None:
    root = logging.getLogger()
    # Preserve existing handlers so other tests are not affected.
    original_handlers = list(root.handlers)
    try:
        # Remove all handlers to force configure_logging to run its setup.
        for handler in list(root.handlers):
            root.removeHandler(handler)

        assert not root.handlers

        configure_logging()
        assert root.handlers
        first_ids = {id(h) for h in root.handlers}

        # Calling configure_logging again should not add duplicate handlers.
        configure_logging()
        second_ids = {id(h) for h in root.handlers}
        assert second_ids == first_ids
    finally:
        # Restore original handlers.
        for handler in list(root.handlers):
            root.removeHandler(handler)
        for handler in original_handlers:
            root.addHandler(handler)


def test_get_db_raises_when_sqlalchemy_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(db, "SQLALCHEMY_AVAILABLE", False)
    monkeypatch.setattr(db, "SessionLocal", None)

    with pytest.raises(RuntimeError) as exc_info:
        next(db.get_db())

    assert "Database support is not available" in str(exc_info.value)


def test_init_db_handles_schema_migration_failure_gracefully(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class DummyMeta:
        @staticmethod
        def create_all(bind=None) -> None:  # pragma: no cover - trivial
            return None

    class DummyBase:
        metadata = DummyMeta()

    class DummyConn:
        def __enter__(self):  # pragma: no cover - trivial
            raise RuntimeError("forced schema error")

        def __exit__(self, exc_type, exc, tb) -> bool:  # pragma: no cover - trivial
            return False

    class DummyEngine:
        url = "sqlite:///./dummy.db"

        def connect(self) -> DummyConn:  # pragma: no cover - trivial
            return DummyConn()

    class DummySession:
        def get(self, *args, **kwargs):  # pragma: no cover - trivial
            # Pretend the default business already exists so init_db
            # does not attempt to insert anything.
            return object()

        def close(self) -> None:  # pragma: no cover - trivial
            return None

    monkeypatch.setattr(db, "Base", DummyBase)
    monkeypatch.setattr(db, "engine", DummyEngine())
    monkeypatch.setattr(db, "SessionLocal", lambda: DummySession())

    # init_db should swallow the migration error and not raise.
    db.init_db()


def test_init_db_returns_when_sqlalchemy_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(db, "SQLALCHEMY_AVAILABLE", False)
    monkeypatch.setattr(db, "engine", None)
    # Should simply return without raising.
    db.init_db()


def test_init_db_creates_default_business_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(db, "SQLALCHEMY_AVAILABLE", True)

    class DummyMeta:
        @staticmethod
        def create_all(bind=None) -> None:  # pragma: no cover - trivial
            return None

    class DummyBase:
        metadata = DummyMeta()

    class DummyEngine:
        url = "postgres://example"

    class DummySession:
        def __init__(self) -> None:
            self.added = []
            self.commits = 0
            self.closed = False

        def get(self, model, key):  # type: ignore[no-untyped-def]
            return None

        def add(self, obj) -> None:  # pragma: no cover - trivial
            self.added.append(obj)

        def commit(self) -> None:  # pragma: no cover - trivial
            self.commits += 1

        def close(self) -> None:  # pragma: no cover - trivial
            self.closed = True

    dummy_session = DummySession()
    monkeypatch.setattr(db, "Base", DummyBase)
    monkeypatch.setattr(db, "engine", DummyEngine())
    monkeypatch.setattr(db, "SessionLocal", lambda: dummy_session)

    class DummyCalendar:
        calendar_id = "cal-default"

    class DummySettings:
        calendar = DummyCalendar()

    monkeypatch.setattr(app_config, "get_settings", lambda: DummySettings())

    db.init_db()

    # Default business should have been added and committed.
    assert dummy_session.added
    assert dummy_session.commits >= 1
