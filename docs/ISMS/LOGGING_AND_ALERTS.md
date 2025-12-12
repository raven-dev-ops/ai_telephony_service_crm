Logging, Monitoring, and Alert Rules
====================================

Central log pipeline
--------------------
- Emit structured key/value logs from backend (`backend/app/logging_config.py`) with request IDs; ship stdout to the log sink (e.g., Cloud Logging or Datadog). Set `LOG_FORMAT=json` for JSON output from the app if the collector does not normalize plain text.
- Normalize fields: `severity`, `tenant`, `request_id`, `call_sid`, `webhook_event`, `auth_principal`, `customer_phone`, `status`.
- Retention: 30 days for standard logs; 90 days for security/audit logs (webhooks/auth/access).

Alert rules (P0 focus)
----------------------
- **Twilio webhooks**: Error rate > 1% over 5 minutes OR signature verification failures > 5/min → page IC + owner email/SMS.
- **Scheduling/Calendar**: Consecutive failures writing events or zero available slots for active hours → page IC; include business id + requested slot window.
- **Auth**: JWT refresh failures > threshold, repeated login failures per IP/user > 10/min → alert and temporarily block IP/user.
- **Owner notifications**: SMS delivery failure without retry success, or fallback email used > 3 times in 15 minutes → notify owner + IC.
- **Stripe webhooks**: Signature verification failures or repeated 500s → alert and pause billing actions until resolved.

Implementation (stack)
----------------------
- **Cloud Logging (Cloud Run default)**: Backend stdout/err flows into Cloud Logging. Set env `LOG_FORMAT=json` in prod for structured payloads. Create logs-based metrics and alerting policies (examples below) via `gcloud` or the console.
  - Example logs-based metric filter (Twilio webhook failures):  
    `resource.type=\"cloud_run_revision\" AND labels.\"service_name\"=\"ai-telephony-backend\" AND textPayload:\"twilio_webhook_failure\"`
  - Alert policy CLI pattern (adjust metric and threshold):  
    `gcloud alpha monitoring policies create --display-name=\"P0 Twilio webhook failures\" --condition-display-name=\"webhook failures\" --condition-filter=\"metric.type=\"logging.googleapis.com/user/twilio_webhook_failure\"\" --condition-compare=COMPARISON_GT --condition-threshold-value=5 --condition-duration=300s --notification-channels=<channel-ids>`
- **Prometheus-style checks via GitHub Actions**: `.github/workflows/p0-alerts.yml` polls the `/metrics/prometheus` endpoint every 15 minutes. Configure repo secrets `METRICS_URL`, optional `METRICS_AUTH_HEADER`, thresholds (e.g., `TWILIO_WEBHOOK_THRESHOLD`, `CALENDAR_FAILURE_THRESHOLD`, `AUTH_FAILURE_THRESHOLD`, `OWNER_ALERT_FAILURE_THRESHOLD`), and optional `SLACK_WEBHOOK`. Workflow fails (and optionally posts to Slack) when thresholds are exceeded.
- **Cloud Run 5xx alert (created)**: Alert policy `P0 Cloud Run 5xx (backend)` watches `run.googleapis.com/request_count` for `service_name=ai-telephony-backend` with `response_code_class=5xx`, threshold >0 over 5m, notifying channel `P0 Alerts Email` (damon.heath@ravdevops.com). Adjust notification channels as needed.
- **Twilio webhook alert (created)**: Logs-based metric `twilio_webhook_failures` (resource.type=cloud_run_revision, service=ai-telephony-backend, textPayload contains twilio_webhook_failure) with alert policy `P0 Twilio webhook failure` (threshold >0, immediate) notifying `P0 Alerts Email`.
- **Cloud SQL backup alert (created)**: Logs-based metric `cloudsql_backup_errors` (resource.type=cloudsql_database, severity>=ERROR with backup text/protoPayload) and alert policy `P0 Cloud SQL backup error` (threshold >0, immediate) notifying `P0 Alerts Email`.

Dashboards
----------
- Create P0 dashboard panels for the above metrics; include red/amber/green thresholds and links to runbooks.
- Use existing metrics endpoints (`/metrics`) and owner notification status (`/v1/admin/owner-metrics`) as sources.

Runbooks and escalation
-----------------------
- Link alerts to playbooks in `INCIDENT_RESPONSE_PLAN.md` and `ai_telephony_service_crm.wiki.local/IncidentPlaybooks.md`.
- Escalation order: IC → backup engineer → product lead → executive sponsor.
- Post-alert actions: for each P0 alert, create an incident doc even if auto-recovered.