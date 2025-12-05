AI Telephony Service & CRM for Trades
=====================================

This repository contains the design and a working prototype for an AI-powered telephony service and
lightweight CRM tailored to small trades businesses, using **Bristol Plumbing (Merriam, KS)** as
the reference customer. The assistant acts as a 24/7 virtual receptionist that can:

- Answer and triage inbound calls.
- Capture caller details and problem descriptions by voice or chat.
- Detect urgent plumbing emergencies (e.g., burst pipes, sewage backup).
- Schedule, modify, and cancel appointments against Google Calendar.
- Keep a searchable history of customers, jobs, and conversations.
- Notify the business by SMS about new leads, emergencies, and changes.

Quick Start (Development)
-------------------------

To run the backend and dashboards locally:

1. **Start the backend**
   - From the repo root:
     - `cd backend`
     - `python -m venv .venv && .venv\Scripts\activate` (Windows) or `python -m venv .venv && source .venv/bin/activate` (Unix)
     - `pip install -e .[dev]`
   - Option A (simple defaults):
     - `uvicorn app.main:app --reload`
   - Option B (using the provided env profiles from the repo root):
     - In-memory dev: `uvicorn app.main:app --reload --env-file ..\env.dev.inmemory`
     - DB-backed dev: `uvicorn app.main:app --reload --env-file ..\env.dev.db`

2. **Open the owner dashboard**
   - From the repo root, open `dashboard/index.html` in a browser (filesystem or a simple static server).
   - Set `X-API-Key` and `X-Owner-Token` in the header fields using the values from your tenant (`/v1/admin/businesses` or demo tenants).
   - Use the cards for **Tomorrow’s Schedule**, **Today’s Jobs**, **Emergency Jobs**, and **Recent Conversations** to verify behaviour.

3. **Open the admin dashboard (optional)**
   - Open `dashboard/admin.html` in a browser.
   - Enter `X-Admin-API-Key` and use the **Tenants**, **Tenant Usage**, and **Twilio / Webhook Health** cards to manage and inspect tenants.

4. **Explore the API**
   - Visit `http://localhost:8000/docs` for interactive docs.
   - See `API_REFERENCE.md` for a route-by-route summary grouped by area (voice, telephony, Twilio, CRM, owner, admin, reminders, retention, widget).


Source PDFs & Traceability
--------------------------

All system behavior, architecture, and engineering practices are grounded in the PDFs in the repo
root. This README is distilled from:

- `Bristol_Plumbing_Analysis.pdf` - business profile, services, marketing posture, and call patterns.
- `Bristol_Plumbing_Implementation.pdf` - AI assistant implementation concept, business strategy,
  and channel roadmap.
- `Bristol_Plumbing_Project_Plan.pdf` - feature set, phased requirements, and cloud architecture.
- `Project_Engineering_Whitepaper.pdf` - RavDevOps engineering culture and safety standard that
  governs code, operations, and testing.

Other root docs (`OUTLINE.md`, `BACKLOG.md`, `WIKI.md`, `SECURITY.md`, `PRIVACY_POLICY.md`,
`TERMS_OF_SERVICE.md`, `RUNBOOK.md`, `PILOT_RUNBOOK.md`, `DEPLOYMENT_CHECKLIST.md`) are similarly
derived from these PDFs and should be treated as the design ground truth. When in doubt about
behavior or trade-offs, prefer the intent described in these PDFs and keep changes boring,
observable, and easy to roll back, in line with the RavDevOps engineering standard.


Project Goals
-------------

The goals of this project are to:

- **Never miss a call or lead** for a one-person plumbing operation.
- **Automate scheduling** via natural voice interaction with Google Calendar integration.
- **Maintain a CRM-like history** of customers, jobs, and conversations in the cloud.
- **Provide a secure business dashboard** for reviewing logs, appointments, analytics, and QA.
- **Support emergency workflows**, tagging and escalating high-priority jobs.
- **Respect privacy and security**, aligning with the RavDevOps engineering standard.

Although the initial reference implementation is for Bristol Plumbing (Kansas City metro,
specializing in tankless water heaters and full-service plumbing), the architecture is designed to
generalize to other trades (HVAC, electrical, home services, etc.).


High-Level Capabilities
-----------------------

The system is designed to support:

