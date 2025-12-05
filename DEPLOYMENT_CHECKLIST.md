Deployment Checklist
=====================

This checklist summarizes what you need to configure and create in order to deploy the AI Telephony Service & CRM for a real tenant (e.g., Bristol Plumbing).
For a concrete, GCP-specific walkthrough using Google Kubernetes Engine (GKE) and Docker, see
`DEPLOYMENT_GCP_GKE.md`.

Source PDFs & Traceability
--------------------------

These steps are derived from:

- `Bristol_Plumbing_Project_Plan.pdf` - cloud architecture, calendar integration, and reliability goals.
- `Bristol_Plumbing_Implementation.pdf` - Twilio wiring, SMS flows, and owner notifications.
- `Project_Engineering_Whitepaper.pdf` - production-readiness, secrets, and environment discipline.

1. Environment & Secrets
------------------------

Configure environment variables for the backend process:

- **Core**
  - `DATABASE_URL` - real database URL (e.g., Postgres, Cloud SQL).
  - `REQUIRE_BUSINESS_API_KEY=true` - require tenant credentials on CRM/owner/widget routes.
- **Admin / dashboard**
  - `ADMIN_API_KEY=<strong admin key>` - protects `/v1/admin/*`.
  - `OWNER_DASHBOARD_TOKEN=<owner token>` - protects `/v1/owner/*` and `/v1/crm/*` via the dashboard.
- **SMS / Twilio (per environment)**
  - `SMS_PROVIDER=twilio`
  - `SMS_FROM_NUMBER=+1XXXXXXXXXX` - Twilio number used for outbound SMS.
  - `SMS_OWNER_NUMBER=+1XXXXXXXXXX` - owner phone for alerts (optional if using per-tenant `owner_phone`).
  - `TWILIO_ACCOUNT_SID=...`
  - `TWILIO_AUTH_TOKEN=...`
  - Optional: `VERIFY_TWILIO_SIGNATURES=true` - enforce Twilio signature verification.

2. Calendar Configuration
-------------------------

Back-end calendar settings (from env or config, see `backend/app/config.py`):

- `GOOGLE_CALENDAR_ID` - default Google Calendar ID for appointments (for example, `primary`).
- `GOOGLE_CALENDAR_CREDENTIALS_FILE` - path to the Google service account JSON credentials file.
- `CALENDAR_USE_STUB` - when left at the default (`true`), the backend uses a safe stub calendar; set to `false` to force use of the real Google Calendar API.

Google Cloud / Calendar:

- Create or choose a Google Calendar for the business.
- Create OAuth / service account credentials for the Calendar API.
- Share the calendar with the service account as needed.

3. Business / Tenant Records
----------------------------

Create or seed `Business` rows via the admin API:

- Use `POST /v1/admin/businesses` with:
  - `id` (optional; if omitted, a random ID is generated).
  - `name` (e.g., `"Bristol Plumbing"`).
  - `calendar_id` (optional; overrides `GOOGLE_CALENDAR_ID` for this tenant).
- Or, for local demos, call:
  - `POST /v1/admin/demo-tenants` - seeds two demo tenants with sample customers/appointments.

For each `Business` the backend maintains:

- `api_key` - tenant API key, used as `X-API-Key`.
- `widget_token` - public token for the web chat widget.
- `status` - `"ACTIVE"` or `"SUSPENDED"`.
- `owner_phone`, `emergency_keywords`, `default_reminder_hours` - optional per-tenant settings.

4. Tenant Safety & Notification Settings
----------------------------------------

Per tenant, configure safety/notification fields via:

- `PATCH /v1/admin/businesses/{business_id}` body can include:
  - `status` - `"ACTIVE"` (normal) or `"SUSPENDED"` (block Twilio/CRM/widget traffic).
  - `owner_phone` - owner's phone for alerts.
  - `emergency_keywords` - comma-separated overrides, e.g. `"no water, sewer backup, basement flooding"`.
  - `default_reminder_hours` - e.g. `24` (used by reminder jobs).

These are also editable from the dashboard in the **Owner & Notifications (Admin)** card when `ADMIN_API_KEY` is set and supplied as `X-Admin-API-Key`.

5. Keys for Dashboard & API Clients
-----------------------------------

From `GET /v1/admin/businesses` (or via the dashboard Tenants card), capture:

- Per tenant:
  - `api_key` - use as:
    - `X-API-Key` in the dashboard (set once in the header section).
    - `X-API-Key` from any trusted client (scripts, owner mobile app, etc.).
  - `widget_token` - used only for the web chat widget.
- Global:
  - `ADMIN_API_KEY` - to manage tenants from the dashboard/admin API.
  - `OWNER_DASHBOARD_TOKEN` - to access owner schedule and CRM views from the dashboard.

6. Web Chat Widget Embeds
-------------------------

Host `widget/chat.html` and `widget/embed.js` alongside the backend. For each tenant:

1. Ensure the tenant has a `widget_token` (rotate via `POST /v1/admin/businesses/{id}/rotate-widget-token` if needed).
2. Use this embed snippet on the tenant's site:

```html
<script src="https://<your-domain>/widget/embed.js"
        data-widget-url="/widget/chat.html"
        data-widget-token="TENANT_WIDGET_TOKEN"></script>
```

Notes:

- `embed.js` resolves `data-widget-url` against its own origin and appends `?widget_token=...`.
- `chat.html` forwards `widget_token` as `X-Widget-Token` so the backend can resolve the tenant without exposing `api_key`.

7. Twilio Configuration
-----------------------

In the Twilio Console for each phone number you use:

- **Voice webhook**:
  - Request URL: `POST https://<your-domain>/twilio/voice`
  - Optionally add `?business_id=<tenant_id>` if you want to route by query parameter instead of relying solely on `X-API-Key`.
- **Messaging webhook**:
  - Request URL: `POST https://<your-domain>/twilio/sms`
  - Same note regarding `business_id` if per-number routing is desired.

8. Optional Seed Data
---------------------

To make the dashboard useful from day one, optionally seed:

- Customers:
  - `POST /v1/crm/customers` with `name`, `phone`, `address` for known clients.
- Appointments:
  - `POST /v1/crm/appointments` with `customer_id`, `start_time`, `end_time`, etc.

This ensures:

- The **Schedule**, **Customers**, **Analytics**, and **QA** cards have real data to display.
- Owner schedule endpoints (`/v1/owner/schedule/tomorrow` and `/audio`) return meaningful results.

9. Final Verification
---------------------

Before onboarding a real tenant:

- Run the backend test suite: `pytest backend -q` - expect all tests to pass.
- From the dashboard:
  - Confirm you can:
    - Use `X-Admin-API-Key` to list/edit tenants, rotate API keys and widget tokens.
    - Use `X-API-Key` + `X-Owner-Token` to view schedule, customers, conversations, QA.
    - See per-tenant usage and Twilio health.
- Exercise end-to-end flows in staging:
  - Inbound call -> emergency detection -> appointment creation -> calendar + SMS.
  - Web chat via the embedded widget -> conversation appears as `channel="web"` in Recent/Flagged Conversations.
