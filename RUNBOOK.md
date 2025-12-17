Operations Runbook
==================

This document provides a lightweight operational guide for running the AI Telephony Service & CRM
in a development, staging, or early pilot environment.


Source PDFs & Traceability
--------------------------

This runbook is informed by:

- `Bristol_Plumbing_Project_Plan.pdf` - deployment and operations requirements for the assistant.
- `Bristol_Plumbing_Implementation.pdf` - Twilio wiring, SMS behavior, and channel roadmap.
- `Project_Engineering_Whitepaper.pdf` - expectations for monitoring, incident response, and
  operational safety.


1. Services and Endpoints
-------------------------

- **Backend API** (`backend/`):
  - Health: `GET /healthz` should return `{"status": "ok"}`.
  - Metrics: `GET /metrics` returns JSON counters such as:
    - `total_requests`, `total_errors`
    - `appointments_scheduled`
    - `sms_sent_total`, `sms_sent_owner`, `sms_sent_customer`
    - `sms_by_business[business_id].sms_sent_total/sms_sent_owner/sms_sent_customer`
    - Per-tenant SMS events such as `sms_confirmations_via_sms`, `sms_cancellations_via_sms`,
      `sms_reschedules_via_sms`, `sms_opt_out_events`, and `sms_opt_in_events`.
    - `twilio_voice_requests`, `twilio_voice_errors`
    - `twilio_sms_requests`, `twilio_sms_errors`
    - `twilio_by_business[business_id].voice_requests/voice_errors/sms_requests/sms_errors`
  - Voice & telephony:
    - `/v1/voice/session/*` - direct voice session API.
    - `/telephony/*` - webhook-style entry for a telephony provider.
  - CRM & owner views:
    - `/v1/crm/*` - customers and appointments.
    - `/v1/owner/schedule/tomorrow` - text summary for the owner's schedule.
    - `/v1/owner/schedule/tomorrow/audio` - same, plus synthesized audio token.
    - `/v1/owner/summary/today` and `/v1/owner/summary/today/audio` - today's jobs summary.
    - `/v1/owner/reschedules` - list appointments marked `PENDING_RESCHEDULE`.
    - `/v1/owner/sms-metrics` - SMS usage for the current tenant (owner vs. customer).
    - `/v1/owner/twilio-metrics` - Twilio webhook usage (voice/SMS requests and errors) for the current tenant.
    - `/v1/owner/service-mix` - recent service mix per service type (including emergency counts); supports `?days=N` (default 30, max 90).
    - `/v1/owner/export/service-mix.csv` and `/v1/owner/export/conversations.csv` - owner-scoped CSV exports for service mix and conversations; both accept the same `?days=N` window parameter (default 30).
    - `/v1/owner/business` - current tenant metadata (id/name) for the dashboard header.
  - Prometheus view:
    - `GET /metrics/prometheus` - minimal text-format metrics suitable for Prometheus scraping, including global counters and per-path request/error counts.
  - Reminders:
    - `POST /v1/reminders/send-upcoming?hours_ahead=24` - sends SMS reminders for upcoming
      appointments in the specified window.
  - Retention & follow-ups:
    - `POST /v1/reminders/send-followups` - sends follow-up SMS to recent leads that have not yet
      booked an appointment, updating `lead_followups_sent` metrics for the tenant.
    - `POST /v1/retention/send-retention` - sends simple retention campaigns to past customers
      whose last visit was more than `min_days_since_last_visit` days ago and who have no future
      appointment. Campaign counts are tracked in `metrics.retention_by_business` and surfaced via
      the owner retention summary endpoint.

- **Owner Dashboard** (`dashboard/index.html`):
  - Static HTML/JS that calls the backend APIs for schedules, customers, analytics, and recent
    conversations for a single tenant.

- **Admin Dashboard** (`dashboard/admin.html`):
  - Static HTML/JS that calls admin APIs (`/v1/admin/*`) for listing and editing tenants,
    viewing per-tenant usage, and inspecting Twilio/webhook health.


2. Normal Operations
--------------------

- **Start backend**:
  - From `backend/`:
    - Create a virtualenv and install: `pip install -e .`
    - Run: `uvicorn app.main:app --reload`
  - Confirm:
    - `GET http://localhost:8000/healthz`
    - `GET http://localhost:8000/metrics`

