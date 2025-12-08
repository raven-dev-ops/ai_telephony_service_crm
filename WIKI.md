Project Wiki
============

This wiki captures domain knowledge and design details for the AI Telephony Service & CRM, based on
the Bristol Plumbing PDFs and the RavDevOps engineering whitepaper.


1. Domain Summary - Bristol Plumbing
------------------------------------

- **Business profile**
  - Family-owned plumbing contractor founded in 2003 by a Master Plumber.
  - Based in Merriam, Kansas; serves the greater Kansas City metro on both Kansas and Missouri
    sides.
  - Reputation for honesty, reliability, and high-quality craftsmanship.

- **Service area**
  - Key suburbs on the Kansas side: Overland Park, Olathe, Shawnee, Lenexa, Leawood, Prairie
    Village, and others across Johnson County.
  - Key neighborhoods on the Missouri side: areas like Waldo and Brookside in Kansas City, MO.

- **Services**
  - General plumbing repairs and fixtures (faucets, toilets, sinks, disposals, hose bibs).
  - Traditional water heater repair/replacement.
  - Tankless water heaters (Navien, Rinnai, Noritz, etc.) as a primary specialty.
  - Leak detection and pipe repair/re-piping.
  - Sump pumps, gas line installation, backflow testing and certification.
  - Emergency plumbing services for urgent issues.

- **Brand positioning**
  - "Kansas City's Tankless Water Heater Expert."
  - "Local & trusted" in Johnson County with strong word-of-mouth referrals.


2. Personas & Users
-------------------

- **Owner (primary user)**
  - One-person or small-team operator, frequently in the field on jobs.
  - Needs a way to avoid missed calls while still doing hands-on work.
  - Wants fast visibility into tomorrow's schedule and job details via phone or dashboard.

- **Customer (caller)**
  - Homeowners or small commercial clients experiencing plumbing issues or planning upgrades.
  - Often calling from mobile devices; may be stressed if the issue is an emergency.
  - Needs quick reassurance, clear expectations, and a concrete appointment time.

- **Future roles**
  - Office assistant or dispatcher (if the business grows).
  - Additional technicians with read-only access to schedules and job notes.


3. Core Use Cases
-----------------

3.1 Inbound Call - Standard Service

- Caller describes a non-emergency issue (e.g., dripping faucet, running toilet).
- Assistant:
  - Greets caller and confirms they have reached Bristol Plumbing.
  - Collects name, phone, email (optional), and service address.
  - Captures a concise description of the issue.
  - Checks Google Calendar for suitable time slots, respecting service durations and travel.
  - Offers one or more options and confirms the chosen slot.
  - Creates an event in Google Calendar with structured details.
  - Sends confirmation by SMS or email if configured.

3.2 Inbound Call - Emergency

- Caller reports critical symptoms (e.g., "pipe burst", "basement is flooding", "no water at all",
  "sewage backing up").
- Assistant:
  - Quickly collects address and contact details.
  - Detects emergency keywords or intent and marks the call as high priority.
  - Notifies the owner immediately via SMS or phone bridge.
  - Offers the earliest possible appointment slot and explains any emergency surcharge policy if
    configured.
  - Tags the calendar event as an emergency job.

3.3 Returning Customer Scheduling Follow-Up

- Caller references a prior job ("You replaced my water heater last year").
- Assistant:
  - Recognizes repeat customer via phone number or name.
  - Retrieves prior appointment and job data (e.g., tankless heater installation, model/brand).
  - Uses past context to ask smarter questions and update the job notes.
  - Schedules the follow-up work and links it to the existing customer record.

3.4 Owner Voice Query

- Owner calls or uses a voice interface to ask:
  - "What's on my schedule tomorrow?"
  - "Read me the details for the Smith job."
  - "Any emergencies booked for this afternoon?"
- Assistant:
  - Authenticates the owner.
  - Reads back schedule details from Google Calendar and the CRM.
  - Optionally sends a summary via SMS for reference.

3.5 Example Call Flow - Standard Plumbing Job

The implemented `ConversationManager` in `backend/app/services/conversation.py` follows a simple,
state-machine style flow that matches the use cases above. A typical non-emergency call looks like:

1. **Greeting (`stage="GREETING"`)**
   - If the caller is recognized by phone, the assistant greets them as a returning customer
     (reusing their name when available).
   - Otherwise, it greets them as a new caller and asks for their name.

