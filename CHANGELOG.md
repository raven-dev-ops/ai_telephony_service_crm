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

- Implemented initial backend voice assistant, CRM, multi-tenant support, and dashboard prototype as described in the project documentation.
- Documented SMS opt-out behavior and Twilio wiring in `README.md`, `PRIVACY_POLICY.md`, and `RUNBOOK.md`.
- Added per-tenant SMS and Twilio metrics (`sms_by_business`, `twilio_by_business`) and surfaced them via `/metrics` and the admin Twilio health endpoint.
- Expanded owner/admin dashboards with reschedule queues, SMS usage summaries, per-tenant usage reports, and Twilio/webhook health views.
- Added example env profiles (`env.dev.inmemory`, `env.dev.db`), an API index (`API_REFERENCE.md`), and data/ops documentation (`DATA_MODEL.md`, expanded `RUNBOOK.md`, and `ENGINEERING.md`) to align docs with the current implementation.


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
