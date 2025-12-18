from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Dict, Protocol
from uuid import uuid4
import json
import logging
import os

from ..config import get_settings

redis_module: Any | None
try:  # Optional Redis dependency
    import redis as _redis
except Exception:  # pragma: no cover - redis is optional
    redis_module = None
else:
    redis_module = _redis


@dataclass
class CallSession:
    id: str
    caller_phone: str | None = None
    caller_name: str | None = None
    address: str | None = None
    problem_summary: str | None = None
    requested_time: str | None = None
    is_emergency: bool = False
    emergency_confidence: float = 0.0
    emergency_reasons: list[str] = field(default_factory=list)
    emergency_confirmation_pending: bool = False
    intent: str | None = None
    intent_confidence: float | None = None
    stage: str = "GREETING"
    status: str = "ACTIVE"
    business_id: str = "default_business"
    channel: str = "phone"
    lead_source: str | None = None
    no_input_count: int = 0
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class SessionStore(Protocol):
    """Abstract interface for storing CallSession state.

    Implementations may be in-memory, Redis-backed, or database-backed.
    """

    def create(
        self,
        caller_phone: str | None = None,
        business_id: str = "default_business",
        lead_source: str | None = None,
    ) -> CallSession: ...

    def get(self, session_id: str) -> CallSession | None: ...

    def end(self, session_id: str) -> None: ...


class InMemorySessionStore:
    """Temporary in-memory session store for early development."""

    def __init__(self) -> None:
        self._sessions: Dict[str, CallSession] = {}

    def create(
        self,
        caller_phone: str | None = None,
        business_id: str = "default_business",
        lead_source: str | None = None,
    ) -> CallSession:
        session_id = str(uuid4())
        session = CallSession(
            id=session_id,
            caller_phone=caller_phone,
            business_id=business_id,
            channel="phone",
            lead_source=lead_source,
        )
        self._sessions[session_id] = session
        return session

    def get(self, session_id: str) -> CallSession | None:
        return self._sessions.get(session_id)

    def end(self, session_id: str) -> None:
        session = self._sessions.pop(session_id, None)
        if session:
            session.status = "COMPLETED"
            session.updated_at = datetime.now(UTC)


class RedisSessionStore:
    """Session store backed by Redis.

    This implementation is opt-in via SESSION_STORE_BACKEND=redis and expects
    a REDIS_URL environment variable. When Redis is unavailable, the factory
    will fall back to the in-memory store.
    """

    def __init__(
        self,
        client: Any,
        key_prefix: str = "call_session",
        ttl_seconds: int = 3600,
    ) -> None:
        self._client = client
        self._key_prefix = key_prefix
        self._ttl_seconds = ttl_seconds

    def _key(self, session_id: str) -> str:
        return f"{self._key_prefix}:{session_id}"

    def create(
        self,
        caller_phone: str | None = None,
        business_id: str = "default_business",
        lead_source: str | None = None,
    ) -> CallSession:
        session_id = str(uuid4())
        now = datetime.now(UTC)
        session = CallSession(
            id=session_id,
            caller_phone=caller_phone,
            business_id=business_id,
            channel="phone",
            created_at=now,
            updated_at=now,
            lead_source=lead_source,
        )
        self._persist(session)
        return session

    def get(self, session_id: str) -> CallSession | None:
        raw = self._client.get(self._key(session_id))
        if not raw:
            return None
        try:
            data = json.loads(raw)
        except Exception:
            return None
        created_at = _parse_iso_datetime(data.get("created_at"))
        updated_at = _parse_iso_datetime(data.get("updated_at"))
        return CallSession(
            id=data.get("id", session_id),
            caller_phone=data.get("caller_phone"),
            caller_name=data.get("caller_name"),
            address=data.get("address"),
            problem_summary=data.get("problem_summary"),
            requested_time=data.get("requested_time"),
            is_emergency=bool(data.get("is_emergency", False)),
            stage=data.get("stage", "GREETING"),
            status=data.get("status", "ACTIVE"),
            business_id=data.get("business_id", "default_business"),
            channel=data.get("channel", "phone"),
            lead_source=data.get("lead_source"),
            no_input_count=int(data.get("no_input_count", 0)),
            created_at=created_at or datetime.now(UTC),
            updated_at=updated_at or datetime.now(UTC),
        )

    def end(self, session_id: str) -> None:
        # Mark the session as completed if it exists and persist the update.
        session = self.get(session_id)
        if not session:
            return
        session.status = "COMPLETED"
        session.updated_at = datetime.now(UTC)
        self._persist(session)

    def _persist(self, session: CallSession) -> None:
        payload = {
            "id": session.id,
            "caller_phone": session.caller_phone,
            "caller_name": session.caller_name,
            "address": session.address,
            "problem_summary": session.problem_summary,
            "requested_time": session.requested_time,
            "is_emergency": session.is_emergency,
            "stage": session.stage,
            "status": session.status,
            "business_id": session.business_id,
            "channel": session.channel,
            "lead_source": session.lead_source,
            "no_input_count": session.no_input_count,
            "created_at": session.created_at.isoformat(),
            "updated_at": session.updated_at.isoformat(),
        }
        try:
            self._client.setex(
                self._key(session.id), self._ttl_seconds, json.dumps(payload)
            )
        except Exception:
            # Redis failures should not bring down request handling; callers
            # must be prepared for missing sessions.
            logging.getLogger(__name__).warning(
                "redis_session_store_persist_failed", exc_info=True
            )


def _parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        # fromisoformat understands timezone info when present.
        return datetime.fromisoformat(value)
    except Exception:
        return None


def _create_session_store() -> SessionStore:
    """Factory for the process-wide session store.

    For now only the in-memory backend is supported. In future, this can be
    extended to return a Redis- or DB-backed implementation when configured via
    settings.session_store_backend.
    """
    settings = get_settings()
    # If a REDIS_URL is provided, prefer redis even when the backend setting is
    # left at the default. This enables shared sessions in multi-replica setups.
    backend = getattr(settings, "session_store_backend", "memory").lower()
    if backend == "memory" and os.getenv("REDIS_URL"):
        backend = "redis"
    if backend == "redis":
        if redis_module is None:
            logging.getLogger(__name__).warning(
                "session_store_backend_redis_unavailable_falling_back",
                extra={"backend": backend},
            )
        else:
            try:
                redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
                client = redis_module.from_url(redis_url)
                return RedisSessionStore(client)
            except Exception:
                logging.getLogger(__name__).warning(
                    "session_store_backend_redis_init_failed_falling_back",
                    exc_info=True,
                )
    # Default and fallback: in-memory store.
    return InMemorySessionStore()


session_store: SessionStore = _create_session_store()
