Security Policy
===============

This document describes how security should be approached for the AI Telephony Service & CRM
platform described in this repository. It is informed by the RavDevOps Engineering Code Culture &
Safety Standard and the domain requirements of the Bristol Plumbing assistant.


Source PDFs & Traceability
--------------------------

This security policy is derived from:

- `Project_Engineering_Whitepaper.pdf` – core engineering rules (code quality, testing, operations,
  incident response, and safety).
- `Bristol_Plumbing_Project_Plan.pdf` – non‑functional requirements around reliability, data
  protection, and cloud architecture.
- `Bristol_Plumbing_Analysis.pdf` – the nature of customer and job data handled for Bristol
  Plumbing.
- `Bristol_Plumbing_Implementation.pdf` – how AI, telephony, and cloud services are combined in a
  production‑style deployment.

The concrete controls below are intended to keep implementations consistent with these documents.


Scope
-----

Security considerations here apply to:

- Backend services (voice assistant APIs, scheduling, CRM).
- Frontend dashboard and any website widget.
- Data stores holding customer information, appointments, conversations, and logs.
- Integrations with third‑party providers (Google Calendar, SMS gateway, cloud hosting).

Environment separation and data isolation should follow the RavDevOps engineering guidelines:

- Use distinct environments (dev, staging, production) with separate credentials.
- Do not point local or test deployments at production data stores.
- Ensure each tenant's data is logically isolated and, where feasible, separated at the
  infrastructure level (e.g., per‑tenant projects or schemas for high‑sensitivity deployments).


Data Classification & Handling
------------------------------

The system processes data that must be treated as sensitive:

- **Customer identity & contact data**
  - Names, phone numbers, email addresses, and physical addresses.

- **Service & job data**
  - Appointment times, service types, job notes, and equipment details.

- **Conversation metadata**
  - Timestamps, channels (phone/web/SMS), and summaries.
  - Optional transcripts or recordings if configured and legally permissible.

- **Business configuration & secrets**
  - API keys, OAuth tokens, and service configuration (e.g., emergency rules, pricing notes).

Guidelines:

- Encrypt all data in transit using TLS 1.2+.
- Use cloud‑native encryption at rest for databases and storage.
- Store secrets only in a dedicated secrets manager (e.g., GCP Secret Manager), never in source
  control.
- Apply the principle of least privilege to all service accounts and IAM roles.


Authentication & Authorization
------------------------------

- Require strong authentication for dashboard access (password + optional MFA).
- Use OAuth for Google Calendar access with scoped permissions (read/write only to the relevant
  calendar).
- Isolate each business's data logically and, where appropriate, by project/tenant boundary.
- Implement role‑based access control (RBAC):
  - Owner: full access to configuration, customers, and appointments.
  - Staff: limited access to schedules and job notes as appropriate.
  - No anonymous access to internal APIs or dashboards.

Concretely, for the backend described here:

- Protect `/v1/admin/*` with an admin API key:
  - Set `ADMIN_API_KEY` in the environment.
  - Require callers to send `X-Admin-API-Key: <ADMIN_API_KEY>` for all admin operations (business
    creation, key rotation, demo tenant seeding).
- Protect tenant‑scoped CRM and owner APIs (`/v1/crm/*`, `/v1/owner/*`):
  - Use per‑business API keys stored in the `Business` table.
  - Require callers to send `X-API-Key: <business_api_key>` and/or `X-Business-ID`.
  - In production, set `REQUIRE_BUSINESS_API_KEY=true` so requests without any tenant identifier
    are rejected rather than silently falling back to the default tenant.


Secure Development Practices
----------------------------

Align implementation with the engineering whitepaper:

- Write readable, maintainable code; avoid cleverness that obscures security‑relevant behavior.
- Validate all external inputs at boundaries (API payloads, web forms, telephony metadata).
- Avoid ad hoc global mutable state; rely on well‑defined services and data stores.
- Enforce strong static analysis and linting in CI; reject merges that introduce new
  security‑relevant warnings.
- Treat compiler and linter warnings as issues to fix, not ignore.
- Use deterministic builds and pinned dependencies to avoid supply‑chain surprises.

Additional expectations from the RavDevOps standard:

- Require code review for all changes that touch authentication, authorization, or data access.
- Keep security‑sensitive logic (e.g., Twilio signature verification, tenant resolution, opt‑out
  handling) in dedicated, unit‑tested helpers rather than scattered inline.
- Prefer small, reversible changes; when in doubt, split risky refactors into multiple steps.
- Maintain a documented threat model for telephony/web entry points and update it as new features
  (e.g., widget, multi‑tenant admin) are added.


Logging, Monitoring & Incident Response
---------------------------------------

- Log security‑relevant events:
  - Authentication successes/failures.
  - Privilege changes.
  - Configuration changes (e.g., hours, emergency rules, SMS templates).
  - Access to sensitive records where practical.

- Aggregate logs centrally with access controls and retention policies.
- Define and monitor SLOs for availability and latency, with alerts on meaningful deviations.
- Establish an incident response process:
  - Detect -> triage -> contain -> remediate -> learn (postmortem).
  - Postmortems are blameless and focus on systemic fixes.


Telephony, Voice & Recordings
-----------------------------

- Only capture recordings or full transcripts if:
  - There is a clear business need.
  - Applicable law (e.g., one‑party or two‑party consent rules) is followed.
  - Callers are informed about recording where required.
- Prefer storing concise conversation summaries instead of full raw transcripts when possible.
- Protect any audio/text data as sensitive; restrict access to authorized personnel only.


Vulnerability Reporting
-----------------------

If you believe you have found a security vulnerability in an implementation based on this design:

- Do **not** publicly disclose details in a public issue tracker.
- Contact the project owner or security contact for the deployed system through a private channel
  (for example, a dedicated security email address or internal ticketing system).
- Provide sufficient detail to reproduce the issue (affected component, steps to reproduce, impact).

The team operating a deployment of this system is expected to:

- Acknowledge reports promptly.
- Triage and remediate issues according to severity.
- Communicate status and resolution timelines to reporters when appropriate.