- **Start dashboard**:
  - Open `dashboard/index.html` directly in a browser, or serve the repo root with a simple static
    file server and navigate to the dashboard file.

- **SMS and calendar integrations**:
  - The system runs safely in stub mode when calendar or SMS credentials are not configured.
  - For a pilot, configure:
    - Google Calendar service account and `GOOGLE_CALENDAR_*` env vars.
    - Twilio credentials and `SMS_*` / `TWILIO_*` env vars.
  - For production hardening, also configure:
    - `ADMIN_API_KEY` for securing `/v1/admin/*` routes (pass via `X-Admin-API-Key`).
    - `REQUIRE_BUSINESS_API_KEY=true` so multi-tenant traffic is always scoped by a tenant identifier
      (`X-API-Key`, `X-Widget-Token`, or `X-Business-ID`) instead of defaulting to a shared tenant.


3. Reminders & Scheduling Integration
-------------------------------------

The reminders endpoint is designed to be triggered by a scheduler or cron job:

- Endpoint: `POST /v1/reminders/send-upcoming?hours_ahead=24`
  - Scans for appointments starting between `now` and `now + hours_ahead`.
  - Sends SMS reminders to customers with a known phone number who have not opted out of SMS.
  - Marks appointments as `reminder_sent` to avoid duplicate reminders.

Examples:

- **Local cron job (single tenant)**:
  - Run every hour to send 24-hour reminders:
    - `0 * * * * curl -X POST http://localhost:8000/v1/reminders/send-upcoming?hours_ahead=24`

- **Cron with tenant API key (multi-tenant)**:
  - Include `X-API-Key` for the business:
    - `0 * * * * curl -X POST \`
      `  -H "X-API-Key: <BUSINESS_API_KEY>" \`
      `  http://localhost:8000/v1/reminders/send-upcoming?hours_ahead=24`

- **Cloud Scheduler (GCP)**:
  - Create an HTTP target that POSTs to the service URL:
    - `https://<cloud-run-url>/v1/reminders/send-upcoming?hours_ahead=24`
  - Add an `X-API-Key` header if tenant auth is enabled.

In addition to reminders, the retention endpoint supports simple win-back campaigns:

- Endpoint: `POST /v1/retention/send-retention`
  - Targets past customers whose last appointment is at least `min_days_since_last_visit` days ago and who do not have a future `SCHEDULED`/`CONFIRMED` appointment.
  - Respects per-tenant language and vertical settings and skips numbers marked `sms_opt_out`.
  - Key query parameters include:
    - `min_days_since_last_visit` (default 180, range 30–1095).
    - `max_messages` (default 50) to cap sends per call.
    - `campaign_type`, `service_type`, and `tag` for basic segmentation.
- Schedule this similarly to reminders (for example, a weekly job per tenant with `X-API-Key`). 

Operational tips:

- Start with `hours_ahead=24` and adjust based on business preference (e.g., 24-48 hours).
- Monitor SMS volume (`sms_sent_customer` in `/metrics`) to confirm reminders are sending as
  expected.
- Ensure Twilio/SMS credentials are configured and that marketing/notification policies comply with
  local regulations.
- Remember that customers who have texted standard opt-out keywords (e.g., STOP, STOPALL,
  UNSUBSCRIBE, CANCEL, END, QUIT) will not receive customer-facing SMS such as reminders.


4. Twilio Setup & Pricing (Operations Notes)
-------------------------------------------

- **Voice webhook**:
  - In Twilio, set the phone number's Voice webhook to
    `POST https://<your-domain>/twilio/voice` (single tenant) or
    `POST https://<your-domain>/twilio/voice?business_id=demo_alpha` (per-tenant).
  - Expect Twilio to send `CallSid`, `From`, `CallStatus`, and `SpeechResult`; the backend returns
    TwiML `<Say>` + `<Gather input="speech" action="/twilio/voice" method="POST">`.

- **SMS webhook**:
  - Set the Messaging webhook to `POST https://<your-domain>/twilio/sms` (single tenant) or
    `POST https://<your-domain>/twilio/sms?business_id=demo_alpha` for a specific tenant.
  - Each SMS is mapped into a conversation keyed by `(business_id, From)` and replied to via TwiML
    `<Message>...assistant reply...</Message>`.
  - If the inbound message body matches a standard opt-out keyword (STOP, STOPALL, UNSUBSCRIBE,
    CANCEL, END, QUIT), the system:
    - Marks that phone number as opted out of customer SMS for the relevant business.
    - Returns a confirmation message and does **not** forward that message into the normal assistant
      conversation flow.

