Developer Workflow (Local)
===========================

This checklist is for engineers spinning up the AI Telephony Service & CRM locally for day-to-day
development.


1. Clone & Inspect
------------------

- Clone the repo and skim:
  - `README.md` – high-level overview + Quick Start.
  - `OUTLINE.md` – architecture and phases.
  - `TOOLS.md` – list of helpful env files and scripts.


2. Create a Python Environment
------------------------------

- From the repo root:

  ```bash
  cd backend
  python -m venv .venv
  .venv\Scripts\activate  # or: source .venv/bin/activate
  pip install -e .[dev]
  ```


3. Choose a Dev Profile & Run Backend
-------------------------------------

- **In-memory dev (fast, no persistence)**:

  ```bash
  uvicorn app.main:app --reload --env-file ..\env.dev.inmemory
  ```

- **DB-backed dev (SQLite, closer to real)**:

  ```bash
  uvicorn app.main:app --reload --env-file ..\env.dev.db
  ```

- Confirm:
  - `GET http://localhost:8000/healthz`
  - `GET http://localhost:8000/docs`

See `ENGINEERING.md` for more on these profiles.


4. Seed / Inspect Tenants
-------------------------

- Use the admin API or dashboard:
  - Call `POST /v1/admin/demo-tenants` (in non-production) to seed demo tenants, or
  - Use `POST /v1/admin/businesses` to create a real tenant.
- Retrieve tenant info:
  - `GET /v1/admin/businesses` – capture `id`, `api_key`, `widget_token`, etc.


5. Open Dashboards
------------------

- **Owner dashboard**:
  - Open `dashboard/index.html` in a browser.
  - Set:
    - `X-API-Key` (tenant `api_key`).
    - `X-Owner-Token` (from env: `OWNER_DASHBOARD_TOKEN`).
  - Verify cards:
    - Tomorrow’s Schedule, Today’s Jobs.
    - Emergency Jobs, Recent Conversations, SMS Usage.

- **Admin dashboard**:
  - Open `dashboard/admin.html`.
  - Set `X-Admin-API-Key` (from env: `ADMIN_API_KEY`).
  - Use:
    - Tenants: list/edit tenants, rotate keys/tokens.
    - Tenant Usage: appointments, emergencies, SMS volume.
    - Twilio / Webhook Health: Twilio voice/SMS request/error counts.

See `dashboard/DASHBOARD.md` for card-by-card details.


6. Run Tests & Linting
----------------------

- From `backend/`:

  ```bash
  pytest
  ruff check .
  black --check .
  ```

- Focus tests around the area you are changing:
  - Conversation flows: `backend/tests/test_conversation.py`
  - Owner routes: `backend/tests/test_owner_*`
  - Twilio integration: `backend/tests/test_twilio_integration.py`


7. Load Testing (Optional)
--------------------------

- Use `backend/load_test_voice.py` to exercise voice or telephony APIs:

  ```bash
  # Voice session API
  cd backend
  python load_test_voice.py --mode voice --sessions 20 --concurrency 5 \
    --backend http://localhost:8000 \
    --api-key YOUR_API_KEY --business-id YOUR_BUSINESS_ID

  # Telephony API
  python load_test_voice.py --mode telephony --sessions 20 --concurrency 5 \
    --backend http://localhost:8000 \
    --api-key YOUR_API_KEY --business-id YOUR_BUSINESS_ID
  ```

- Monitor `/metrics` and logs as described in `RUNBOOK.md` and `ENGINEERING.md`.


8. Update Docs with Behaviour Changes
-------------------------------------

- When changing user-visible behaviour or operational expectations, update:
  - `README.md` (overview / Quick Start).
  - `API_REFERENCE.md` (if routes change).
  - `RUNBOOK.md` / `PILOT_RUNBOOK.md` (ops, Twilio wiring, reminders/retention).
  - `SECURITY.md` / `PRIVACY_POLICY.md` / `TERMS_OF_SERVICE.md` (if there are policy implications).
  - `CHANGELOG.md` / `RELEASES.md` (for release notes).

Follow the patterns and standards described in `ENGINEERING.md` and the RavDevOps whitepaper.


9. First Bug / First Feature Checklist
--------------------------------------

When you pick up your first change (bug fix or small feature), keep it small and well-scoped:

1. **Clarify the intent**
   - For a bug: write down the observed vs expected behaviour and where it shows up (endpoint, dashboard card, Twilio flow, etc.).
   - For a feature: tie it to an item in `BACKLOG.md` or a design doc section when possible.

2. **Find the code and tests**
   - Use the route and file maps in `ENGINEERING.md` and `API_REFERENCE.md` to locate the relevant router/service:
     - Voice/telephony: `backend/app/routers/voice.py`, `telephony.py`, `services/conversation.py`.
     - Twilio: `backend/app/routers/twilio_integration.py` and `services/twilio_state.py`.
     - CRM/owner: `backend/app/routers/crm.py`, `owner.py`, `repositories.py`.
   - Identify existing tests that cover the flow:
     - Conversation: `backend/tests/test_conversation.py`.
     - Owner: `backend/tests/test_owner_*`.
     - Twilio: `backend/tests/test_twilio_integration.py`.
     - CRM: `backend/tests/test_crm_*`.

3. **Reproduce and write/adjust a test**
   - If a bug: add or adjust a test that fails with the current behaviour and clearly expresses the expected outcome.
   - If a feature: add a test that captures the new behaviour end-to-end where reasonable (e.g., router-level test hitting the public API).

4. **Implement the change**
   - Keep changes localized (router → service → repository) and avoid drive-by refactors.
   - Preserve safety-sensitive behaviour (emergency flows, SMS opt-out, tenant isolation); when in doubt, check `SECURITY.md` and the whitepaper.

5. **Run tests and basic checks**
   - From `backend/`:
     ```bash
     pytest
     ruff check .
     black --check .
     ```
   - If your change affects runtime behaviour, exercise it via the dashboard or a `curl`/`httpx` script.

6. **Update docs and notes**
   - If the change is user-visible or operationally relevant, update the docs as in step 8.
   - Add a bullet to `[Unreleased]` in `CHANGELOG.md` summarizing the change.
   - If you are preparing a release, follow `RELEASES.md` for versioning/tagging steps.
