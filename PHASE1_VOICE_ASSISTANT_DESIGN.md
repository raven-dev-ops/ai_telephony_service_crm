Phase 1 Design - Voice Assistant & Scheduling
=============================================

1. Purpose & Scope
------------------

This document defines the Phase 1 design for the AI telephony assistant that:

- Handles inbound calls for a trades business (Bristol Plumbing as reference).
- Conducts a basic conversational intake with callers.
- Schedules, modifies, and cancels appointments against Google Calendar.
- Supports owner voice queries about their schedule.

Out of scope for Phase 1 (but planned later):

- Web dashboard UI.
- Customer-facing website widget/chatbot.
- Multi-tenant support for multiple businesses.
- Advanced analytics and reporting.


2. Functional Requirements
--------------------------

2.1 Call Handling & Sessions

- Start a call session when a call is received from the telephony provider.
- Maintain a unique session ID per active call.
- Track session state (caller identified, address captured, appointment being scheduled, etc.).
- End the session when the call terminates or times out.

2.2 Conversational Intake

- Greet the caller and confirm they reached the correct business.
- Collect:
  - Caller name.
  - Callback phone number (if available) and confirm caller ID if used.
  - Service address.
  - Brief description of the problem.
  - Preferred date/time window (if the call is about scheduling).
- Detect whether the call is:
  - New appointment.
  - Reschedule/cancellation.
  - General question (informational only).

2.3 Emergency Detection (Basic)

- Identify potential emergencies based on keywords and patterns (e.g., "burst pipe",
  "flooding", "no water", "sewage").
- Mark sessions and resulting appointments with an emergency flag when triggered.
- Immediately notify the owner via SMS for emergency-flagged calls (Phase 1 may stub SMS).

2.4 Scheduling with Google Calendar

- Authenticate with Google Calendar using OAuth and a service configuration for the business.
- Map high-level service types to default duration blocks (e.g., 1 hour for typical repairs, 4 hours
  for tankless installation).
- Query availability within a time window based on the caller's preferences.
- Create events on the business calendar with:
  - Title (e.g., "Bristol Plumbing - [Service Type] - [Customer Name]").
  - Start/end times.
  - Location (address).
  - Description including problem summary and notes about emergency status.
- Allow callers to:
  - Confirm one of the proposed time slots.
  - Request a different time within constrained limits.
  - Cancel a previously created appointment (given enough identifying info).

2.5 Owner Voice Queries

- Accept calls or voice commands from the owner.
- Support simple queries such as:
  - "What's on my schedule tomorrow?"
  - "Any emergencies this afternoon?"
  - "Read me my first appointment for Monday."
- Read back a concise, voice-friendly summary based on Google Calendar and session data.


3. Non-Functional Requirements
------------------------------

- **Latency**:
  - Target < 500 ms perceived round-trip for most conversational turns.
  - Avoid long blocking operations in the hot path; use async where appropriate.
- **Availability**:
  - Design to tolerate transient failures of STT/TTS or Calendar APIs with retries and fallbacks.
- **Observability**:
  - Emit structured logs for each call session, including key decisions (emergency detection,
    scheduling actions, API errors).
- **Privacy & Security**:
  - No secrets in logs.
  - Use TLS for all external API calls.
  - Align with `SECURITY.md` and the RavDevOps engineering whitepaper.


4. High-Level Architecture
--------------------------

Phase 1 introduces the following components (backend-focused):

4.1 Telephony Integration Boundary

- Integrates with a telephony provider (e.g., Twilio, Plivo, or similar) via webhooks.
- Responsibilities:
  - Receive call events (call started, audio stream, call ended).
  - Forward audio (or text) to the Voice Session API.
  - Apply provider-specific requirements (signing secrets, callbacks).
- Implemented as a thin adapter around the main backend to keep the core logic provider-agnostic.

4.2 Voice Session Service (Backend Core)

