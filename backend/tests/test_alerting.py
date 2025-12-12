from fastapi import HTTPException
from fastapi.testclient import TestClient

import app.main as main
from app.metrics import metrics
from app.services import alerting


def _reset_metrics() -> None:
    metrics.total_requests = 0
    metrics.total_errors = 0
    metrics.twilio_webhook_failures = 0
    metrics.calendar_webhook_failures = 0
    metrics.notification_attempts = 0
    metrics.notification_failures = 0
    metrics.alert_events_total = 0
    metrics.alerts_open.clear()
    metrics.alert_last_fired.clear()


def test_alerting_records_runbook_and_cooldown() -> None:
    _reset_metrics()
    fired = alerting.maybe_trigger_alert(
        "twilio_webhook_failure",
        detail="first failure",
        cooldown_seconds=0,
    )
    assert fired
    assert metrics.alert_events_total == 1
    assert "twilio_webhook_failure" in metrics.alerts_open
    assert metrics.alerts_open["twilio_webhook_failure"]["runbook"]

    fired_again = alerting.maybe_trigger_alert(
        "twilio_webhook_failure",
        detail="second failure",
        cooldown_seconds=9999,
    )
    assert not fired_again
    assert metrics.alert_events_total == 1


def test_twilio_failure_increments_metrics_and_alerts() -> None:
    _reset_metrics()
    app = main.create_app()

    @app.post("/twilio/testfail")
    async def _twilio_fail():
        raise HTTPException(status_code=503, detail="boom")

    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post("/twilio/testfail")
    assert resp.status_code == 503
    assert metrics.twilio_webhook_failures == 1
    assert "twilio_webhook_failure" in metrics.alerts_open
    alert = metrics.alerts_open["twilio_webhook_failure"]
    assert alert["runbook"]
    assert alert["severity"] == "P0"


def test_notification_failure_helper_flags_alert() -> None:
    _reset_metrics()
    alerting.record_notification_failure("sms", "downstream error")
    assert metrics.notification_failures == 1
    assert "notification_failure" in metrics.alerts_open
