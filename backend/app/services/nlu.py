from __future__ import annotations

from typing import Optional

"""Lightweight NLU helpers for the Phase 1 conversation manager.

These helpers are intentionally simple and deterministic so they can be used
in safety-critical flows without introducing external dependencies.
"""


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

    This is deliberately tolerant but still conservative:
    - requires at least one digit (street number or ZIP code)
    - accepts common street suffixes or a comma-separated structure
    - accepts presence of a 5-digit ZIP even if suffix is missing
    """
    stripped = (text or "").strip()
    if not stripped:
        return None

    lower = stripped.lower()
    if not any(ch.isdigit() for ch in stripped):
        return None

    suffixes = {
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
        " hwy",
        " highway",
        " pkwy",
        " parkway",
        " ter",
        " terrace",
        " pl",
        " place",
    }
    has_suffix = any(suffix in lower for suffix in suffixes)
    has_comma = "," in stripped
    has_zip = any(ch.isdigit() for ch in stripped[-5:]) and any(
        part.isdigit() and len(part) == 5 for part in stripped.replace(",", " ").split()
    )
    looks_like_street_number = stripped[0].isdigit()

    if not (has_suffix or has_comma or has_zip or looks_like_street_number):
        return None

    # Normalize whitespace/punctuation lightly.
    normalized = " ".join(stripped.replace(" ,", ",").split())
    return normalized
