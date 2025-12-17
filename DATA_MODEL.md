Data Model Overview
====================

This document summarizes the core data model implemented in `backend/app/db_models.py` and exposed
through `backend/app/models.py` and the repositories. It is intended as a quick reference for how
entities relate and which fields are important for scheduling, SMS, and analytics.


Entities
--------

**Business**

- Represents a tenant (e.g., Bristol Plumbing) and holds configuration.
- Key fields:
  - `id`: string tenant identifier.
  - `name`: display name.
  - `status`: `"ACTIVE"` or `"SUSPENDED"` (controls whether Twilio/CRM/widget traffic is served).
  - `api_key`: secret used as `X-API-Key` on CRM/owner/admin routes.
  - `widget_token`: public token used as `X-Widget-Token` by the web chat widget.
  - `calendar_id`: optional override for `GOOGLE_CALENDAR_ID`.
  - `owner_phone`: phone number for owner alerts.
  - `emergency_keywords`: optional comma-separated overrides for emergency detection.
  - `default_reminder_hours`: per-tenant reminder window for `/v1/reminders/send-upcoming`.
  - `language_code`: preferred language (e.g., `en`, `es`).
  - `vertical`: business type (e.g., `plumbing`, `hvac`).
  - Capacity & schedule fields used by the calendar service:
    - `open_hour`, `close_hour`, `closed_days`.
    - `max_jobs_per_day`, `reserve_mornings_for_emergencies`.
    - `travel_buffer_minutes`.
  - `service_duration_config`: optional overrides for service-type durations.

**Customer**

- Represents an end customer for a given business.
- Key fields:
  - `id`: customer identifier.
  - `business_id`: owning tenant.
  - `name`, `phone`, `email`, `address`.
  - `tags`: free-form labels (e.g., `vip`, `tankless`, `warranty`).
  - `sms_opt_out`: boolean flag set when the customer sends STOP/UNSUBSCRIBE keywords.

**Appointment**

- Represents a scheduled job/visit.
- Key fields:
  - `id`: appointment identifier.
  - `business_id`: owning tenant.
  - `customer_id`: linked `Customer`.
  - `start_time`, `end_time`: UTC windows for the visit.
  - `service_type`: normalized label from the conversation (`tankless_water_heater`, `drain_or_sewer`, etc.).
  - `description`: free-form notes about the job.
  - `is_emergency`: whether the job was classified as an emergency.
  - `status`: e.g., `SCHEDULED`, `CONFIRMED`, `COMPLETED`, `CANCELLED`, `PENDING_RESCHEDULE`.
  - `calendar_event_id`: ID of the corresponding Google Calendar event (when calendar is enabled).
  - `tags`: additional labels (e.g., `callback`, `promo:fall-drain-cleaning`).
  - `lead_source`: normalized channel label (phone/web/SMS + optional campaign).
  - `estimated_value`: numeric estimate for analytics.
  - `job_stage`: rough pipeline stage (e.g., `New`, `In Progress`, `Pending Reschedule`).
  - `technician_id`: optional link to a technician.
  - `quoted_value`, `quote_status`: simple quote tracking heuristics.
  - `reminder_sent`: boolean flag used by `/v1/reminders/send-upcoming` to avoid duplicates.

**Conversation**

- Represents a logical conversation thread for a channel.
- Key fields:
  - `id`: conversation identifier.
  - `business_id`: owning tenant.
  - `customer_id`: linked `Customer` when the customer is known.
  - `session_id`: links voice/SMS/telephony sessions when applicable.
  - `channel`: `"phone"`, `"sms"`, `"web"`, or similar.
  - `created_at`: timestamp for when the conversation started.
  - `messages`: set of `ConversationMessage` rows.
  - `flagged_for_review`: whether the conversation has been flagged for QA.
  - `tags`: QA or routing tags (e.g., `emergency`, `price_shopper`).
  - `outcome`: high-level outcome text (e.g., `"booked"`, `"lost"`, `"needs follow-up"`).
  - `notes`: additional QA notes or operator comments.

**ConversationMessage**

- Represents one utterance inside a conversation.
- Key fields:
  - `id`: message identifier.
  - `conversation_id`: owning `Conversation`.
  - `role`: `"user"` or `"assistant"`.
  - `text`: plain text content of the message.
  - `timestamp`: when the message was recorded.

**CallbackItem (metrics-backed)**

- Not stored as a DB row, but exposed via `metrics.callbacks_by_business`.
- Tracks potential callback leads when calls are missed or not booked.
- Key fields:
  - `phone`, `first_seen`, `last_seen`, `count`.
  - `channel`, `lead_source`.
  - `status` and `last_result` for tracking follow-up attempts.
  - `reason` (e.g., `MISSED_CALL`).


Relationships
-------------

- A `Business` owns many `Customer`, `Appointment`, and `Conversation` entities, all scoped by `business_id`.
- A `Customer` can have many `Appointment` and `Conversation` rows.
- A `Conversation` has many `ConversationMessage` rows.
- An `Appointment` can be linked back to one or more `Conversation` rows via customer and tags/outcomes, and is exported for analytics and CSV downloads.


Where to Look in Code
---------------------

- SQLAlchemy models: `backend/app/db_models.py`.
- In-memory / DB repositories:
  - `backend/app/repositories.py` (customers, appointments, conversations).
- Pydantic/API models:
  - `backend/app/models.py` (entity-level).
  - `backend/app/routers/crm.py` and `backend/app/routers/owner.py` (response schemas).

