Changelog
=========

All notable changes to this project will be documented in this file.


Source PDFs & Traceability
--------------------------

Release notes here summarize work that implements or documents the design described in:

- `Bristol_Plumbing_Analysis.pdf`
- `Bristol_Plumbing_Implementation.pdf`
- `Bristol_Plumbing_Project_Plan.pdf`
- `Project_Engineering_Whitepaper.pdf`


[Unreleased]
------------
- Process: track AGENTS.md in-repo and require PR creation/push for all improvements.
- Ops: update January 2026 access review log and make GitHub access export resilient to permission errors.

- Implemented initial backend voice assistant, CRM, multi-tenant support, and dashboard prototype as described in the project documentation.
- Documented SMS opt-out behavior and Twilio wiring in `README.md`, `PRIVACY_POLICY.md`, and `RUNBOOK.md`.
- Added per-tenant SMS and Twilio metrics (`sms_by_business`, `twilio_by_business`) and surfaced them via `/metrics` and the admin Twilio health endpoint.
- Expanded owner/admin dashboards with reschedule queues, SMS usage summaries, per-tenant usage reports, and Twilio/webhook health views.
- Added example env profiles (`env.dev.inmemory`, `env.dev.db`), an API index (`API_REFERENCE.md`), and data/ops documentation (`DATA_MODEL.md`, expanded `RUNBOOK.md`, and `ENGINEERING.md`) to align docs with the current implementation.
- Added per-tenant Twilio number support (`twilio_phone_number`), owner onboarding Twilio provisioning (`/v1/owner/twilio/provision`), and admin Stripe health monitoring (`/v1/admin/stripe/health` + dashboard card) covering config readiness, webhook failures, and subscription usage.
- Added owner QuickBooks link/pending approvals view (`/v1/owner/qbo/summary`, `/pending`), owner notifications (`/v1/owner/qbo/notify`), and a dashboard card to surface pending QBO insertions.
- Owner dashboard polish: quick actions + plan badge, data-strip status pills, intent/voice chips; conversation & callback cards gained client-side filters (search/channel/status), newest/oldest sorting, live summaries, CSV/phone copy shortcuts, and “last updated” stamps for schedule/callbacks/conversations/service metrics.
- Email delivery now supports Gmail (per-tenant OAuth) and SendGrid providers with retry/backoff, configuration validation, and stubbed fallbacks.
- Google Calendar: per-tenant OAuth tokens are used for availability/event writes, refreshed and persisted when needed; added `/v1/calendar/google/webhook` to sync inbound updates/cancellations into stored appointments with stub fallback.
- QuickBooks: OAuth token exchanges/refresh persisted per tenant; `/v1/integrations/qbo/sync` now pushes customers + sales receipts (with retry/backoff) when credentials are configured, and returns stubbed status otherwise.
- AI/intent: intent classifier now uses recent conversation history with OpenAI guardrails and fallback heuristics; owner assistant can reply via Twilio voice (`/v1/owner/assistant/voice-reply`) alongside text.
- Onboarding guardrails now protect voice/Twilio session routes; enforcement can be disabled in tests via `ONBOARDING_ENFORCE_IN_TESTS=false`.
- Integration readiness badges distinguish stub vs live for QuickBooks, Twilio, and Google; QuickBooks sync defaults to inline execution (pass `enqueue=true` to run in the background).
- Testing/guardrails: added admin route regression checks, end-to-end signup→voice scheduling flow, load-smoke context test, and subscription enforcement guardrail coverage.
- Twilio voice streaming now enqueues missed/partial calls into the callback queue, sends owner alerts, and includes signature validation tests for voice and status webhooks; voice-assistant completions also enqueue callback follow-ups.
- Stripe billing now favors live Checkout/Customer Portal when configured, verifies webhook signatures with replay protection, records plan/service tier from Stripe metadata, emails owners on payment failures, and enforces subscription status across voice/Twilio/voice-session APIs with dashboard warnings.



[0.1.2] - 2026-01-06
-------------------

- Enforce Stripe webhook signature verification in production-like environments (prod/staging/qa).
- Staging env template sets `ENVIRONMENT=staging` for correct environment detection.


[0.1.1] - 2026-01-05
-------------------

- Allow Stripe webhooks to bypass owner auth while preserving signature and replay protection.
- Sign QuickBooks OAuth state and validate callbacks without owner tokens in production flows.
- Enable proxy headers in the container for correct scheme/host behind Cloud Run.
- Documentation updates for webhook auth, OAuth state signing, and proxy header guidance.
- CI: fix mypy typing for GCP speech credentials to unblock Backend CI.


[0.2.0] – TBD (example)
-----------------------

Planned scope for a “Load Testing & Ops Docs” release:

- Add a simple load-test script (`backend/load_test_voice.py`) to exercise `/v1/voice/session/*`
  and measure per-session latency under configurable concurrency.
- Extend documentation around metrics, troubleshooting, and performance expectations in
  `ENGINEERING.md` and `RUNBOOK.md`, tying guidance back to `/metrics` and route-level metrics.
- Document the data model (`DATA_MODEL.md`), API surface (`API_REFERENCE.md`), and dashboard cards
  (`dashboard/DASHBOARD.md`) so new contributors can quickly understand how entities and views fit
  together.
- Provide example development env profiles (`env.dev.inmemory`, `env.dev.db`) and a Quick Start
  in `README.md` so running the backend + dashboards is a 10–15 minute task for new engineers.


[0.1.0] – 2025-12-01
--------------------

- Added comprehensive root-level documentation based on:
  - `Bristol_Plumbing_Analysis.pdf`
  - `Bristol_Plumbing_Implementation.pdf`
  - `Bristol_Plumbing_Project_Plan.pdf`
  - `Project_Engineering_Whitepaper.pdf`
- Defined initial product backlog, security policy, privacy policy, and terms of service.
