Engineering Overview
=====================

This document gives engineers a high-level view of how the AI Telephony Service & CRM is structured
and which paths are most critical to keep stable and well-tested.


Source PDFs & Alignment
-----------------------

The architecture and behavior here are grounded in:

- `Bristol_Plumbing_Analysis.pdf` - business context, services, and call patterns.
- `Bristol_Plumbing_Implementation.pdf` - AI receptionist concept, channels, and owner UX.
- `Bristol_Plumbing_Project_Plan.pdf` - phased feature plan and target cloud architecture.
- `Project_Engineering_Whitepaper.pdf` - RavDevOps engineering culture and safety rules.

When in doubt, prefer the intent in these PDFs and the root docs (`README.md`, `OUTLINE.md`,
`BACKLOG.md`, `WIKI.md`, `SECURITY.md`, `RUNBOOK.md`) over ad hoc changes.


System Architecture (Code)
--------------------------

- **Backend service (`backend/app`)**
  - `main.py`:
    - Creates the FastAPI app.
    - Wires HTTP middleware for metrics (`/metrics`) and health (`/healthz`).
    - Registers routers:
      - `/v1/voice` (`routers/voice.py`) - generic voice session API.
      - `/telephony` (`routers/telephony.py`) - provider-agnostic telephony entry.
      - `/twilio` (`routers/twilio_integration.py`) - Twilio Voice/SMS webhooks.
      - `/v1/crm` (`routers/crm.py`) - customers, appointments, conversations.
      - `/v1/owner` (`routers/owner.py`) - owner schedule and summaries.
      - `/v1/reminders` (`routers/reminders.py`) - SMS reminder jobs.
      - `/v1/widget` (`routers/chat_widget.py`) - web chat widget backend.
      - `/v1/admin` (`routers/business_admin.py`) - tenant admin and usage.
  - `db.py`, `db_models.py`:
    - SQLAlchemy setup when available.
    - Core tables (or typed placeholders when disabled):
      - `Business` - tenant config, API keys, widget tokens, status, safety settings.
      - `CustomerDB` - customer records, including `sms_opt_out`.
      - `AppointmentDB` - jobs/appointments with emergency flag and calendar link.
      - `ConversationDB`, `ConversationMessageDB` - conversation threads and messages.
  - `deps.py`:
    - Request-scoped dependencies for resolving `business_id`, enforcing API keys, and checking
      tenant status (e.g., `ensure_business_active`, owner dashboard auth, admin auth).
  - `repositories.py`:
    - Data access layer for customers, appointments, conversations, and business records.
  - `services/conversation.py` and `services/sessions.py`:
    - Conversation manager and call session handling.
    - Business logic for call flows, emergency detection, appointment creation, and owner SMS.
  - `metrics.py`:
    - In-process counters for global and per-tenant activity (`/metrics`).
  - `services/sessions.py` and Twilio maps:
    - In-memory call session store and Twilio CallSid / SMS conversation maps.
    - These are suitable for early development and single-process deployments; see notes below for
      scaling considerations.

- **Dashboard (`dashboard/`)**
  - `index.html`:
    - Owner-focused dashboard that calls backend APIs (schedule, today summary, analytics,
      conversations) using `X-API-Key` and `X-Owner-Token`.
    - Intended to stay thin: no business rules beyond presentation.

- **Widget (`widget/`)**
  - `chat.html`, `embed.js`:
    - Web chat surface that talks to the backend with `X-Widget-Token`, allowing tenants to embed
      the assistant on their sites without exposing `api_key`.

For a quick, entity-focused view of how core tables relate (Business, Customer, Appointment,
Conversation, ConversationMessage), see `DATA_MODEL.md`.


Multi-Tenant Model
------------------

Tenant identity is resolved via:

- `Business.api_key`:
  - Provided as `X-API-Key` on CRM, owner, reminders, and most widget/telephony flows.
- `Business.widget_token`:
  - Provided as `X-Widget-Token` on widget-backed conversations.
