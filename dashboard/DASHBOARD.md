Owner & Admin Dashboards
=========================

This directory contains the static HTML/JS dashboards for the AI Telephony Service & CRM backend.

- `index.html` ƒ?" owner dashboard for a single tenant.
- `admin.html` ƒ?" platform/admin dashboard for managing tenants and Twilio/webhook health.

Both dashboards expect a backend running at `http://localhost:8000` by default and communicate via
JSON APIs using the headers described in `README.md` and `API_REFERENCE.md`.


Owner Dashboard (`index.html`)
------------------------------

Key header inputs:

- `X-API-Key` ƒ?" tenant API key.
- `X-Owner-Token` ƒ?" owner/dashboard token.

Main cards and the endpoints they rely on:

- **Getting Started**
  - Shows basic steps to verify connectivity (backend health, tenant label, environment) and how to
    set `X-API-Key` / `X-Owner-Token`.

- **Owner KPIs**
  - Aggregates a few high-level metrics from existing owner APIs (customers analytics, SMS metrics,
    time-to-book, conversion funnel, and data completeness) into a single compact card so owners can
    see repeat share, SMS confirmation share, average time-to-book, leadƒ+'booked conversion, and data
    completeness at a glance.

- **Scheduling Rules**
  - Surfaces business hours, closed days, and basic capacity rules as configured on the `Business`
    row and used by the calendar service.

- **Tomorrowƒ?Ts Schedule**
  - Uses `/v1/owner/schedule/tomorrow` (and its audio variant) to show upcoming appointments and a
    voice-friendly summary for the next day.

- **Todayƒ?Ts Jobs**
  - Uses `/v1/owner/summary/today` to show counts for emergency vs standard appointments.

- **Analytics (Today & Recent Window)**
  - Aggregates appointment counts by status and emergency flag from `/v1/owner/service-mix` and
    related analytics endpoints.

- **Lead Sources & Follow-ups**
  - Uses owner analytics and `/v1/owner/followups` to show which lead sources are performing and
    whether recent leads have been contacted or booked.

- **Retention & Campaigns**
  - Uses `/v1/owner/retention` and `metrics.retention_by_business` to summarize retention SMS
    campaigns driven by `/v1/retention/send-retention`.

- **Pipeline (Last 30 Days)**
  - Uses `/v1/owner/pipeline?days=N` to show a simple pipeline view of appointments by stage and
    status.

- **Upcoming Workload (Next 7 Days)**
  - Uses `/v1/owner/workload/next?days=N` to show upcoming jobs, emergency load, and basic capacity
    signals.

- **Customers & Economics / Service Value by Type / Neighborhood Heatmap / Time to Book / Conversion Funnel**
  - Use `/v1/owner/customers/analytics`, `/v1/owner/service-economics`, `/v1/owner/neighborhoods`,
    `/v1/owner/time-to-book`, and `/v1/owner/conversion-funnel` to provide simple customer
    analytics, estimated value by service type, neighbourhood density, time-to-book metrics, and
    per-channel conversion from leads to booked appointments.

- **Data Completeness**
  - Uses `/v1/owner/data-completeness` to surface how many customers and appointments have key
    fields populated (emails, addresses, service types, estimated values, and lead sources) plus
    simple completeness scores so owners can prioritize cleanup.

- **Appointments (Filtered)**
  - Uses `/v1/crm/appointments` with filters (status, date window, emergency flag) to show a
    filterable subset of appointments.

- **SMS Usage**
  - Uses `/v1/owner/sms-metrics` to display per-tenant SMS volume broken down into owner vs
    customer messages, plus follow-ups and retention SMS counts.
  - Also surfaces SMS-driven events (confirmations, cancellations, reschedule requests, opt-outs,
    and opt-ins) and simple shares that estimate what portion of all confirmed/cancelled
    appointments were driven by SMS.

- **Emergency Jobs**
  - Uses `/v1/owner/service-mix` and appointment lists to highlight emergency appointments.

- **Recent Conversations / QA Queue / Callback Queue**
  - Use `/v1/owner/conversations/review`, `/v1/owner/callbacks`, and related CRM endpoints to:
    - Show recent/flagged conversations by channel (phone, web, SMS).
    - Surface flagged or emergency-tagged conversations for QA.
    - Show the callback queue backed by `metrics.callbacks_by_business`.

- **Data Export & Governance**
  - Uses `/v1/owner/export/service-mix.csv` and `/v1/owner/export/conversations.csv` to provide
    simple CSV exports for analytics and QA.


Admin Dashboard (`admin.html`)
------------------------------

Key header inputs:

- `X-Admin-API-Key` ƒ?" admin API key (required).

Main cards and the endpoints they rely on:

- **Tenants**
  - Uses:
    - `GET /v1/admin/businesses` to list tenants.
    - `PATCH /v1/admin/businesses/{business_id}` to update tenant status and notification fields.
    - `POST /v1/admin/businesses/{business_id}/rotate-key` and
      `POST /v1/admin/businesses/{business_id}/rotate-widget-token` for key rotation.
    - `POST /v1/admin/demo-tenants` for seeding demo tenants in non-production environments.

- **Tenant Usage**
  - Uses:
    - `GET /v1/admin/businesses/usage` and `GET /v1/admin/businesses/usage.csv` for per-tenant
      appointment and emergency counts, SMS volume (owner vs customer), and service-type mix.

- **Twilio / Webhook Health**
  - Uses:
    - `GET /v1/admin/twilio/health` together with `/metrics` and `twilio_by_business` to show
      Twilio voice/SMS request and error counts for each tenant.

- **Safety & Production Checklist**
  - Uses backend configuration and a subset of metrics to show whether key production settings
    (admin key, `REQUIRE_BUSINESS_API_KEY`, Twilio signature verification, DB/Redis/Session
    backing) are configured according to `DEPLOYMENT_CHECKLIST.md` and `SECURITY.md`.


Hosting Dashboards on GCP Storage
---------------------------------

The dashboards in this directory are static HTML/JS files that call the backend over HTTPS. A
minimal production setup on GCP is:

- Build nothing special: `dashboard/index.html` and `dashboard/admin.html` are already static.
- Create a Cloud Storage bucket for static hosting (example):

  ```bash
  gcloud storage buckets create gs://ai-telephony-dash-YOUR_PROJECT_ID \
    --location=us-central1 \
    --uniform-bucket-level-access
  ```

- Upload dashboard files:

  ```bash
  gcloud storage cp dashboard/* gs://ai-telephony-dash-YOUR_PROJECT_ID
  ```

- Configure the bucket for static site defaults:

  ```bash
  gcloud storage buckets update gs://ai-telephony-dash-YOUR_PROJECT_ID \
    --web-main-page-suffix=index.html \
    --web-error-page=index.html
  ```

- For a real deployment, front the bucket with an HTTPS load balancer + Cloud CDN and map a domain
  such as `dash.example.com` to the bucket, while the backend runs behind `https://api.example.com`
  on GKE (see `DEPLOYMENT_GCP_GKE.md`). Ensure CORS or same-origin configuration matches your API
  domain so XHR calls from the dashboards to the backend succeed.
