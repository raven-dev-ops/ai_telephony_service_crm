from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Dict, Optional, Protocol, Tuple
import json
import logging
import os

from ..config import get_settings

try:  # Optional Redis dependency, mirroring services/sessions.py
    import redis  # type: ignore[import]
except Exception:  # pragma: no cover - redis is optional
    redis = None  # type: ignore[assignment]


logger = logging.getLogger(__name__)


@dataclass
class CallSessionLink:
    session_id: str
    created_at: datetime


@dataclass
class SmsConversationLink:
    conversation_id: str
    created_at: datetime


class TwilioStateStore(Protocol):
    """Abstract interface for Twilio call/SMS state.

    Implementations may be in-memory or backed by a shared store such as Redis.
    """

    def get_call_session(self, call_sid: str) -> Optional[CallSessionLink]:
        ...

    def set_call_session(self, call_sid: str, session_id: str) -> None:
        ...

    def clear_call_session(self, call_sid: str) -> Optional[CallSessionLink]:
        ...

    def get_sms_conversation(
        self,
        business_id: str,
        from_phone: str,
    ) -> Optional[SmsConversationLink]:
        ...

    def set_sms_conversation(
        self,
        business_id: str,
        from_phone: str,
        conversation_id: str,
    ) -> None:
        ...

    def clear_sms_conversation(
        self,
        business_id: str,
        from_phone: str,
    ) -> Optional[SmsConversationLink]:
        ...


class InMemoryTwilioStateStore:
    """In-process Twilio state with bounded TTL.

    This is suitable for local development and single-process deployments.
    """

    _CALL_SESSION_TTL = timedelta(hours=1)
    _SMS_CONV_TTL = timedelta(days=7)

    def __init__(self) -> None:
        self._call_map: Dict[str, CallSessionLink] = {}
        self._sms_map: Dict[Tuple[str, str], SmsConversationLink] = {}

    def _prune_call_sessions(self) -> None:
        if not self._call_map:
            return
        now = datetime.now(UTC)
        expired = [
            sid
            for sid, link in self._call_map.items()
            if link.created_at + self._CALL_SESSION_TTL < now
        ]
        for sid in expired:
            self._call_map.pop(sid, None)

    def _prune_sms_conversations(self) -> None:
        if not self._sms_map:
            return
        now = datetime.now(UTC)
        expired = [
            key
            for key, link in self._sms_map.items()
            if link.created_at + self._SMS_CONV_TTL < now
        ]
        for key in expired:
            self._sms_map.pop(key, None)

    def get_call_session(self, call_sid: str) -> Optional[CallSessionLink]:
        self._prune_call_sessions()
        return self._call_map.get(call_sid)

    def set_call_session(self, call_sid: str, session_id: str) -> None:
        self._prune_call_sessions()
        self._call_map[call_sid] = CallSessionLink(
            session_id=session_id,
            created_at=datetime.now(UTC),
        )

    def clear_call_session(self, call_sid: str) -> Optional[CallSessionLink]:
        self._prune_call_sessions()
        return self._call_map.pop(call_sid, None)

    def get_sms_conversation(
        self,
        business_id: str,
        from_phone: str,
    ) -> Optional[SmsConversationLink]:
        self._prune_sms_conversations()
        key = (business_id, from_phone)
        return self._sms_map.get(key)

    def set_sms_conversation(
        self,
        business_id: str,
        from_phone: str,
        conversation_id: str,
    ) -> None:
        self._prune_sms_conversations()
        key = (business_id, from_phone)
        self._sms_map[key] = SmsConversationLink(
            conversation_id=conversation_id,
            created_at=datetime.now(UTC),
        )

    def clear_sms_conversation(
        self,
        business_id: str,
        from_phone: str,
    ) -> Optional[SmsConversationLink]:
        self._prune_sms_conversations()
        key = (business_id, from_phone)
        return self._sms_map.pop(key, None)