- `X-Business-ID`:
  - Trusted only in controlled environments (single-tenant or internal calls).

Key points:

- `deps.py` enforces `REQUIRE_BUSINESS_API_KEY` when configured, preventing silent fallback to a
  default tenant.
- `Business.status` (`ACTIVE` / `SUSPENDED`) controls whether Twilio/CRM/widget routes should
  serve traffic for a tenant.
- All CRM records (`CustomerDB`, `AppointmentDB`, `ConversationDB`) carry a `business_id` for
  isolation.

The default calendar ID used by the backend is taken from the `GOOGLE_CALENDAR_ID` environment
variable and can be overridden per tenant via the `Business.calendar_id` field.


Critical Paths (Be Extra Careful Here)
--------------------------------------

Changes in these areas must be made cautiously and with tests:

- **Emergency handling & call flows**
  - `services/conversation.py`:
    - Greeting and safety messaging (e.g., instructing callers to contact 911 themselves for
      life-threatening emergencies).
    - Emergency keyword detection and tagging (`is_emergency` flag on appointments).
    - Owner SMS content for emergency vs. standard jobs.
  - Tests: `backend/tests/test_conversation.py` and owner schedule tests around emergency counts.

- **Owner schedule & summaries**
  - `routers/owner.py`:
    - `/v1/owner/schedule/tomorrow` (text and audio variants).
    - `/v1/owner/summary/today` (counts and reply text).
  - Any change should keep responses voice-friendly and consistent with dashboard expectations and
    tests in `backend/tests/test_owner_schedule.py`.

- **Twilio Voice/SMS webhooks**
  - `routers/twilio_integration.py`:
    - Voice flow: TwiML generation, conversation session mapping, per-tenant metrics, and
      early rejection for suspended tenants.
    - SMS flow: opt-out/opt-in keywords, conversation context by `(business_id, From)`, and
      defensive error handling.
  - Ensure:
    - Signature verification behavior matches `VERIFY_TWILIO_SIGNATURES` and `TWILIO_AUTH_TOKEN`.
    - Opt-out handling continues to suppress customer-facing SMS while allowing owner alerts.

- **Tenant admin & safety controls**
  - `routers/business_admin.py`:
    - Creating/updating tenants (`/v1/admin/businesses`).
    - Rotating API keys and widget tokens.
    - Exposing per-tenant usage and Twilio health.
  - Combined with `SECURITY.md`, this governs how the platform is safely shared across tenants.

- **Reminders & SMS volume**
  - `routers/reminders.py`:
    - `/v1/reminders/send-upcoming` uses `default_reminder_hours` and respects `sms_opt_out`.
  - Updates here should be reflected in `RUNBOOK.md` and `DEPLOYMENT_CHECKLIST.md` and must not
    break opt-out behavior.


How to Approach Changes
-----------------------

For any non-trivial change:

- Start from the PDFs and root docs:
  - Confirm what the desired behavior is (business goal, safety expectation).
- Sketch a short design:
  - Especially for new endpoints, data model changes, or cross-cutting behavior.
- Implement in small steps:
  - Touch one critical area at a time (e.g., conversation manager, then Twilio wiring).
- Add or update tests:
  - See `backend/tests/` for patterns; keep tests focused on observable behavior.
- Update docs:
  - `README.md`, `BACKLOG.md`, `SECURITY.md`, and relevant runbooks when behavior or operations
    change.


Session Store Considerations
----------------------------

For early development and the provided local profiles, call sessions and Twilio state are kept
in-process:

- Voice sessions use an in-memory `InMemorySessionStore` keyed by `session_id`.
- Twilio voice calls use `_CALL_SESSION_MAP` to map `CallSid` to `(session_id, created_at)`.
- Twilio SMS conversations use `_SMS_CONV_MAP` to map `(business_id, From)` to
  `(conversation_id, created_at)`.
- Simple TTL-based pruning is applied so long-running dev processes do not accumulate unbounded
  state.

