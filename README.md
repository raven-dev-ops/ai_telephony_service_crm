AI Telephony Service & CRM for Trades
=====================================

![Backend CI](https://github.com/raven-dev-ops/ai_telephony_service_crm/actions/workflows/backend-ci.yml/badge.svg)
![Perf Smoke](https://github.com/raven-dev-ops/ai_telephony_service_crm/actions/workflows/perf-smoke.yml/badge.svg)
![Coverage](https://img.shields.io/badge/coverage-%E2%89%A585%25-brightgreen)

AI-powered telephony and lightweight CRM for small trades businesses (reference tenant: Bristol Plumbing, Merriam KS). The assistant acts as a 24/7 virtual receptionist that answers calls/chats, triages emergencies, schedules on Google Calendar, and keeps a searchable history of customers and jobs.

Quick Start (local dev)
-----------------------
1) Backend  
   ```bash
   cd backend
   python -m venv .venv && .venv\Scripts\activate  # Windows
   # or: source .venv/bin/activate                # Unix
   pip install -e .[dev]
   uvicorn app.main:app --reload                  # uses defaults
   # or use provided envs:
   #   uvicorn app.main:app --reload --env-file ..\env.dev.inmemory
   #   uvicorn app.main:app --reload --env-file ..\env.dev.db
   ```

2) Owner dashboard  
   - Open `dashboard/index.html` (file:// or `python -m http.server` from repo root).  
   - Set `X-API-Key` and `X-Owner-Token` from your tenant (`/v1/admin/businesses`).  
   - Quick investor view: `/planner` serves the PLANNER.md HTML; `dashboard/planner.html` embeds it alongside owner/admin links.

3) Admin dashboard (optional)  
   - Open `dashboard/admin.html`; supply `X-Admin-API-Key`.  
   - Use Tenants, Usage, Twilio/Stripe health cards to verify config.

4) Self-service signup/onboarding (optional)  
   - Set `ALLOW_SELF_SIGNUP=true`; open `dashboard/signup.html` then `dashboard/onboarding.html` to connect calendar/email/QBO stubs.

5) Owner AI assistant  
   - Ask questions via the floating chat bubble.  
   - Rich answers require `SPEECH_PROVIDER=openai` and `OPENAI_API_KEY`; otherwise you get a metrics snapshot fallback.

Feature highlights
------------------
- Voice/chat assistant with deterministic emergency routing and optional OpenAI intent assist.
- Scheduling with Google Calendar; reschedule/cancel flows and SMS confirmations.
- CRM: customers, appointments, conversations, CSV import, retention campaigns.
- Owner/admin dashboards: schedules, callbacks/voicemails, analytics, Twilio/Stripe health.
- Self-service signup/onboarding, per-tenant API and widget tokens, subscription gating with grace and limits.
- Notifications: owner alerts for emergencies/missed calls/voicemail; customer reminders and opt-out handling.

Architecture (capsule)
----------------------
- **Backend**: FastAPI, pluggable STT/TTS and intent (heuristics with optional OpenAI), repositories for appointments/conversations/customers.  
- **Dashboards**: static HTML/JS (`dashboard/index.html`, `dashboard/admin.html`) using `X-API-Key`, `X-Owner-Token`, `X-Admin-API-Key`.  
- **Widget**: `widget/chat.html` + `widget/embed.js` using `X-Widget-Token`.  
- **Integrations**: Google Calendar, Twilio/SMS, Stripe, QBO, Gmail—all stubbed by default via env profiles (`env.*.stub`) with signature verification where applicable.

Safety, auth, and billing
-------------------------
- Secrets via env vars; stubs avoid real calls in dev/CI.  
- Auth: bcrypt passwords, JWT access/refresh, lockout/reset, rate limits.  
- Subscription enforcement: `ENFORCE_SUBSCRIPTION=true` blocks voice/chat when not active/trialing; grace reminders and plan caps included.  
- Retention: periodic purge (`RETENTION_PURGE_INTERVAL_HOURS`), transcript capture is configurable (`capture_transcripts`, per-tenant retention opt-out).  
- Webhooks: Twilio/Stripe signature verification and replay protection; owner/admin tokens required in prod (`OWNER_DASHBOARD_TOKEN`, `ADMIN_API_KEY`, `REQUIRE_BUSINESS_API_KEY=true`).

Performance & load smoke (CI)
-----------------------------
- `tests/test_perf_smoke.py` – baseline perf for core flows.  
- `tests/test_perf_multitenant_smoke.py` – multi-tenant path checks under load.  
- `tests/test_perf_transcript_smoke.py` – long transcript handling.  
- `tests/test_twilio_streaming_canary.py` – streaming canary (respects env secrets).  
These run in `.github/workflows/perf-smoke.yml` on every push/PR.

Testing & coverage
------------------
- Coverage enforced at 85% in Backend CI; artifacts (`coverage.xml`) per Python version are uploaded in Actions → backend-ci artifacts.  
- Lint/type/security: `ruff check .`, `black --check .`, `mypy`, `bandit`.  
- Core suites: `pytest` (full), `tests/test_business_admin.py` for admin health (Twilio/Stripe), `tests/test_intent_and_retention.py` for safety/transcript opts, `tests/test_subscription_guardrails.py` for gating/limits.  
- Providers are mocked/stubbed by default via `env.*.stub`; Twilio/Stripe/Google/QBO keys are never required to run tests locally or in CI.

Docs & references
-----------------
- Design and operating ground truth: `OUTLINE.md`, `BACKLOG.md`, `WIKI.md`, `ENGINEERING.md`, `SECURITY.md`, `PRIVACY_POLICY.md`, `TERMS_OF_SERVICE.md`, `RUNBOOK.md`, `DEPLOYMENT_CHECKLIST.md`, and the Bristol PDFs (`Bristol_Plumbing_*.pdf`, `Project_Engineering_Whitepaper.pdf`).  
- API details: `API_REFERENCE.md`; data model: `DATA_MODEL.md`.  
- Dev workflow: `DEV_WORKFLOW.md`, `TOOLS.md`.  
- Incident playbooks: `INCIDENT_RESPONSE.md`, `POST_INCIDENT_TEMPLATE.md`.

New in this iteration
---------------------
- Subscription enforcement improved: grace reminders, plan cap warnings, and voicemail/callback surfacing on dashboard cards.  
- Perf smoke and coverage badges visible; README streamlined for faster onboarding.  
- Investor brief available at `/planner` and `dashboard/planner.html`.
