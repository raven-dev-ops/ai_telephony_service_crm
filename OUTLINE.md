Project Outline
===============

This outline summarizes the AI Telephony Service & CRM project for trades businesses, using Bristol
Plumbing as the primary reference customer. It is distilled from:

- `Bristol_Plumbing_Analysis.pdf`
- `Bristol_Plumbing_Implementation.pdf`
- `Bristol_Plumbing_Project_Plan.pdf`
- `Project_Engineering_Whitepaper.pdf`


1. Domain & Business Context
----------------------------

- **Customer archetype**
  - Local, family-owned plumbing contractor.
  - Service area: Greater Kansas City metro, with focus on Johnson County, KS.
  - Services: tankless water heaters (signature specialty), general plumbing repairs, water heaters,
    leak detection, sump pumps, gas lines, backflow testing, and emergency plumbing.
  - Reputation: trusted local resource with strong referrals and word-of-mouth.

- **Operational pain points**
  - Owner frequently in the field doing jobs and unable to answer every call.
  - Missed calls translate directly into missed revenue and weaker customer experience.
  - Job durations are variable and make scheduling by hand difficult.
  - No centralized, structured CRM history of customers and their service history.

- **Marketing and growth**
  - Strong local SEO presence via website and listings (Google Maps, Yelp, manufacturer "Find a Pro").
  - Emphasis on tankless water heater expertise and trustworthy service.
  - Targeted campaigns to affluent ZIP codes for premium upgrades (tankless + filtration).


2. Product Vision
-----------------

Build a voice-first AI assistant and lightweight CRM that:

- Acts as a 24/7 receptionist (no busy signals, no unreturned voicemails).
- Schedules and manages appointments using natural language conversation.
- Maintains a cloud-based history of customers, jobs, and conversations.
- Notifies the owner immediately of new opportunities and emergencies.
- Surfaces simple analytics about demand, revenue, and service mix.

The initial reference deployment is for Bristol Plumbing, but the design generalizes to other
service businesses (e.g., HVAC, electrical, home services).


3. Functional Scope
-------------------

3.1 Voice Assistant & Telephony

- Receive inbound calls from customers.
- Greet callers and collect:
  - Name and contact information.
  - Service address.
  - Problem description and urgency.
- Classify the request:
  - New job vs. follow-up vs. general inquiry.
  - Emergency vs. standard service (use keywords and intent).
- Confirm and summarize what the customer requested.
- For emergencies, prioritize escalation and immediate notification.

3.2 Scheduling & Calendar Integration

- Integrate with Google Calendar using OAuth.
- For each job type, maintain default duration assumptions.
- Find available time slots and propose options to the caller.
- Create, update, or cancel events on the calendar.
- Tag emergency appointments and mark special instructions in the event details.

3.3 CRM & Data Management

- Maintain a customer record with:
  - Names, phone numbers, emails, addresses.
  - Previous jobs, dates, and notes.
  - Equipment details where relevant (e.g., tankless heater brand/model).
- Recognize repeat customers by phone number or name.
- Auto-populate known fields when scheduling new work.
- Store conversation summaries and relevant metadata (no raw recordings by default, unless
  explicitly configured and consented).

3.4 Business Dashboard (Web)

- Authenticated web app for the owner and staff.
- Views:
  - Upcoming schedule (calendar and list views).
  - Conversation logs and summaries for QA and training.
  - Customer profiles and job history.
  - Analytics on jobs, revenue estimates, and common service areas.
- Configuration:
  - Business hours, blackout dates, and service menu.
  - Emergency keywords and escalation rules.
  - Notification preferences (SMS/email).

3.5 Notifications & Messaging

- SMS/text alerts to the owner for:
  - New inquiries and jobs.
  - Emergency-flagged calls.
  - Changes to scheduled appointments.
- Customer messaging for:
  - Appointment confirmations and reminders.
  - Reschedule notifications.

Additional notes:

- Customer SMS flows should recognize standard opt-out keywords (e.g., STOP, STOPALL, UNSUBSCRIBE,
  CANCEL, END, QUIT) and stop sending customer-facing texts to opted-out numbers while internal
  owner alerts continue.

3.6 Future Enhancements

- Website chatbot/voice widget that uses the same backend as the phone assistant.
- Multi-channel input (phone, web, SMS) with a unified conversation history.
- Multi-tenant support for multiple trades businesses.


4. Technical Architecture (Planned)
-----------------------------------

- **Backend (Python)**
  - Web service (e.g., FastAPI/Flask) exposing APIs for:
    - Call/session management.
    - Scheduling and calendar operations.
    - CRM operations (customers, jobs, notes).
  - Integrations:
    - STT and TTS providers.
    - Google Calendar.
    - Cloud database (Firestore or Cloud SQL).
    - SMS gateway (e.g., Twilio).
  - Internal modules for:
    - Intent classification and dialogue.
    - Emergency detection and tagging.
    - Analytics aggregation.

- **Frontend (dashboard & widget)**
  - Current implementation: static HTML/JS in `dashboard/` and `widget/`, served as simple web pages.
  - Planned evolution: business dashboard as a PWA (Node.js + JS framework) with authentication and authorization.
  - Views described in section 3.4.

- **Infrastructure (GCP)**
  - Docker containers for backend and frontend.
  - Kubernetes (GKE) for deployment and auto-scaling.
  - HTTPS ingress, secrets management, and observability through GCP tools.

- **Engineering practices**
  - Follow the RavDevOps Engineering Code Culture & Safety Standard:
    - Boring, readable code.
    - Deterministic builds and environments.
    - Strong test pyramid and static analysis.
    - Design-before-implementation for non-trivial features.
    - Blameless postmortems and psychological safety.


5. Implementation Phases (High-Level)
-------------------------------------

The project plan suggests a phased approach:

1. **Phase 0 - Foundations**
   - Finalize requirements with the reference customer (Bristol Plumbing).
   - Capture call scripts, FAQs, and emergency procedures.
   - Set up cloud project, CI/CD, and basic repo structure.

2. **Phase 1 - Core Voice Assistant & Scheduling**
   - Implement backend with STT/TTS and basic dialogue flows.
   - Integrate with Google Calendar for owner-only scheduling.
   - Support owner voice queries about schedule and job details.

3. **Phase 2 - CRM & Dashboard**
   - Add customer database and appointment history.
   - Build the web dashboard with schedule, conversation logs, and basic analytics.
   - Add configuration UI for hours, services, and emergency rules.

4. **Phase 3 - Notifications & Reliability**
   - Integrate SMS for owner and customer notifications.
   - Harden reliability with monitoring, alerts, and SLOs.
   - Apply the engineering safety standard (testing, static analysis, rollout practices).

5. **Phase 4 - Website Widget & Multi-Channel**
   - Embed the assistant on the website as a chatbot/voice widget.
   - Unify conversations across phone and web.
   - Explore multi-tenant architecture for additional trades customers.

Each phase should be backed by a design document consistent with the
`Project_Engineering_Whitepaper.pdf` guidance.
