Pilot Runbook
=============

This runbook describes how to run a small end-to-end pilot for one or more trades tenants (e.g., Bristol Plumbing) using this repo.

It assumes you have followed `DEPLOYMENT_CHECKLIST.md` and already have a deployed backend (with DB, TLS, and Twilio configured).


Source PDFs & Traceability
--------------------------

This pilot procedure is derived from:

- `Bristol_Plumbing_Project_Plan.pdf` - phased rollout, pilot goals, and success criteria.
- `Bristol_Plumbing_Implementation.pdf` - real-world call/SMS flows and owner experience.
- `Project_Engineering_Whitepaper.pdf` - guidance on safe experimentation, observability, and
  post-pilot learning.


1. Pre-flight Checks
--------------------

- Confirm environment/config:
  - `DATABASE_URL` points at a real database.
  - `REQUIRE_BUSINESS_API_KEY=true` in production/staging.
  - `ADMIN_API_KEY` and `OWNER_DASHBOARD_TOKEN` are set and stored safely.
  - Twilio env vars (`SMS_PROVIDER`, `SMS_FROM_NUMBER`, `SMS_OWNER_NUMBER`, `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`) are configured.
  - `VERIFY_TWILIO_SIGNATURES=true` if you want signature validation.
- Run backend tests locally (optional but recommended before deployment):
  - `pytest backend -q`


2. Create / Verify Tenants
--------------------------

- Use the admin API or dashboard to ensure tenants exist:
  - If starting from scratch, create tenants via:
    - `POST /v1/admin/businesses` with `id`, `name`, and optional `calendar_id`.
  - For local/staging demos, you can seed demo tenants via:
    - `POST /v1/admin/demo-tenants`

From the dashboard (Tenants card):

- Enter your `X-Admin-API-Key` at the top.
- Click **Refresh Tenants** to see the list.
- For each tenant:
  - Use **Use API key** to set the dashboard `X-API-Key` for that tenant.
  - Optionally **Rotate key** if you want a fresh `api_key`.
  - Optionally **Rotate widget token** to create a new `widget_token` for web chat.


3. Configure Tenant Safety & Notifications
------------------------------------------

From the **Owner & Notifications (Admin)** card:

- Select a tenant and set:
  - `Owner phone override` - the owner's mobile number (if different from global `SMS_OWNER_NUMBER`).
  - `Emergency keywords` - comma-separated overrides if the tenant has specific language (e.g., "no water,no hot water,sewer backup,basement flooded").
  - `Default reminder window (hours)` - e.g. `24`.
- Click **Save settings** and, if needed, **Refresh Tenants** / **Tenant Usage** to see the changes reflected.

If a tenant becomes noisy, abusive, or should be paused during the pilot:

- Set their `status` to `SUSPENDED` via:
  - Dashboard Tenants card (status controls and suspend button in Tenant Usage), or
  - `PATCH /v1/admin/businesses/{id}` with `{"status": "SUSPENDED"}`.

Suspended tenants will have their Twilio/CRM/widget requests blocked with 403 responses.


4. Web Chat Widget Embed
------------------------

For each tenant that wants a website chat widget:

- From the dashboard Tenants card:
  - Rotate or verify the `widget_token` for that tenant.
  - Click **Show embed snippet** to get the `<script>` snippet.
  - Copy the snippet and paste it into the tenant's website HTML (e.g., in the `<body>`):

```html
<script src="https://<your-domain>/widget/embed.js"
        data-widget-url="/widget/chat.html"
        data-widget-token="TENANT_WIDGET_TOKEN"></script>
```

This will render a floating chat widget in the bottom-right corner of the site. Web chat conversations will appear as `channel="web"` in the dashboard's Recent/Flagged Conversations and be tied to the correct tenant via `X-Widget-Token`.


5. Twilio Wiring for the Pilot
------------------------------

In the Twilio Console for each pilot number:

- **Voice webhook**:
  - Set the Voice URL to `POST https://<your-domain>/twilio/voice`.
  - Optionally append `?business_id=<tenant_id>` if you are routing by query parameter per number.
- **Messaging webhook**:
  - Set the Messaging URL to `POST https://<your-domain>/twilio/sms`.
  - Optionally append `?business_id=<tenant_id>` similar to voice.

Verify a simple flow before going live:

- Call the number and confirm:
  - The assistant greets the caller.
  - It collects basic info and, if appropriate, schedules a test appointment.
- Send a test SMS and confirm:
  - Message is logged as a conversation (`channel="sms"`).
  - Opt-out keywords (`STOP`, `STOPALL`, etc.) are respected.


6. Observability During the Pilot
---------------------------------

Use logs and the dashboard to monitor behavior:

- **Logs** (depending on your logging sink):
  - Look for:
    - `conversation_start` and `appointment_created` events.
    - `twilio_voice_webhook`, `twilio_sms_webhook` entries.
    - Warnings like `twilio_voice_business_suspended`, `twilio_signature_missing`, `twilio_signature_invalid`.

- **Dashboard**:
  - **Tomorrow's Schedule** - owner view of upcoming work.
  - **Today's Jobs** - owner summary of today's appointments (with an audio variant for voice-friendly playback).
  - **Analytics** - counts of appointments and conversation QA metrics.
  - **Emergency Jobs** - recent emergency appointments.
  - **Recent Conversations** & **Flagged Conversations (QA)** - to review call/chat quality and outcomes.
  - **Reschedule Requests** - appointments marked `PENDING_RESCHEDULE` (typically triggered by SMS "RESCHEDULE" flows).
  - **SMS Usage** - per-tenant SMS volume broken down by owner alerts vs customer-facing messages, plus Twilio voice/SMS webhook counts and error rates for the current tenant.
  - **Tenant Usage (Admin)** - per-tenant appointment counts, emergency counts, SMS volume (owner vs customer), service-type mix, and pending reschedules.
  - **Twilio / Webhook Health (Admin)** - aggregate voice/SMS webhook counts and errors by provider and tenant.


7. Handling Issues During the Pilot
-----------------------------------

If a tenant reports problems (e.g., "calls aren't working", "SMS not sending"):

1. Check **Twilio / Webhook Health** for recent voice/SMS traffic and errors.
2. Check logs for:
   - Signature warnings or errors on `/twilio/*` routes.
   - HTTP 4xx/5xx spikes for a particular tenant.
3. Verify tenant status and keys:
   - Ensure the tenant is not `SUSPENDED` unless intentionally paused.
   - Confirm Twilio webhook URLs are correct and match the intended `business_id` / tenant.
4. Rotate credentials if needed:
   - `POST /v1/admin/businesses/{id}/rotate-key` if an API key leaked.
   - `POST /v1/admin/businesses/{id}/rotate-widget-token` if a widget token leaked.
   - Update any embed snippets or clients using old values.


8. Wrapping Up the Pilot
------------------------

After the pilot window:

- Review logged conversations and QA outcomes to refine call flows, prompts, and emergency rules.
- Export per-tenant usage via:
  - `GET /v1/admin/businesses/usage.csv`
  - Use it for pilot reporting (appointments booked, emergencies handled, SMS volume).
- Decide on next steps:
  - Broader rollout to more tenants.
  - Targeted feature enhancements (e.g., richer QA taxonomy, more analytics, improved call scripts) based on pilot findings.