- Exposes API endpoints such as:
  - `POST /v1/voice/session/start`
  - `POST /v1/voice/session/{session_id}/input` (audio or text)
  - `POST /v1/voice/session/{session_id}/end`
- Responsibilities:
  - Manage session state in a datastore (e.g., in-memory for early dev, Redis/DB later).
  - Interface with STT/TTS providers.
  - Invoke the Conversation Manager with recognized text and current state.
  - Return the next system utterance (text and synthesized audio) to the telephony adapter.

4.3 STT/TTS Integration Layer

- Abstracts speech providers.
- Interfaces:
  - `transcribe(audio_chunk) -> text`
  - `synthesize(text) -> audio_chunk`
- Implementation options:
  - Self-hosted open-source models (e.g., Whisper, Coqui) for full control.
  - Managed API providers where appropriate for rapid iteration.
- Must support streaming or chunked processing for low-latency interactions.

4.4 Conversation Manager

- Implements the conversational logic as a deterministic state machine and/or intent-based handler.
- Inputs:
  - Session state.
  - Latest user utterance (text).
- Outputs:
  - Next system utterance (text).
  - Updated session state.
  - Side-effects (e.g., "create appointment", "mark emergency", "call Calendar API").
- Initial implementation can be rule-based with:
  - State flags (e.g., `COLLECTING_NAME`, `COLLECTING_ADDRESS`, `SCHEDULING`, `EMERGENCY_FLOW`).
  - Intent classification via simple heuristics or a small model (later).

4.5 Scheduling & Calendar Service

- Encapsulates all interactions with Google Calendar.
- Responsibilities:
  - Token management and OAuth flows (stored securely, not in logs).
  - Availability search given:
    - Desired day or time window.
    - Default duration by service type.
    - Business hours and blackout dates (Phase 1 may hard-code).
  - Event creation, update, and cancellation.
- Exposes internal APIs such as:
  - `find_slots(preferences, duration) -> [slot]`
  - `create_event(appointment_data) -> event_id`
  - `cancel_event(event_id)`


5. Data Model (Phase 1)
-----------------------

Phase 1 focuses on minimal but structured data:

- **CallSession**
  - `id`: unique ID.
  - `caller_phone`: caller ID from provider.
  - `caller_name`: collected from dialog.
  - `address`: collected from dialog.
  - `problem_summary`: short text.
  - `is_emergency`: boolean.
  - `status`: `ACTIVE`, `COMPLETED`, `FAILED`, `ABANDONED`.
  - `created_at`, `updated_at`.

- **AppointmentDraft** (transient)
  - `session_id`: link to CallSession.
  - `requested_day/time_window`.
  - `service_type`.
  - `duration_minutes`.
  - `selected_slot`: chosen timeslot, if any.

Phase 2 will introduce persistent Customer/Appointment entities; for Phase 1, we only require what
is needed to create calendar events and complete a call.


6. External Interfaces & APIs
-----------------------------

6.1 Telephony Webhooks (Example)

- `POST /telephony/inbound`
  - Triggered when a call starts.
  - Validates signature from provider.
  - Creates a new CallSession via the Voice Session Service.

- `POST /telephony/audio`
  - Receives audio chunks or URLs.
  - Forwards audio to the STT layer and then into the Conversation Manager.

- `POST /telephony/end`
  - Marks the CallSession as completed or abandoned.

6.2 Owner Voice Interface

- `POST /v1/voice/owner/query`
  - Accepts owner-authenticated voice or text queries.
  - Returns spoken and/or textual summaries of schedule and jobs.


7. Dependencies & Assumptions
-----------------------------

- Google Calendar is the source of truth for scheduled work.
- STT/TTS providers are reachable with acceptable latency from the backend.
- Telephony provider webhooks are configured correctly and secured with signatures.
- Networking, TLS termination, and secret storage follow the practices outlined in
  `DEPLOYMENT_CHECKLIST.md` and `SECURITY.md`.