- **Operational checks**:
  - If Twilio webhooks fail, you will see increased errors on `/metrics` and 4xx/5xx statuses for
    `/twilio/*` in logs.
  - Confirm webhook URLs and credentials if owner alerts or confirmations stop arriving.
  - For an aggregated view of voice/SMS request and error counts (global and per tenant), use
    `GET /v1/admin/twilio/health` from the admin dashboard.

- **Cost awareness**:
  - Numbers are typically charged monthly; voice is billed per minute and SMS per message.
  - Monitor usage in the Twilio Console; unexpected spikes can indicate abuse, misconfiguration, or
    bots hitting your numbers.


5. Multi-Tenant Demo (Operations Recipe)
----------------------------------------

- **Seed demo tenants**:
  - Once the backend and database are running, call:
    - `POST http://localhost:8000/v1/admin/demo-tenants`
  - Capture the returned `api_key` values for `demo_alpha` and `demo_beta`.

- **Call CRM/owner APIs per tenant**:
  - For any CRM or owner endpoint (for example, `GET /v1/crm/customers` or
    `GET /v1/crm/appointments`), include:
    - `X-API-Key: <demo_alpha_api_key>` or `X-API-Key: <demo_beta_api_key>`
  - Confirm that each key only returns data for its own tenant.

- **Widget/Twilio per tenant**:
  - For the web widget or Twilio webhooks, ensure requests carry the correct `X-API-Key` (or, in a
    production deployment, are routed per-tenant behind an auth layer).
  - Use logs and `/metrics` to confirm that traffic for each tenant is being handled and counted
    separately.


6. Local Dev Profiles (Operator Notes)
--------------------------------------

Operators and engineers often need two flavours of local environment:

- **In-memory dev**
  - Uses an environment file like `env.dev.inmemory` with:
    - `USE_DB_CUSTOMERS=false`, `USE_DB_APPOINTMENTS=false`, `USE_DB_CONVERSATIONS=false`.
    - `SMS_PROVIDER=stub`, `CALENDAR_USE_STUB=true`.
    - `REQUIRE_BUSINESS_API_KEY=false` (single-tenant default).
  - Good for quickly reproducing bugs in conversation flows or Twilio/webhook handlers without
    worrying about persistent data.

- **DB-backed dev**
  - Uses an environment file like `env.dev.db` with:
    - `DATABASE_URL=sqlite:///./app.db`.
    - `USE_DB_CUSTOMERS=true`, `USE_DB_APPOINTMENTS=true`, `USE_DB_CONVERSATIONS=true`.
    - `REQUIRE_BUSINESS_API_KEY=true`, `ADMIN_API_KEY`, `OWNER_DASHBOARD_TOKEN`.
    - `SMS_PROVIDER=stub`, `CALENDAR_USE_STUB=true`.
  - Matches staging/production behaviour more closely for tenant admin, dashboards, usage metrics,
    and Twilio/webhook health.

Switch between these profiles by changing the `--env-file` passed to `uvicorn` from `backend/`.


6. Monitoring & Alerts (Guidance)
---------------------------------

For a production deployment, integrate these signals with your monitoring stack:

- Export `/metrics` to a metrics system such as Prometheus; create dashboards for:
  - Request volume and error rate over time.
  - Appointments scheduled per day.
  - SMS volume (owner vs. customer).

Example Prometheus scrape config (replace host/port as needed):

```yaml
scrape_configs:
  - job_name: "ai-telephony-backend"
    metrics_path: /metrics
    static_configs:
      - targets: ["backend-service.default.svc.cluster.local:8000"]
```

Because `/metrics` returns JSON counters rather than native Prometheus exposition format, you can:

- Use a sidecar or gateway to translate JSON into Prometheus metrics, or
- Ingest `/metrics` into your existing logging/metrics pipeline and build charts on fields:
  - `total_requests`, `total_errors`
  - `appointments_scheduled`
  - `sms_sent_total`, `sms_sent_owner`, `sms_sent_customer`
  - `twilio_voice_requests`, `twilio_voice_errors`
  - `twilio_sms_requests`, `twilio_sms_errors`
  - `voice_session_requests`, `voice_session_errors`
  - Per-tenant breakdowns in `sms_by_business`, `twilio_by_business`, and `voice_sessions_by_business`

