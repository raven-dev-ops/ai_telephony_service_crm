Releases
========

This file tracks high-level release notes for the AI Telephony Service & CRM project.


Source PDFs & Traceability
--------------------------

Release summaries here are grounded in the four primary design documents:

- `Bristol_Plumbing_Analysis.pdf` - business context and call patterns.
- `Bristol_Plumbing_Implementation.pdf` - implementation concepts and channel roadmap.
- `Bristol_Plumbing_Project_Plan.pdf` - phased feature plan and architecture.
- `Project_Engineering_Whitepaper.pdf` - engineering culture, safety, and operational standards.


Unreleased
----------

- Define initial documentation set derived from Bristol Plumbing and RavDevOps PDFs.
- Establish project vision, architecture outline, and security/privacy baselines.
- Capture initial product backlog and domain wiki.
- Expand root documentation to cover Twilio wiring, multi-tenant usage, and SMS opt-out behavior
  (including how reminders and customer-facing SMS honor opt-out keywords).
- Add initial backend implementation, dashboard prototype, and web chat widget that implement the
  Phase 1–4 flows described in the PDFs and root docs.
- Introduce per-tenant metrics and admin/owner dashboards for:
  - SMS volume (owner vs customer) per tenant.
  - Twilio voice/SMS request and error counts (global and per tenant).
  - Reschedule queues, emergency counts, and service-type mix across tenants.
- Add example development env profiles, an API index, and deeper data/ops documentation to make the
  prototype easier to run and extend.



0.1.4 - Twilio Streaming Token Guard
------------------------------------

Date: 2026-01-06

- Add optional `TWILIO_STREAM_TOKEN` guard for public Twilio Media Streams WebSocket ingress.


0.1.3 - Twilio Media Streams Ingest
-----------------------------------

Date: 2026-01-06

- Add Twilio Media Streams WebSocket ingest with mu-law conversion and transcript forwarding into the
  existing voice stream pipeline.
- Expose `TWILIO_STREAM_MIN_SECONDS` to tune minimum buffer duration before transcription.


0.1.2 - Stripe Webhook Hardening (Non-Prod)
-------------------------------------------

Date: 2026-01-06

- Stripe webhooks now enforce signature verification in prod-like environments (prod/staging/qa).
- Staging env template sets `ENVIRONMENT=staging` for correct environment detection.


0.1.1 - Integration Webhook Fixes
---------------------------------

Date: 2026-01-05

- Stripe webhooks are now public endpoints protected by signature + replay checks (no owner token required).
- QuickBooks OAuth uses signed state and allows public callback validation for production flows.
- Backend container enables proxy headers to preserve scheme/host behind Cloud Run.
- Docs updated with webhook/OAuth/proxy guidance.
- CI typing fix for GCP speech credentials to keep mypy green.


0.2.0 - Load Testing & Ops Docs (example)
----------------------------------------

Date: TBD

- Add a simple load-test harness (`backend/load_test_voice.py`) for `/v1/voice/session/*` so teams
  can measure per-session latency and basic throughput against local or staging environments.
- Extend engineering and runbook documentation around metrics, troubleshooting, and performance
  expectations, tying them explicitly to `/metrics` and route-level metrics.
- Introduce data/API/dashboard reference docs (`DATA_MODEL.md`, `API_REFERENCE.md`,
  `dashboard/DASHBOARD.md`) to make the prototype easier to understand and extend.
- Provide example development env profiles (`env.dev.inmemory`, `env.dev.db`) and a Quick Start in
  `README.md` so new engineers can spin up backend + dashboards quickly.


Release Process (0.2.x and beyond)
----------------------------------

When cutting a new backend release (for example, `0.2.0`):

1. **Decide scope**
   - Group a coherent set of changes (features, docs, or fixes) that are ready to ship together.
2. **Update version and docs**
   - Bump `version` in `backend/pyproject.toml` (e.g., `0.2.0`).
   - In `CHANGELOG.md`:
     - Move completed items from `[Unreleased]` into a new `[0.2.0] - YYYY-MM-DD` section.
     - Leave `[Unreleased]` for future work.
   - In `RELEASES.md`:
     - Summarize the release under a new `0.2.0 - <name>` entry, focusing on user-facing changes.
3. **Verify**
   - Run `pytest` in `backend/` and ensure all tests pass.
   - Optionally run `ruff` and `black --check` to match CI behaviour.
   - Smoke test key flows:
     - `/v1/voice/session/*` and `/telephony/*` with stubbed providers.
     - `/v1/owner/*` and `/v1/crm/*` via the owner dashboard.
     - `/v1/admin/*` via the admin dashboard.
4. **Tag and publish**
   - Commit the version and docs changes.
   - Create a Git tag (e.g., `v0.2.0`) and push it.
   - If you publish to a package index or container registry, build and push using your usual pipeline.


0.1.0 - Documentation & Planning Drop
-------------------------------------

Date: 2025-12-01 (initial scaffolding)

- Added root-level documentation:
  - `README.md` - project overview and architecture.
  - `OUTLINE.md` - detailed system and phase outline.
  - `BACKLOG.md` - product backlog derived from project plan.
  - `WIKI.md` - domain knowledge and use cases.
  - `SECURITY.md`, `PRIVACY_POLICY.md`, `TERMS_OF_SERVICE.md` - baseline policies.
  - `RELEASES.md`, `CHANGELOG.md`, `LICENSE` - supporting project docs.
- Linked documentation back to the four source PDFs in the repository.
