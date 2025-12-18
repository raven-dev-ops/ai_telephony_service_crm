from __future__ import annotations

import logging
import os
import httpx
from datetime import datetime, timedelta, timezone
from typing import Dict

from ..metrics import metrics

logger = logging.getLogger(__name__)

# Simple SLO targets used for dashboards/runbooks.
SLO_TARGETS: Dict[str, float] = {
    "uptime": 0.995,  # 99.5%
    "booking_success_rate": 0.95,
    "emergency_notify_p95_ms": 60000.0,
}

# Runbook links for P0s; these are durable URLs owned by the team.
RUNBOOK_LINKS: Dict[str, str] = {
    "twilio_webhook_failure": "https://github.com/raven-dev-ops/ai_telephony_service_crm/wiki/Runbooks#twilio-webhooks",
    "calendar_webhook_failure": "https://github.com/raven-dev-ops/ai_telephony_service_crm/wiki/Runbooks#calendar-sync",
    "notification_failure": "https://github.com/raven-dev-ops/ai_telephony_service_crm/wiki/Runbooks#owner-notifications",
    "uptime_slo": "https://github.com/raven-dev-ops/ai_telephony_service_crm/wiki/Runbooks#uptime",
    "booking_slo": "https://github.com/raven-dev-ops/ai_telephony_service_crm/wiki/Runbooks#booking-success",
    "emergency_latency_slo": "https://github.com/raven-dev-ops/ai_telephony_service_crm/wiki/Runbooks#emergency-latency",
}


def _should_fire(key: str, cooldown_seconds: int) -> bool:
    last = metrics.alert_last_fired.get(key)
    if not last:
        return True
    try:
        last_dt = datetime.fromisoformat(last)
    except Exception:
        return True
    return datetime.now(timezone.utc) - last_dt >= timedelta(seconds=cooldown_seconds)


def _record_alert(key: str, detail: str, severity: str, runbook: str) -> None:
    now = datetime.now(timezone.utc)
    existing = metrics.alerts_open.get(key) or {}
    occurrences = existing.get("occurrences", 0) + 1
    metrics.alerts_open[key] = {
        "key": key,
        "severity": severity,
        "detail": detail,
        "runbook": runbook,
        "first_triggered": existing.get("first_triggered", now.isoformat()),
        "last_triggered": now.isoformat(),
        "occurrences": occurrences,
    }
    metrics.alert_events_total += 1
    metrics.alert_last_fired[key] = now.isoformat()
    webhook = os.getenv("ONCALL_WEBHOOK_URL")
    if webhook:
        payload = {"text": f"[{severity}] {key}: {detail} | runbook={runbook or 'n/a'}"}
        try:
            httpx.post(webhook, json=payload, timeout=3.0)
        except Exception:
            logger.warning(
                "oncall_webhook_failed",
                exc_info=True,
                extra={"key": key, "severity": severity},
            )
    logger.warning(
        "p0_alert_triggered",
        extra={
            "key": key,
            "severity": severity,
            "runbook": runbook,
            "detail": detail,
            "occurrences": occurrences,
        },
    )


def maybe_trigger_alert(
    key: str,
    *,
    detail: str,
    severity: str = "P0",
    cooldown_seconds: int = 300,
    runbook_key: str | None = None,
) -> bool:
    """Trigger a P0/P1 alert with cooldown and runbook pointer."""

    if not _should_fire(key, cooldown_seconds=cooldown_seconds):
        return False
    rb = RUNBOOK_LINKS.get(runbook_key or key, "")
    _record_alert(key, detail=detail, severity=severity, runbook=rb)
    return True


def record_notification_failure(channel: str, detail: str) -> None:
    """Normalize notification failures with a shared P0 alert."""

    metrics.notification_failures += 1
    maybe_trigger_alert(
        "notification_failure",
        detail=f"{channel} notification failure: {detail}",
        severity="P0",
        cooldown_seconds=120,
        runbook_key="notification_failure",
    )