Useful dashboard views:

- Error rate: plot `total_errors` as a rate over time and compare to `total_requests`.
- Booking volume: plot `appointments_scheduled` as a rate per hour/day.
- SMS behaviour: plot `sms_sent_owner` vs. `sms_sent_customer` to spot anomalies.
- Twilio/Webhook health: plot `twilio_voice_errors`/`twilio_sms_errors` and per-tenant
  `twilio_by_business[business_id].voice_errors` / `.sms_errors` alongside request volume.
- Voice session health: plot `voice_session_errors` and per-tenant
  `voice_sessions_by_business[business_id].errors` to catch issues in `/v1/voice/session/*` flows.

Configure alerts for:

- High error rate (e.g., `total_errors` increasing faster than normal).
- Drop in `appointments_scheduled` compared to historical baselines.
- Sudden spike or drop in SMS volume (could indicate integration issues).
- Twilio/webhook issues, for example:
  - Voice: alert when the ratio `twilio_voice_errors / max(twilio_voice_requests, 1)` exceeds a
    small threshold for several minutes.
  - SMS: similarly for `twilio_sms_errors / max(twilio_sms_requests, 1)`.
- Voice session issues, for example:
  - Alert when `voice_session_errors / max(voice_session_requests, 1)` exceeds a threshold, or when
    per-tenant `voice_sessions_by_business[business_id].errors` spikes.

Example Prometheus-style alerts (schematic):

```yaml
groups:
  - name: ai-telephony-alerts
    rules:
      - alert: HighTwilioVoiceErrorRate
        expr: (twilio_voice_errors / clamp_max(twilio_voice_requests, 1)) > 0.05
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "High Twilio voice error rate"
          description: "Voice webhook errors >5% for 5m."

      - alert: HighVoiceSessionErrorRate
        expr: (voice_session_errors / clamp_max(voice_session_requests, 1)) > 0.05
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "High voice session error rate"
          description: "Voice session API errors >5% for 5m."
```

- **Log aggregation**:
  - Backend logs are emitted to stdout by default via `logging_config.configure_logging`.
  - Ship logs to a central store (e.g., Cloud Logging, ELK, or similar) and index by:
    - Service name.
    - Request path and status.
    - Error stack traces for investigation.


7. Performance & Load Testing
-----------------------------

For early-stage and staging environments, you can use the provided load-test script in `backend/`
to exercise the voice and telephony APIs:

- Script: `backend/load_test_voice.py`
- Requirements:
  - Backend running (for example, `uvicorn app.main:app --reload --env-file ..\env.dev.db`).
  - Python venv for `backend` with `pip install -e .[dev]`.

Examples (from repo root, backend on `http://localhost:8000`):

- **Voice session API (`/v1/voice/session/*`)**

  ```bash
  cd backend
  python load_test_voice.py --mode voice --sessions 50 --concurrency 10 \
    --backend http://localhost:8000 \
    --api-key YOUR_API_KEY --business-id YOUR_BUSINESS_ID
  ```

- **Telephony API (`/telephony/*`)**

  ```bash
  cd backend
  python load_test_voice.py --mode telephony --sessions 50 --concurrency 10 \
    --backend http://localhost:8000 \
    --api-key YOUR_API_KEY --business-id YOUR_BUSINESS_ID
  ```

The script prints:

- Total sessions, completed sessions, errors, and wall-clock time.
- Per-session latency (start + 3 conversational turns + end): average, p50, p95, and p99.

Correlate these runs with `/metrics`:

- `voice_session_requests` / `voice_session_errors` and `voice_sessions_by_business`.
- `twilio_voice_requests` / `twilio_voice_errors` (for telephony-style flows).
- `route_metrics["/v1/voice/session/input"].max_latency_ms` and similar per-path metrics.

Use these observations to refine timeouts, retry policies, and capacity assumptions before
onboarding higher-volume tenants.


8. Incident Response
--------------------

When something goes wrong:

1. **Triage**
   - Check `/healthz` and `/metrics` to understand whether the service is up and if errors are
     increasing.
   - Inspect recent logs for stack traces or upstream API failures (e.g., Calendar or SMS provider).

2. **Containment**
   - If an external dependency is down (Calendar, SMS), decide whether to:
     - Degrade gracefully (continue to accept calls but defer scheduling).
     - Temporarily stop sending notifications.
   - Communicate with the business owner if call handling or scheduling is impacted.

