Product Backlog
===============

This backlog is derived from the Bristol Plumbing business analysis, implementation ideas, the
project plan PDFs, and the RavDevOps engineering whitepaper. Items are grouped by major milestone.
Status labels (`Todo`, `In Progress`, `Done`) are placeholders for future tracking.


Engineering & Safety Expectations
---------------------------------

When implementing items from this backlog:

- Follow the RavDevOps engineering standard: boring, readable code; deterministic builds; strong
  tests and static analysis.
- Treat emergency detection, scheduling, Twilio/SMS handling, and tenant isolation as
  safety-critical paths that require tests and careful review.
- Prefer small, reversible changes and clear rollout/rollback plans for production-facing work.


MVP 0 - Foundations & Environment
---------------------------------

- **Define reference call flows and scripts**  
  Status: Todo, Priority: High  
  - Capture greeting, intake, scheduling, and emergency handling scripts from Bristol Plumbing's
    real call patterns.

- **Document emergency classification rules**  
  Status: Todo, Priority: High  
  - Enumerate phrases and symptoms that should trigger emergency handling (e.g., "burst pipe",
    "no water", "sewage backup").

- **Select STT/TTS stack and hosting model**  
  Status: Todo, Priority: High  
  - Choose concrete STT and TTS providers or models (e.g., Whisper/Coqui) and strategy for
    real-time performance.

- **Provision GCP project and base infrastructure**  
  Status: Todo, Priority: High  
  - Create a GCP project, enable required APIs, and provision basic networking, container registry,
    and monitoring.

- **Establish repo standards & CI skeleton**  
  Status: Done, Priority: Medium  
  - Apply RavDevOps engineering standards (linting, tests, formatting) and set up initial CI
    workflow (build + tests).


MVP 1 - Core Voice Assistant & Scheduling
-----------------------------------------

- **Implement call/session API in backend**  
  Status: Done, Priority: High  
  - Expose endpoints for starting, updating, and ending call sessions, with state tracked in a
    datastore.

- **Integrate speech-to-text (STT)**  
  Status: In Progress, Priority: High  
  - Wire audio input from telephony provider to an STT service; ensure low latency and accuracy for
    noisy, real-world audio.

- **Integrate text-to-speech (TTS)**  
  Status: In Progress, Priority: High  
  - Provide natural, professional voice output to callers and the owner; support configurable voice
    options.

- **Implement intent recognition and dialogue flows**  
  Status: In Progress, Priority: High  
  - Support intents such as new appointment, reschedule, cancel, ask-a-question, and emergency
    escalation.

- **Google Calendar integration (read/write)**  
  Status: In Progress, Priority: High  
  - Authenticate with the owner's Google account; check availability, create events, and handle
    updates/cancellations.

- **Service duration profiles**  
  Status: Todo, Priority: Medium  
  - Configure default durations per service type; ensure scheduling logic respects these blocks.


MVP 2 - CRM & Business Dashboard
--------------------------------

- **Design and implement data model**  
  Status: Done, Priority: High  
  - Entities: Business, Customer, Appointment, Conversation, Job, Channel, User.

- **Create customer and appointment storage**  
  Status: Done, Priority: High  
  - Implement persistence in Firestore or Cloud SQL with indexes for common queries (by phone,
    date, status).

- **Implement repeat-customer recognition**  
  Status: Todo, Priority: High  
  - Match phone number/name to existing records and auto-populate contact and address details.

- **Conversation logging and summarization**  
  Status: Todo, Priority: Medium  
  - Store call transcripts and/or concise summaries with metadata; ensure privacy and retention
    rules are followed.

- **Build business dashboard (PWA)**  
  Status: In Progress, Priority: High  
  - Implement a secure web app with views for schedules, customers, and conversation logs.

- **Owner voice-query interface**  
  Status: In Progress, Priority: Medium  
  - Allow the owner to query schedule and job details by voice via phone or web.


MVP 3 - Notifications, Reliability & Operations
-----------------------------------------------

- **Integrate SMS notifications (owner)**  
  Status: In Progress, Priority: High  
  - Send SMS alerts for new leads, emergencies, and same-day changes using Twilio or similar.

- **Customer SMS confirmations & reminders**  
  Status: In Progress, Priority: Medium  
  - Implement configurable reminder windows and templates; handle simple YES/NO replies for
    confirmations and reschedules.

- **Monitoring, metrics, and alerting**  
  Status: In Progress, Priority: High  
  - Define SLOs (availability, P95/P99 latency, error rates); instrument services and configure
    dashboards and alerts.

- **Operational runbooks and incident process**  
  Status: Done, Priority: Medium  
  - Create on-call procedures, troubleshooting guides, and blameless postmortem templates in
    line with the engineering whitepaper.

- **Security hardening and audit logging**  
  Status: Todo, Priority: High  
  - Ensure TLS everywhere, principle-of-least-privilege IAM, secrets management, and
    audit-quality logs for key actions.


MVP 4 - Website Widget & Multi-Channel
--------------------------------------

- **Website chat/voice widget**  
  Status: In Progress, Priority: Medium  
  - Embed the assistant on the business website for text or voice chat, reusing backend logic.

- **Unified conversation history across channels**  
  Status: Todo, Priority: Medium  
  - Present calls, web chats, and SMS threads in a single conversation timeline per customer.

- **Multi-tenant foundations**  
  Status: Done, Priority: Medium  
  - Introduce a tenant isolation model so multiple businesses can safely share the platform.

- **Billing, plans, and quotas (if SaaS)**  
  Status: Todo, Priority: Low  
  - Design subscription plans, quotas, and usage tracking if the platform is offered beyond the
    initial reference customer.


Cross-Cutting Engineering Work
------------------------------

- **Adopt RavDevOps engineering standards**  
  Status: In Progress, Priority: High  
  - Apply rules from `Project_Engineering_Whitepaper.pdf` for code style, testing, safety, and
    operations.

- **Automated testing strategy**  
  Status: In Progress, Priority: High  
  - Define and implement unit, integration, and end-to-end tests for critical flows (call handling,
    scheduling, emergency paths).

- **Performance and load testing**  
  Status: Todo, Priority: Medium  
  - Validate behavior at 10x expected load; ensure voice latency and scheduling performance stay
    within agreed bounds.