- **Voice assistant for call handling**
  - Natural conversation to gather name, address, issue, and scheduling preferences.
  - Detection of emergencies (keywords such as "burst", "flood", "no water", "sewage").
  - Voice interaction for the owner (e.g., "What's on my schedule tomorrow?").

- **Scheduling & calendar integration**
  - Real-time availability checks against Google Calendar.
  - Appointment creation, updates, and cancellations.
  - Configurable default durations per service type (e.g., 1 hr faucet fix, 4 hr tankless install).

- **Customer data & CRM**
  - Cloud database of customers, service history, and appointments.
  - Recognition of repeat customers by phone or name with auto-population of details.
  - Linkage of conversations, jobs, and (in the Bristol example) external invoicing.

- **Business dashboard (web)**
  - Conversation log viewer for QA and training.
  - Calendar-style view and job list.
  - Basic analytics (jobs per week, revenue estimates, common service locations/services).
  - Configuration of business hours, services, and emergency rules.

- **Notifications & messaging**
  - SMS alerts to the owner for new leads and emergency calls.
  - SMS confirmations and reminders to customers.
  - Support for standard SMS opt-out keywords (e.g., STOP, STOPALL, UNSUBSCRIBE, CANCEL, END, QUIT)
    so customers can stop receiving text messages while owner alerts continue.
  - Simple retention SMS campaigns for lapsed customers via the `/v1/retention/send-retention` endpoint.

- **Future channels**
  - Website chatbot or voice widget using the same backend.
  - Multi-tenant support for multiple trades businesses.


Architecture Overview
---------------------

From `Bristol_Plumbing_Project_Plan.pdf`, the target architecture consists of:

- **Voice Assistant Backend (Python/FastAPI)**
  - REST API using FastAPI.
  - Speech-to-text (STT) using an accurate, low-latency model (e.g., Whisper or similar).
  - Text-to-speech (TTS) using a high-quality, near real-time model.
  - Dialogue / intent layer (currently rule-driven, with room for LLM-backed flows).
  - Business logic for scheduling, CRM operations, and emergency handling.
  - Integrations with Google Calendar, SMS gateway, and a cloud database.

- **Owner & Admin Dashboards (web, static HTML/JS)**
  - `dashboard/index.html`: owner dashboard for schedule, analytics, and QA.
  - `dashboard/admin.html`: admin dashboard for tenant management, usage, and Twilio/webhook health.
  - Both talk to the backend via JSON APIs using `X-API-Key`, `X-Owner-Token`, and `X-Admin-API-Key`.

- **Website widget**
  - `widget/chat.html` and `widget/embed.js` for a lightweight web chat experience.
  - Uses `X-Widget-Token` so tenants do not expose their `api_key` in browser code.

For more detail on phases, technical components, and engineering practices, see `OUTLINE.md`,
`ENGINEERING.md`, and `API_REFERENCE.md`.


Owner Dashboard
---------------

Open `dashboard/index.html` in a browser (either directly from the filesystem or via a static file
server such as `python -m http.server` from the repo root).

By default the owner dashboard targets a backend at `http://localhost:8000`. You can override this
for staging/production by:

- Passing a `backend_base` query parameter, e.g. `index.html?backend_base=https://api.example.com`.
- Defining `window.BACKEND_BASE_URL = "https://api.example.com"` before the dashboard script tag.

The owner dashboard primarily calls:

- `/v1/owner/schedule/tomorrow` and `/v1/owner/schedule/tomorrow/audio` - tomorrow's schedule
  (text + voice-friendly summary).
- `/v1/owner/summary/today` and `/v1/owner/summary/today/audio` - summary of today's jobs.
- `/v1/owner/reschedules` - appointments marked for rescheduling.
- `/v1/owner/sms-metrics` - SMS usage (owner vs. customer) for the current tenant.
- `/v1/owner/twilio-metrics` - Twilio webhook usage (voice/SMS requests and errors) for the
  current tenant.
- `/v1/owner/service-mix?days=N` - recent service mix (per service type, including emergency counts).
- `/v1/owner/export/service-mix.csv?days=N` - CSV export of service-mix for this tenant.
- `/v1/owner/export/conversations.csv?days=N` - CSV export of conversations for this tenant.
- `/v1/owner/business` - current tenant label (id/name) shown in the header.
- `/v1/crm/appointments` and `/v1/crm/conversations` - data for analytics and QA cards.

