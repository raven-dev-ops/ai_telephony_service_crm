from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, asdict
from datetime import UTC, datetime
from typing import Any, Dict, List, Optional


@dataclass
class FeedbackEntry:
    created_at: datetime
    business_id: str
    category: str | None
    summary: str
    steps: str | None
    expected: str | None
    actual: str | None
    call_sid: str | None
    contact: str | None
    url: str | None
    user_agent: str | None


class FeedbackStore:
    """Thread-safe append-only feedback store with optional JSONL persistence."""

    def __init__(self, path: str | None = None) -> None:
        self._path = path or os.getenv("FEEDBACK_LOG_PATH", "feedback.jsonl")
        self._lock = threading.Lock()
        self._entries: List[FeedbackEntry] = []
        # Best-effort load existing entries if the file exists.
        if os.path.exists(self._path):
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    for line in f:
                        try:
                            obj = json.loads(line)
                            self._entries.append(
                                FeedbackEntry(
                                    created_at=datetime.fromisoformat(obj.get("created_at")),
                                    business_id=obj.get("business_id") or "unknown",
                                    category=obj.get("category"),
                                    summary=obj.get("summary") or "",
                                    steps=obj.get("steps"),
                                    expected=obj.get("expected"),
                                    actual=obj.get("actual"),
                                    call_sid=obj.get("call_sid"),
                                    contact=obj.get("contact"),
                                    url=obj.get("url"),
                                    user_agent=obj.get("user_agent"),
                                )
                            )
                        except Exception:
                            continue
            except Exception:
                # Ignore load failures to avoid blocking requests.
                self._entries = []

    def append(self, entry: FeedbackEntry) -> None:
        with self._lock:
            self._entries.append(entry)
            try:
                with open(self._path, "a", encoding="utf-8") as f:
                    serializable = asdict(entry)
                    serializable["created_at"] = entry.created_at.isoformat()
                    f.write(json.dumps(serializable) + "\n")
            except Exception:
                # Persistence failures are logged by caller if needed; do not raise.
                pass

    def list(
        self,
        *,
        business_id: str | None = None,
        since: datetime | None = None,
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        with self._lock:
            items = list(self._entries)
        if business_id:
            items = [e for e in items if e.business_id == business_id]
        if since:
            items = [e for e in items if e.created_at >= since]
        items.sort(key=lambda e: e.created_at, reverse=True)
        return [
            {
                "created_at": e.created_at.isoformat(),
                "business_id": e.business_id,
                "category": e.category,
                "summary": e.summary,
                "steps": e.steps,
                "expected": e.expected,
                "actual": e.actual,
                "call_sid": e.call_sid,
                "contact": e.contact,
                "url": e.url,
                "user_agent": e.user_agent,
            }
            for e in items[:limit]
        ]


feedback_store = FeedbackStore()
