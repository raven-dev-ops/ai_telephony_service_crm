from __future__ import annotations

"""Lightweight NLU helpers for the Phase 1 conversation manager.

These helpers are intentionally simple and deterministic so they can be used
in safety-critical flows without introducing external dependencies.
"""

from typing import Optional


def parse_name(text: str) -> Optional[str]:
    """Best-effort extraction of a caller name from free-form input.

    Handles simple lead-in phrases such as "my name is Jane Doe" or "this is
    John" and falls back to treating reasonably short phrases as names.
    """
    stripped = (text or "").strip()
    if not stripped:
        return None

    lower = stripped.lower()
    prefixes = [
        "my name is",
        "this is",
        "i am",
        "i'm",
    ]
    for prefix in prefixes:
        if lower.startswith(prefix):
            candidate = stripped[len(prefix) :].strip(" ,.")
            if candidate:
                return candidate

    # Fallback: treat short phrases with at least one space as names.
    if 0 < len(stripped) <= 40 and any(ch.isspace() for ch in stripped):
        return stripped

    return None


def parse_address(text: str) -> Optional[str]:
    """Best-effort extraction of a street-style address.

    Looks for text containing at least one digit and a common street suffix.
    This is deliberately conservative and will return None when uncertain.
    """
    stripped = (text or "").strip()
    if not stripped:
        return None

    lower = stripped.lower()
    if not any(ch.isdigit() for ch in stripped):
        return None

    suffixes = [
        " st",
        " street",
        " ave",
        " avenue",
        " rd",
        " road",
        " blvd",
        " boulevard",
        " dr",
        " drive",
        " ln",
        " lane",
        " ct",
        " court",
    ]
    if not any(suffix in lower for suffix in suffixes):
        return None

    return stripped

