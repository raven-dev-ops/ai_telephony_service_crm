Developer Tools & Scripts
==========================

This file lists small utilities and configs that make developing, testing, and operating the
AI Telephony Service & CRM easier.


Env Profiles
------------

- `env.dev.inmemory`
  - In-memory developer profile (no DB persistence, stub calendar/SMS).
  - Suitable for quick experiments with call flows and APIs.
  - Usage (from `backend/`):
    - `uvicorn app.main:app --reload --env-file ..\env.dev.inmemory`

- `env.dev.db`
  - SQLite-backed developer profile with `USE_DB_CUSTOMERS=true`, `USE_DB_APPOINTMENTS=true`,
    and `USE_DB_CONVERSATIONS=true`.
  - Suitable for working on tenant admin, dashboards, usage/metrics, and persistent data.
  - Usage (from `backend/`):
    - `uvicorn app.main:app --reload --env-file ..\env.dev.db`

See `ENGINEERING.md` for more on the two local development profiles.


Load Testing
------------

- `backend/load_test_voice.py`
  - Asynchronously exercises either:
    - `/v1/voice/session/*` (`--mode voice`), or
    - `/telephony/*` (`--mode telephony`).
  - Runs a configurable number of concurrent synthetic sessions and prints:
    - Completed sessions, errors, wall-clock time.
    - Per-session latency (start + 3 turns + end): avg, p50, p95, p99.

  Basic usage (backend running on `http://localhost:8000`):

  ```bash
  cd backend
  python -m venv .venv
  .venv\Scripts\activate  # or: source .venv/bin/activate
  pip install -e .[dev]

  # Voice session API
  python load_test_voice.py --mode voice --sessions 50 --concurrency 10 \
    --backend http://localhost:8000 \
    --api-key YOUR_API_KEY --business-id YOUR_BUSINESS_ID

  # Telephony API
  python load_test_voice.py --mode telephony --sessions 50 --concurrency 10 \
    --backend http://localhost:8000 \
    --api-key YOUR_API_KEY --business-id YOUR_BUSINESS_ID
  ```

  Correlate these runs with `/metrics` (see `RUNBOOK.md` and `ENGINEERING.md`) to understand
  performance and error behaviour.


Dashboards & Reference
----------------------

- `dashboard/DASHBOARD.md`
  - Describes the owner and admin dashboards, their cards, and the backend endpoints they call.

- `API_REFERENCE.md`
  - High-level index of backend routes grouped by area (voice, telephony, Twilio, CRM, owner,
    admin, reminders, retention, widget).

- `DATA_MODEL.md`
  - Summary of the core data model (Business, Customer, Appointment, Conversation, ConversationMessage,
    callback metrics) and how the entities relate.

- `DEV_WORKFLOW.md`
  - Step-by-step local developer workflow that ties together env profiles, backend startup, dashboards,
    tests, load testing, and documentation updates.