2. **Collect Name (`stage="ASK_NAME"`)**
   - Any non-empty answer is treated as a name (with light parsing).
   - The assistant confirms and moves on to ask for the service address.

3. **Collect Address (`stage="ASK_ADDRESS"`)**
   - If the caller is returning and an address is already on file, the assistant offers to reuse it
     ("I have your address as ... Does that still work for this visit?").
   - Otherwise, it asks for the full service address and stores it.

4. **Collect Problem Summary (`stage="ASK_PROBLEM"`)**
   - The assistant asks for a brief description of what is going on with the plumbing.
   - It infers a service type (e.g., tankless, water heater, drain/sewer, fixture/leak) and
     checks for emergency keywords (e.g., "burst", "no water", "sewage").

5. **Safety & Emergency Handling**
   - If emergency keywords are detected, the assistant:
     - Marks the session/appointment as an emergency.
     - Clearly states that it cannot call 911 and instructs the caller to contact emergency
       services themselves if life or safety is at risk.
     - Prioritizes earlier time slots when scheduling.

6. **Scheduling (`stage="PROPOSE_SLOT"` and confirmation)**
   - The assistant asks about timing preferences (e.g., "Are you hoping for today, tomorrow, or a
     specific day?").
   - It calls the calendar service to find a suitable slot based on service type, duration, business
     hours, and existing events.
   - It proposes a slot and asks the caller to confirm or request a different time.

7. **Confirmation & Wrap-Up**
   - Once a slot is accepted, the assistant:
     - Creates an appointment for the caller, tagged with service type, emergency status, and
       estimated value range.
     - Summarizes the booking details back to the caller.
     - Sends an SMS confirmation when SMS is configured and the caller has not opted out.


4. Data Model (Conceptual)
--------------------------

- **Business**
  - Represents a trades business (e.g., Bristol Plumbing).
  - Fields: name, contact details, service area, configuration.

- **Customer**
  - Fields: name, phone(s), email, addresses, notes.
  - Relationships: has many Appointments and Conversations.

- **Appointment**
  - Fields: business, customer, time window, service type, duration, emergency flag, status.
  - Linked to a corresponding Google Calendar event.

- **Conversation**
  - Fields: channel (phone, web, SMS), transcript or summary, timestamps, outcome.
  - Linked to Customer (if identified) and optionally to an Appointment.

- **User**
  - Fields: owner/staff identity, permissions, contact channels for notifications.

- **Configuration**
  - Business-level settings (hours, service menu, emergency rules, default durations).


5. Engineering & Operations Notes
---------------------------------

The `Project_Engineering_Whitepaper.pdf` sets the standards for how this system should be built and
operated:

- **Code & design**
  - Write boring, readable code.
  - Explicit design documents for non-trivial features.
  - Favor stateless services and explicit data stores.

- **Testing & quality**
  - Behavior-driven tests that verify observable outcomes (not internal wiring).
  - Strong unit test coverage plus targeted integration and end-to-end tests.
  - Static analysis as a gate; main branch must be free of new warnings.

- **Reliability & safety**
  - Design for failure: timeouts, retries, idempotency, and graceful degradation.
  - Optimize for P95/P99 latency, especially in voice paths.
  - Blameless postmortems with actionable follow-ups for incidents.

These principles should inform every code and infrastructure contribution to this project.


6. SMS Behavior & Opt-Out
-------------------------

- Treat appointment confirmations, reminders, and similar texts as transactional messages linked to
  specific jobs.
- Recognize standard opt-out keywords (e.g., STOP, STOPALL, UNSUBSCRIBE, CANCEL, END, QUIT) from
  customers and stop sending customer-facing SMS to opted-out numbers while leaving internal owner
  alerts unaffected.
- Periodically test opt-out flows (in non-production tenants) to ensure the system honors
  preferences and complies with relevant messaging guidelines.


7. Backend Auth & Packaging Notes
---------------------------------

- Editable installs use explicit setuptools discovery (`backend/pyproject.toml`) to package only the `app` module while excluding `alembic` and test code, preventing accidental inclusion of migration scaffolding.
- OAuth integration tests (`backend/tests/test_auth_integration_real_flows.py`) assert the Google authorization hostname equals `accounts.google.com` and validate expected query params (`client_id`, `state`) to guard against incomplete URL sanitization and catch misconfigurations early.
- OAuth test fixtures set `state_secret` for deterministic runs; ensure `GOOGLE_CLIENT_ID` and `LINKEDIN_CLIENT_ID` are configured or defaulted before running integration tests.