class RedisTwilioStateStore:
    """Redis-backed Twilio state store.

    This is opt-in via TWILIO_STATE_BACKEND=redis and expects REDIS_URL to be
    set. When Redis is unavailable or misconfigured, the factory falls back to
    the in-memory implementation.
    """

    _CALL_SESSION_TTL_SECONDS = int(timedelta(hours=1).total_seconds())
    _SMS_CONV_TTL_SECONDS = int(timedelta(days=7).total_seconds())

    def __init__(self, client: "redis.Redis", key_prefix: str = "twilio_state") -> None:
        self._client = client
        self._prefix = key_prefix

    def _call_key(self, call_sid: str) -> str:
        return f"{self._prefix}:call:{call_sid}"

    def _sms_key(self, business_id: str, from_phone: str) -> str:
        return f"{self._prefix}:sms:{business_id}:{from_phone}"

    def get_call_session(self, call_sid: str) -> Optional[CallSessionLink]:
        raw = self._client.get(self._call_key(call_sid))
        if not raw:
            return None
        try:
            data = json.loads(raw)
            created_at_raw = data.get("created_at")
            created_at = (
                datetime.fromisoformat(created_at_raw)
                if isinstance(created_at_raw, str)
                else datetime.now(UTC)
            )
            return CallSessionLink(
                session_id=data.get("session_id", ""),
                created_at=created_at,
            )
        except Exception:
            return None

    def set_call_session(self, call_sid: str, session_id: str) -> None:
        payload = {
            "session_id": session_id,
            "created_at": datetime.now(UTC).isoformat(),
        }
        try:
            self._client.setex(
                self._call_key(call_sid),
                self._CALL_SESSION_TTL_SECONDS,
                json.dumps(payload),
            )
        except Exception:
            logger.warning("redis_twilio_state_set_call_failed", exc_info=True)

    def clear_call_session(self, call_sid: str) -> Optional[CallSessionLink]:
        link = self.get_call_session(call_sid)
        try:
            self._client.delete(self._call_key(call_sid))
        except Exception:
            logger.warning("redis_twilio_state_clear_call_failed", exc_info=True)
        return link

    def get_sms_conversation(
        self,
        business_id: str,
        from_phone: str,
    ) -> Optional[SmsConversationLink]:
        raw = self._client.get(self._sms_key(business_id, from_phone))
        if not raw:
            return None
        try:
            data = json.loads(raw)
            created_at_raw = data.get("created_at")
            created_at = (
                datetime.fromisoformat(created_at_raw)
                if isinstance(created_at_raw, str)
                else datetime.now(UTC)
            )
            return SmsConversationLink(
                conversation_id=data.get("conversation_id", ""),
                created_at=created_at,
            )
        except Exception:
            return None

    def set_sms_conversation(
        self,
        business_id: str,
        from_phone: str,
        conversation_id: str,
    ) -> None:
        payload = {
            "conversation_id": conversation_id,
            "created_at": datetime.now(UTC).isoformat(),
        }
        try:
            self._client.setex(
                self._sms_key(business_id, from_phone),
                self._SMS_CONV_TTL_SECONDS,
                json.dumps(payload),
            )
        except Exception:
            logger.warning("redis_twilio_state_set_sms_failed", exc_info=True)

    def clear_sms_conversation(
        self,
        business_id: str,
        from_phone: str,
    ) -> Optional[SmsConversationLink]:
        link = self.get_sms_conversation(business_id, from_phone)
        try:
            self._client.delete(self._sms_key(business_id, from_phone))
        except Exception:
            logger.warning("redis_twilio_state_clear_sms_failed", exc_info=True)
        return link


def _create_twilio_state_store() -> TwilioStateStore:
    """Factory for the process-wide Twilio state store.

    Defaults to an in-memory implementation. When TWILIO_STATE_BACKEND=redis
    and Redis is available, a Redis-backed implementation is used instead.
    """
    settings = get_settings()
    backend = os.getenv("TWILIO_STATE_BACKEND", "memory").lower()
    if backend == "redis":
        if redis is None:
            logger.warning(
                "twilio_state_backend_redis_unavailable_falling_back",
                extra={"backend": backend},
            )
        else:
            try:
                redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
                client = redis.from_url(redis_url)  # type: ignore[attr-defined]
                return RedisTwilioStateStore(client)
            except Exception:
                logger.warning(
                    "twilio_state_backend_redis_init_failed_falling_back",
                    exc_info=True,
                )
    return InMemoryTwilioStateStore()


twilio_state_store: TwilioStateStore = _create_twilio_state_store()