3. **Remediation**
   - Fix configuration issues (credentials, env vars) or roll back recent changes when appropriate.
   - For external outages, monitor provider status and retry once service is restored.

4. **Postmortem**
   - Capture a short, blameless write-up:
     - What happened (symptoms and timeline).
     - User impact (missed calls, notifications, or schedules).
     - Root cause (internal bug vs. external dependency).
     - What went well and what could improve.
     - Concrete follow-up actions (tests, alerts, documentation).


9. Debugging & Troubleshooting
------------------------------

This section highlights common misconfigurations and how to spot them using logs and metrics.

- **Calendar issues**
  - Symptoms:
    - Scheduling calls work but no real calendar events appear.
    - Owner summary endpoints work in stub mode only.
  - Checks:
    - Verify `GOOGLE_CALENDAR_ID`, `GOOGLE_CALENDAR_CREDENTIALS_FILE`, and `CALENDAR_USE_STUB` in
      the environment (see `DEPLOYMENT_CHECKLIST.md`).
    - Inspect logs for Google API errors in the calendar service.
  - Expected behaviour:
    - With `CALENDAR_USE_STUB=true`, the backend synthesizes slots and uses placeholder
      `event_placeholder_*` IDs without calling Google.
    - With `CALENDAR_USE_STUB=false` and valid credentials, real events are created/updated; HTTP
      errors from Google are caught and logged.

- **Twilio voice/webhook issues**
  - Symptoms:
    - Twilio Console shows webhook failures.
    - Callers hear generic Twilio error messages instead of the assistant.
  - Checks:
    - Confirm the Voice URL is set to `POST https://<your-domain>/twilio/voice` (and, if used, that
      `?business_id=<tenant_id>` is correct).
    - Check `/metrics` for spikes in `twilio_voice_errors` and per-tenant
      `twilio_by_business[business_id].voice_errors`.
    - Look for `twilio_signature_missing` / `twilio_signature_invalid` or
      `twilio_voice_unhandled_error` log entries.
  - Fixes:
    - Ensure `TWILIO_AUTH_TOKEN` and `VERIFY_TWILIO_SIGNATURES` are configured correctly.
    - Confirm the backend is reachable from Twilio (network/firewall/HTTPS).

- **Twilio SMS issues**
  - Symptoms:
    - SMS reminders or responses do not reach customers.
    - Opt-out keywords do not appear to be honoured.
  - Checks:
    - Confirm Messaging webhook is set to `POST https://<your-domain>/twilio/sms` (with a correct
      `business_id` when used).
    - Check `/metrics` for `twilio_sms_requests` vs `twilio_sms_errors` and per-tenant
      `twilio_by_business[business_id].sms_errors`.
    - Inspect logs for `twilio_sms_unhandled_error` and opt-out handling messages.
  - Notes:
    - Customers flagged with `sms_opt_out=true` will not receive reminders or retention SMS, but
      owner alerts continue unaffected.

- **Tenant auth & dashboard access**
  - Symptoms:
    - Dashboards show "unauthorized" or cannot load data for a tenant.
  - Checks:
    - Confirm `REQUIRE_BUSINESS_API_KEY` is set as expected for the environment.
    - Verify that `X-API-Key`, `X-Owner-Token`, and `X-Admin-API-Key` values match those returned
      by `/v1/admin/businesses`.
    - For multi-tenant environments, ensure requests include either `X-API-Key` or `X-Business-ID`.


9. Security & Compliance Notes
------------------------------

- **Logs and metrics**:
  - Avoid logging sensitive personal data (full transcripts, secrets, or full addresses) in
    production. Use IDs and high-level summaries instead.
  - Secrets should be stored in secure secret managers, not in code or plain-text logs.

- **SMS and telephony**:
  - Ensure you comply with local regulations for SMS communications (opt-in, opt-out, marketing vs.
    transactional messaging).
  - Periodically verify that opt-out handling is functioning as expected by testing STOP/UNSUBSCRIBE
    flows in a non-production tenant and confirming that reminders and other customer SMS are
    suppressed for opted-out numbers.
  - For call recording or detailed transcript storage, ensure consent and retention policies are
    clearly defined and documented.

- **Data access**:
  - Protect access to operational endpoints (`/metrics`, `/v1/reminders/*`, CRM routes) behind
    authentication and network controls in production.