For a production deployment behind multiple processes or instances, a shared store is recommended:

- Introduce an abstraction (e.g. `SessionStore` protocol) with in-memory and Redis/DB-backed
  implementations.
- Move CallSid→session and `(business_id, From)`→conversation mappings into that shared store,
  with explicit TTLs and cleanup policies.
- Keep all Twilio entry points and `/v1/voice/session/*` flows routing through that abstraction so
  scaling the backend horizontally does not break ongoing calls or SMS threads.


Local Development Profiles
--------------------------

For day-to-day local work, there are two recommended profiles. Both can run purely in stub mode
(no real Twilio or Google Calendar) unless you provide real credentials via environment variables
or an env file.

- **In-memory dev (default)**
  - Uses in-process repositories for customers, appointments, and conversations.
  - Fast startup and easy to reset (a process restart drops all state).
  - Suitable for experimenting with conversation flows and APIs.
  - Run from `backend/`:
    - `uvicorn app.main:app --reload`
    - Optionally add `--env-file ..\.env` (or another env file path) if you want to load settings
      from a file instead of exporting them in your shell.

- **DB-backed dev (SQLite)**
  - Uses SQLite (`DATABASE_URL=sqlite:///./app.db`) with `USE_DB_CUSTOMERS=true`,
    `USE_DB_APPOINTMENTS=true`, and `USE_DB_CONVERSATIONS=true`.
  - Enables realistic multi-tenant behaviour, admin dashboards, and data persistence across
    restarts.
  - Typically also sets `REQUIRE_BUSINESS_API_KEY=true`, `ADMIN_API_KEY`, and
    `OWNER_DASHBOARD_TOKEN` so dashboards and CRM behave like a real deployment.
  - Run from `backend/`:
    - `uvicorn app.main:app --reload`
    - As above, you can pass `--env-file ..\.env` (or any other env file) to load these variables
      from disk.

Use the in-memory profile for quick iteration on behaviour and tests, and the DB-backed profile
when working on tenant admin, dashboards, usage/metrics, or anything that depends on persistent
data.


Performance & Load Testing
--------------------------

The engineering whitepaper calls for boring, observable performance with attention to P95/P99
latency, especially in voice paths. For this backend, focus on:

- **Critical latency paths**
  - `/v1/voice/session/*` – conversational turns for voice and telephony.
  - `/telephony/*` and `/twilio/voice` – hot paths for live calls.
  - `/twilio/sms` – SMS flows for confirmations, reminders, and reschedules.

- **Targets (initial, non-binding)**
  - P95 < 500 ms and P99 < 1s for non-external-call flows (stub mode).
  - When STT/TTS or Google Calendar are enabled, budget additional latency per call but keep total
    perceived round-trip time under ~2s for conversational turns.

- **How to exercise the system**
  - Use the autogenerated docs (`/docs`) or simple scripts (e.g. `httpx`/`curl`) to run load
    against `/v1/voice/session/*` and `/telephony/*` using stubbed providers.
  - For Twilio-style flows, simulate webhook traffic with recorded payloads against `/twilio/voice`
    and `/twilio/sms`.
  - Start with 5–10 concurrent sessions and scale up to a multiple of expected production load.

- **What to watch**
  - `/metrics`:
    - `total_requests` vs `total_errors`.
    - `voice_session_requests` / `voice_session_errors` and `voice_sessions_by_business`.
    - `twilio_voice_requests` / `twilio_voice_errors` and `twilio_by_business`.
    - `twilio_sms_requests` / `twilio_sms_errors`.
  - `route_metrics[path].max_latency_ms` and `total_latency_ms / request_count` to spot slow
    endpoints.
  - Logs for recurring slow calls or external API timeouts.

Use these observations to tune timeouts, retry policies, and capacity settings (for example,
`max_jobs_per_day`, `reserve_mornings_for_emergencies`, and `travel_buffer_minutes`) before
expanding to higher-volume tenants.
