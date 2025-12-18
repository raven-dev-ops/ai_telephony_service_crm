AI Telephony Service & CRM for Trades
=====================================

![Backend CI](https://github.com/raven-dev-ops/ai_telephony_service_crm/actions/workflows/backend-ci.yml/badge.svg)
![Perf Smoke](https://github.com/raven-dev-ops/ai_telephony_service_crm/actions/workflows/perf-smoke.yml/badge.svg)
[![Coverage](https://img.shields.io/badge/coverage-%E2%89%A585%25-brightgreen)](https://github.com/raven-dev-ops/ai_telephony_service_crm/actions/workflows/backend-ci.yml)

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

   # Optional: seed demo data for dashboards/analytics
   python seed_demo_data.py --reset               # seeds default_business
   ```

2) Owner dashboard  
   - Open `dashboard/index.html` (file:// or `python -m http.server` from repo root).  
   - If running with defaults (no `OWNER_DASHBOARD_TOKEN`/`ADMIN_API_KEY`), you can leave tokens blank and use `X-Business-ID=default_business`.
   - If using `env.dev.db`, use `X-Owner-Token=dev-owner-token` and either `X-Business-ID=default_business` or fetch the tenant `X-API-Key` from `/v1/admin/businesses` (with `X-Admin-API-Key=dev-admin-key`).
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
- QuickBooks: sandbox/demo by default; set `QBO_CLIENT_ID`, `QBO_CLIENT_SECRET`, `QBO_REDIRECT_URI` (and `QBO_SANDBOX=false` for production) to enable live OAuth and syncing customers/receipts from appointments.

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
- Product and policy: `WIKI.md`, `CHANGELOG.md`, `RELEASES.md`, `SECURITY.md`, `PRIVACY_POLICY.md`, `TERMS_OF_SERVICE.md`, and the Bristol PDFs (`Bristol_Plumbing_*.pdf`, `Project_Engineering_Whitepaper.pdf`).  
- Architecture and deployment: `backend/BACKEND.md`, `dashboard/DASHBOARD.md`, plus platform details in `WIKI.md`.  
- ISMS and ISO prep: `docs/ISMS/ISMS_README.md` (links to scope, risk method, SoA, access control, secure SDLC, incident/DR, backups, logging/alerts, vendor register, and audit plan).  
- Incident playbooks: `docs/ISMS/INCIDENT_RESPONSE_PLAN.md` and the wiki playbooks (`ai_telephony_service_crm.wiki.local/IncidentPlaybooks.md`).  
- Beta tracking (archived): `docs/archive/beta/BETA_GITHUB_ISSUES_TASKLIST.md` (issue checklist), `docs/archive/beta/BETA_DOD.md` (Definition of Done), and `docs/archive/beta/BETA_KPIS.md` (top metrics).

New in this iteration
---------------------
- Subscription enforcement improved: grace reminders, plan cap warnings, and voicemail/callback surfacing on dashboard cards.  
- Perf smoke and coverage badges visible; README streamlined for faster onboarding.  
  - Coverage badge links to backend-ci; detailed `coverage.xml` per Python version is available in Actions → backend-ci artifacts.
- Investor brief available at `/planner` and `dashboard/planner.html`.