The dashboard is intentionally simple HTML+JS and uses `X-API-Key` and `X-Owner-Token` headers to
identify the tenant and authorize access.


Admin Dashboard
---------------

Open `dashboard/admin.html` in a browser to manage tenants and view platform-wide usage.

Configure:

- `ADMIN_API_KEY` in the backend environment and supply it as `X-Admin-API-Key` in the dashboard.

Key endpoints used:

- `/v1/admin/businesses` - list tenants and basic config.
- `/v1/admin/businesses/usage` and `/v1/admin/businesses/usage.csv` - per-tenant usage and CSV export,
  including counts of appointments, emergencies, SMS volume (owner vs. customer), pending
  reschedules, and service-type mix.
- `/v1/admin/twilio/health` - Twilio/webhook configuration and metrics, including per-tenant
  voice/SMS request and error counts.
- `PATCH /v1/admin/businesses/{business_id}` - update status and notification fields.
+- `POST /v1/admin/businesses/{business_id}/rotate-key` - rotate per-tenant API keys.
+- `POST /v1/admin/businesses/{business_id}/rotate-widget-token` - rotate per-tenant widget tokens.


Twilio, SMS & Multi-Tenant Notes
--------------------------------

- **Twilio Voice/SMS webhooks**
  - Voice: configure your Twilio number's Voice webhook to
    `POST https://<your-domain>/twilio/voice` (optionally with `?business_id=<tenant_id>` for
    per-tenant routing). The backend returns TwiML using `<Say>` + `<Gather input="speech">` so the
    assistant can carry a natural conversation.
  - SMS: configure the Messaging webhook to `POST https://<your-domain>/twilio/sms` (optionally
    with `?business_id=<tenant_id>`). Incoming SMS are attached to a conversation keyed by
    `(business_id, From)` and responded to via TwiML `<Message>...reply...</Message>`.

- **Customer SMS behavior**
  - Standard opt-out keywords (`STOP`, `STOPALL`, `UNSUBSCRIBE`, `CANCEL`, `END`, `QUIT`) mark a
    customer as opted out of customer-facing SMS for that tenant while leaving owner alerts
    unaffected.
  - Opt-in keywords (`START`, `UNSTOP`) clear the opt-out flag.
  - Appointment flows:
    - `YES` / `Y` / `CONFIRM` - confirm the next upcoming appointment.
    - `NO` / `N` / `CANCEL` - cancel the next upcoming appointment and attempt to remove the
      calendar event.
    - `RESCHEDULE` or similar phrases - mark the appointment as `PENDING_RESCHEDULE` for human
      follow-up.

- **Multi-tenant usage & metrics**
  - Each business (tenant) has:
    - An `api_key` used as `X-API-Key` on CRM/owner/admin calls.
    - A `widget_token` used as `X-Widget-Token` for the web chat widget.
    - A `status` (`ACTIVE` / `SUSPENDED`) that controls whether Twilio/CRM/widget traffic is
      served.
  - `/metrics` exposes:
    - Global counters (`total_requests`, `total_errors`, `appointments_scheduled`).
    - Global SMS counters and `sms_by_business[business_id]` for owner vs. customer SMS volume.
    - Global Twilio request/error counters and `twilio_by_business[business_id]` for per-tenant
      voice/SMS request and error counts.
    - Global voice-session counters and `voice_sessions_by_business[business_id]` for per-tenant
      voice-session health.


Where to Go Next
----------------

- For detailed architecture and phase breakdown, see `OUTLINE.md`.
- For backlog items and feature planning, see `BACKLOG.md`.
- For domain background and call-flow examples, see `WIKI.md`.
- For security expectations, see `SECURITY.md`.
- For privacy and terms baselines, see `PRIVACY_POLICY.md` and `TERMS_OF_SERVICE.md`.
- For day-to-day operations, see `RUNBOOK.md` and `PILOT_RUNBOOK.md`.
- For deployment steps, see `DEPLOYMENT_CHECKLIST.md`.
- For a step-by-step local dev workflow, see `DEV_WORKFLOW.md` and `TOOLS.md`.

All of these are anchored in the Bristol Plumbing PDFs and the RavDevOps engineering whitepaper.
Any implementation should treat them as the reference when making changes or adding new features.
