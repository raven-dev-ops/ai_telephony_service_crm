from app.services import sessions


class _DummySettings:
    def __init__(self, session_store_backend: str) -> None:
        self.session_store_backend = session_store_backend


def test_session_store_defaults_to_inmemory_when_configured_as_memory(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        sessions,
        "get_settings",
        lambda: _DummySettings(session_store_backend="memory"),
    )
    monkeypatch.delenv("REDIS_URL", raising=False)

    store = sessions._create_session_store()
    assert isinstance(store, sessions.InMemorySessionStore)


def test_session_store_falls_back_when_redis_library_missing(monkeypatch) -> None:
    monkeypatch.setattr(
        sessions,
        "get_settings",
        lambda: _DummySettings(session_store_backend="redis"),
    )
    monkeypatch.setattr(sessions, "redis", None)

    store = sessions._create_session_store()
    assert isinstance(store, sessions.InMemorySessionStore)


def test_session_store_falls_back_when_redis_init_fails(monkeypatch) -> None:
    monkeypatch.setattr(
        sessions,
        "get_settings",
        lambda: _DummySettings(session_store_backend="redis"),
    )

    class FailingRedisModule:
        def from_url(self, url: str):
            raise RuntimeError("redis down")

    monkeypatch.setattr(sessions, "redis", FailingRedisModule())

    store = sessions._create_session_store()
    assert isinstance(store, sessions.InMemorySessionStore)


def test_session_store_uses_redis_when_available(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        sessions,
        "get_settings",
        lambda: _DummySettings(session_store_backend="redis"),
    )

    class DummyRedisClient:
        def __init__(self) -> None:
            self._data: dict[str, str] = {}

        def setex(self, key: str, ttl: int, value: str) -> None:
            self._data[key] = value

        def get(self, key: str) -> str | None:
            return self._data.get(key)

    class DummyRedisModule:
        def __init__(self) -> None:
            self.last_url: str | None = None

        def from_url(self, url: str) -> DummyRedisClient:
            self.last_url = url
            return DummyRedisClient()

    dummy_module = DummyRedisModule()
    monkeypatch.setattr(sessions, "redis", dummy_module)
    monkeypatch.setenv("REDIS_URL", "redis://test-redis:6379/1")

    store = sessions._create_session_store()
    assert isinstance(store, sessions.RedisSessionStore)

    session = store.create(caller_phone="555-0100", business_id="b1", lead_source="test")
    assert session.id
    fetched = store.get(session.id)
    assert fetched is not None
    assert fetched.caller_phone == "555-0100"

    store.end(session.id)
    ended = store.get(session.id)
    assert ended is not None
    assert ended.status == "COMPLETED"
    assert dummy_module.last_url == "redis://test-redis:6379/1"


def test_session_store_prefers_redis_when_url_present(monkeypatch) -> None:
    monkeypatch.setattr(
        sessions,
        "get_settings",
        # Simulate default "memory" setting, but REDIS_URL provided.
        lambda: _DummySettings(session_store_backend="memory"),
    )

    class DummyRedisClient:
        def __init__(self) -> None:
            self._data: dict[str, str] = {}

        def setex(self, key: str, ttl: int, value: str) -> None:
            self._data[key] = value

        def get(self, key: str) -> str | None:
            return self._data.get(key)

    class DummyRedisModule:
        def from_url(self, url: str) -> DummyRedisClient:
            return DummyRedisClient()

    monkeypatch.setattr(sessions, "redis", DummyRedisModule())
    monkeypatch.setenv("REDIS_URL", "redis://auto:6379/0")

    store = sessions._create_session_store()
    assert isinstance(store, sessions.RedisSessionStore)

