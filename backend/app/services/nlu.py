from __future__ import annotations

from typing import Optional
import logging
import httpx

from ..config import get_settings

logger = logging.getLogger(__name__)

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


INTENT_LABELS = [
    "emergency",
    "schedule",
    "reschedule",
    "cancel",
    "faq",
    "greeting",
    "other",
]


def _heuristic_intent_with_score(text: str) -> tuple[str, float]:
    """Deterministic, keyword-driven intent classifier."""
    lower = (text or "").lower()
    if not lower:
        return "greeting", 0.4
    if any(k in lower for k in ["burst", "flood", "sewage", "gas leak", "no water"]):
        return "emergency", 0.95
    if any(k in lower for k in ["cancel", "canceling", "cancelling"]):
        return "cancel", 0.85
    if "resched" in lower or "change my time" in lower:
        return "reschedule", 0.85
    if any(k in lower for k in ["book", "schedule", "appointment", "available", "tomorrow"]):
        return "schedule", 0.8
    if any(k in lower for k in ["hours", "pricing", "quote", "estimate", "warranty", "guarantee"]):
        return "faq", 0.65
    if lower.strip() in {"hi", "hello", "hey"}:
        return "greeting", 0.45
    if lower.endswith("?"):
        return "faq", 0.55
    return "other", 0.4


async def _classify_with_llm(text: str) -> str | None:
    """LLM intent classifier for deployments that configure OpenAI."""
    settings = get_settings()
    speech = settings.speech
    if speech.provider != "openai" or not speech.openai_api_key:
        return None

    try:
        timeout = httpx.Timeout(6.0, connect=4.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            system_prompt = (
                "You classify caller utterances into intents for a plumbing booking assistant. "
                "Allowed intents: emergency, schedule, reschedule, cancel, faq, greeting, other. "
                "Return only the intent label."
            )
            payload = {
                "model": speech.openai_chat_model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": (text or "").strip()},
                ],
                "temperature": 0,
                "max_tokens": 4,
            }
            headers = {
                "Authorization": f"Bearer {speech.openai_api_key}",
                "Content-Type": "application/json",
            }
            url = f"{speech.openai_api_base}/chat/completions"
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            choice = data.get("choices", [{}])[0]
            content = choice.get("message", {}).get("content", "") or ""
            label = content.strip().split()[0].lower()
            return label if label in INTENT_LABELS else None
    except Exception:
        logger.debug("intent_llm_fallback_failed", exc_info=True)
    return None


async def classify_intent_with_metadata(
    text: str, business_id: str | None = None
) -> dict:
    """Return intent label with confidence and provider metadata."""
    intent, confidence = _heuristic_intent_with_score(text)
    chosen_provider = "heuristic"
    settings = get_settings()
    provider = getattr(settings.nlu, "intent_provider", "heuristic").lower()

    if provider == "openai":
        llm_label = await _classify_with_llm(text)
        if llm_label:
            intent = llm_label
            confidence = max(confidence, 0.85)
            chosen_provider = "openai"

    return {
        "intent": intent,
        "confidence": float(confidence),
        "provider": chosen_provider,
        "business_id": business_id,
    }


async def classify_intent(text: str) -> str:
    """Backward-compatible intent classifier that returns only the label."""
    meta = await classify_intent_with_metadata(text, None)
    return meta["intent"]
