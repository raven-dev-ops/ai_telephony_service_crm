# AI Telephony Service CRM - GitHub Issues Tasklist (Beta Backlog)

This file is structured so each **ISSUE** section can be pasted into a GitHub issue (or ingested by your VS Code workflow if it supports multi-issue markdown).

Conventions:
- **Priority:** `P0` (must ship for closed beta) through `P3` (nice-to-have)
- **Phase:** `phase/closed-beta`, `phase/open-beta`, `phase/ga`
- **Areas (labels):** `area/backend`, `area/dashboard`, `area/widget`, `area/chat`, `area/infra`, `area/integrations`, `area/security`, `area/ops`, `area/docs`
- **Types (labels):** `type/bug`, `type/feature`, `type/chore`, `type/security`, `type/perf`

---

## [EPIC][P0] Closed Beta Readiness - E2E quality gates
**Labels:** epic, priority: P0, phase/closed-beta, area/ops, area/backend, area/dashboard

### Why
Closed beta success depends on: stable call handling, correct scheduling, safe onboarding, and fast incident recovery.

### Sub-issues
- [ ] [P0] Define "Closed Beta Ready" Definition of Done (DoD)
  - [ ] Create a `BETA_DOD.md` checklist (functional + non-functional)
  - [ ] Add explicit pass/fail gates (security, perf, uptime, data retention)
  - [ ] Add "known limitations" section (what beta users must accept)
  - **AC:**
    - DoD is referenced from README/PLANNER and linked in the dashboard planner view

- [ ] [P0] End-to-end smoke suite (voice + sms + calendar + dashboard)
  - [ ] Create "happy path" scripted scenarios (new caller + schedule + confirmation)
  - [ ] Create "returning customer" scenario (lookup + reschedule)
  - [ ] Create "emergency" scenario (flag + owner notify + emergency slot)
  - [ ] Add CI job that runs against stubbed providers (no real external calls)
  - [ ] Add nightly job that can run against a staging tenant (guarded by secrets)
  - **AC:**
    - CI produces a single artifact report with scenario pass/fail and timings

- [ ] [P0] Beta instrumentation baseline
  - [ ] Define the top 10 KPIs for closed beta (call answer rate, booking rate, emergency capture, etc.)
  - [ ] Ensure each KPI has a metric + dashboard view (or log-based query)
  - [ ] Add a "beta health" dashboard panel set (admin view)
  - **AC:**
    - You can answer: "What broke?", "Who is impacted?", "How bad is it?" in < 5 minutes

- [ ] [P0] Beta feedback loop & support intake
  - [ ] Add in-product feedback link (dashboard + widget) to a single intake endpoint
  - [ ] Add structured bug report template (steps, expected, actual, tenant, call_sid)
  - [ ] Add "export my logs" helper for a tenant/time range (admin-only)
  - **AC:**
    - A beta user can file feedback in < 60 seconds and it includes enough context to reproduce

---

## [EPIC][P0] Telephony & Voice Assistant Hardening
**Labels:** epic, priority: P0, phase/closed-beta, area/backend

### Sub-issues
- [ ] [P0] Canonical call state machine + idempotent webhook handling
  - [ ] Define canonical call states (initiated + active + ended; plus transfers/voicemail)
  - [ ] Persist call state transitions with timestamps + source event ids
  - [ ] Make Twilio webhook handlers idempotent (dedupe by event id / signature)
  - [ ] Handle out-of-order delivery safely (ignore/regress protection)
  - [ ] Add unit tests for idempotency + ordering
  - **AC:**
    - Replaying the same webhook N times does not create duplicate appointments/notifications

- [ ] [P0] Assistant guardrails for deterministic outcomes
  - [ ] Enumerate allowed intents (schedule, reschedule, cancel, FAQ, emergency, fallback)
  - [ ] Enforce hard constraints on assistant actions (e.g., never confirm without address)
  - [ ] Add explicit fallback behavior when confidence is low
  - [ ] Add handoff escalation path (voicemail + callback queue)
  - **AC:**
    - For every inbound call the system ends in one of the allowed terminal outcomes (no limbo)

- [ ] [P0] STT/TTS resiliency + fallback plan
  - [ ] Add provider abstraction health checks (STT/TTS)
  - [ ] Implement fallback order (primary + secondary + "DTMF / short prompts")
  - [ ] Add timeouts + circuit breakers for streaming endpoints
  - [ ] Add clear error prompts that preserve UX ("I'm having trouble hearing you")
  - **AC:**
    - A degraded STT/TTS provider never causes the call to hang indefinitely

- [ ] [P1] Transcript quality and redaction pipeline
  - [ ] Ensure transcripts are captured consistently when enabled
  - [ ] Redact sensitive strings (payment info, SSN-like patterns) from logs/transcripts
  - [ ] Add "transcript missing" reason codes (provider error, opt-out, timeout)
  - [ ] Add tests for redaction correctness
  - **AC:**
    - PII redaction verified by automated tests and spot-check tooling

---

## [EPIC][P0] Emergency Handling & After-Hours Mode
**Labels:** epic, priority: P0, phase/closed-beta, area/backend, area/dashboard

### Sub-issues
- [ ] [P0] Emergency detection v1.1 (deterministic + configurable)
  - [ ] Create per-tenant emergency keyword/phrase list (editable in admin/owner UI)
  - [ ] Add "emergency confidence" scoring and reason codes (keyword hit, intent match)
  - [ ] Prevent false-positives with "confirm emergency?" prompt when ambiguous
  - [ ] Add tests for top emergency phrases (plumbing examples + noisy inputs)
  - **AC:**
    - Emergency calls reliably produce an "emergency" tag + immediate owner notification

- [ ] [P0] Owner notification reliability (SMS + call bridge + dashboard)
  - [ ] Ensure notification dedupe (no spam) + retry policy (bounded)
  - [ ] Add "notification delivery status" visible in owner dashboard
  - [ ] Add fallback channel if SMS fails (voice call or email if enabled)
  - [ ] Add audit trail for notifications (what was sent, when, to whom)
  - **AC:**
    - For emergencies, owner notification happens within a defined SLA (e.g., < 60s) or raises an alert

- [ ] [P1] After-hours policy engine
  - [ ] Add per-tenant business hours + holidays + service-area constraints
  - [ ] Implement after-hours call handling (voicemail, callback queue, emergency exception)
  - [ ] Add "next available slot" suggestions for non-emergencies
  - **AC:**
    - After-hours callers get a consistent experience and non-emergency bookings do not violate constraints

---

## [EPIC][P0] Scheduling, Calendar & Dispatch Reliability
**Labels:** epic, priority: P0, phase/closed-beta, area/backend, area/integrations

### Sub-issues
- [ ] [P0] Calendar slot selection correctness (conflicts, durations, buffers)
  - [ ] Standardize duration rules by job type (configurable per tenant)
  - [ ] Add travel/buffer time support (configurable)
  - [ ] Enforce conflict checks at create-time (re-check before final confirmation)
  - [ ] Add unit tests for edge cases (overlaps, daylight savings, timezone differences)
  - **AC:**
    - It is not possible to create overlapping events for the same technician/calendar resource

- [ ] [P0] Reschedule/cancel flows are safe + auditable
  - [ ] Add explicit "reschedule" and "cancel" intents with confirmation prompts
  - [ ] Ensure calendar updates are atomic and idempotent
  - [ ] Write to CRM conversation history with structured "action records"
  - [ ] Notify customer on changes (SMS/email if configured)
  - **AC:**
    - Reschedule/cancel operations have an audit trail and never create duplicate bookings

- [ ] [P1] Owner voice query: "what's on my schedule tomorrow?"
  - [ ] Owner authentication UX for voice queries
  - [ ] Support date-range queries and emergency filters
  - [ ] Add SMS follow-up summary option
  - **AC:**
    - Owner can reliably retrieve schedule details without exposing customer PII to unauthorized callers

---

## [EPIC][P1] CRM Data Quality & Customer/Job Lifecycle
**Labels:** epic, priority: P1, phase/closed-beta, area/backend, area/dashboard

### Sub-issues
- [ ] [P0] Customer identity & dedupe rules
  - [ ] Define dedupe keys (phone, email, address) with precedence rules
  - [ ] Implement "possible duplicate" detection + merge UI (admin/owner)
  - [ ] Add tests for merge correctness (appointments, conversations, notes)
  - **AC:**
    - You can safely merge duplicates without losing appointment or conversation history

- [ ] [P1] CSV import hardening (mapping, validation, preview)
  - [ ] Provide mapping UI (fields + CRM schema)
  - [ ] Add dry-run preview with row-level validation errors
  - [ ] Add idempotent import behavior (re-import does not duplicate)
  - **AC:**
    - Import failures are explainable; partial imports do not corrupt the CRM

- [ ] [P1] Job status lifecycle + retention campaigns
  - [ ] Formalize appointment/job statuses (new + booked + completed + invoiced)
  - [ ] Add "follow-up campaign" triggers based on status + time
  - [ ] Respect opt-out preferences for all campaigns
  - **AC:**
    - Retention messages never send to opted-out customers and are traceable per customer

---

## [EPIC][P0] Multi-tenant Onboarding, Auth & Billing Guardrails
**Labels:** epic, priority: P0, phase/closed-beta, area/backend, area/security, area/integrations

### Sub-issues
- [ ] [P0] Self-service onboarding: tenant creation + ready-to-take-calls
  - [ ] Ensure a new tenant can be created without manual DB edits
  - [ ] Add guided setup steps (Twilio number, business hours, calendar connect)
  - [ ] Provide a "test call" and "test SMS" button with diagnostics
  - **AC:**
    - A new beta tenant can be fully onboarded in < 30 minutes end-to-end

- [ ] [P0] Subscription enforcement + graceful degradation
  - [ ] Enforce plan caps and subscription state consistently across voice/chat/sms
  - [ ] Define "degraded but safe" behavior when subscription inactive (e.g., voicemail only)
  - [ ] Add owner-facing messages explaining what is blocked and why
  - [ ] Add tests for all subscription states (trialing, active, past_due, canceled)
  - **AC:**
    - Subscription enforcement never blocks emergencies from reaching the owner (configurable policy)

- [ ] [P0] Token hygiene: owner/admin/widget/API keys rotation
  - [ ] Add rotation endpoint for each token type
  - [ ] Add "last used" tracking and display in admin dashboard
  - [ ] Add automatic expiration option for widget tokens (short-lived)
  - **AC:**
    - Tokens can be rotated without downtime and leaked tokens can be invalidated immediately

---

## [EPIC][P1] Dashboards & Operator UX Improvements
**Labels:** epic, priority: P1, phase/closed-beta, area/dashboard, area/backend

### Sub-issues
- [ ] [P0] Owner dashboard: missed calls / voicemail / callback queue usability
  - [ ] Make callback items actionable (call back, mark resolved, add note)
  - [ ] Show transcript/summary (if enabled) and capture resolution outcome
  - [ ] Add filters (date, emergency, status)
  - **AC:**
    - Owner can clear the callback queue efficiently and outcomes are recorded

- [ ] [P1] Admin dashboard: tenant health + configuration drift
  - [ ] Add "tenant readiness" status card (calendar connected, Twilio verified, billing active)
  - [ ] Add configuration diff view (env vs tenant settings)
  - [ ] Add export for support diagnostics
  - **AC:**
    - Admin can detect misconfiguration in < 2 minutes without reading logs manually

- [ ] [P1] Widget/chat UX improvements
  - [ ] Add connection + auth error UX (clear retry path)
  - [ ] Add conversation continuity (resume recent thread)
  - [ ] Add branding settings (tenant name/logo/colors if applicable)
  - **AC:**
    - Widget failures are user-friendly and do not silently drop messages

---

## [EPIC][P0] Observability, Reliability & Incident Response
**Labels:** epic, priority: P0, phase/closed-beta, area/ops, area/backend, area/infra

### Sub-issues
- [ ] [P0] Structured logging + correlation ids everywhere
  - [ ] Standardize request id / call id / tenant id propagation
  - [ ] Ensure logs never contain secrets and redact PII where appropriate
  - [ ] Add "debug bundle" export for a call/conversation id
  - **AC:**
    - A single correlation id links: webhook + assistant decisions + calendar writes + notifications

- [ ] [P0] Metrics + alerting for beta
  - [ ] Define SLOs (uptime, booking success, emergency notify latency)
  - [ ] Implement alert thresholds and on-call routing for critical failures
  - [ ] Add dashboards for: Twilio webhook failures, calendar failures, notification failures
  - **AC:**
    - Every P0 alert has a runbook link and clear "how to mitigate" steps

- [ ] [P1] Chaos / failure injection (safe)
  - [ ] Add toggles to simulate provider outages (Twilio/Calendar/Stripe) in staging
  - [ ] Verify the system degrades gracefully and recovers without manual cleanup
  - **AC:**
    - Staging can prove recovery for top 5 failure modes before closed beta

- [ ] [P1] Incident response workflow polish
  - [ ] Ensure INCIDENT_RESPONSE.md is actionable for beta incidents
  - [ ] Add post-incident template fields specific to telephony (call_sids, webhook traces)
  - [ ] Add "status page / comms plan" template for beta customers
  - **AC:**
    - Anyone on the team can follow the runbook and produce a postmortem with the right artifacts

---

## [EPIC][P0] Security, Privacy & Compliance Baseline
**Labels:** epic, priority: P0, phase/closed-beta, area/security, area/backend, area/ops

### Sub-issues
- [ ] [P0] PII handling policy + enforcement
  - [ ] Document PII fields and where they appear (DB, logs, exports)
  - [ ] Enforce redaction rules in logs + transcripts
  - [ ] Add "export/delete customer" workflow (privacy requests)
  - **AC:**
    - A privacy request can be fulfilled with an auditable workflow

- [ ] [P0] Webhook security hardening
  - [ ] Ensure signature verification is enforced in prod for Twilio/Stripe
  - [ ] Add replay protection and timestamp skew checks
  - [ ] Add security tests (invalid sig, replayed payload, missing headers)
  - **AC:**
    - Invalid webhooks cannot mutate tenant state or create appointments

- [ ] [P0] Rate limiting + abuse prevention
  - [ ] Add per-tenant rate limits on public endpoints (widget/chat)
  - [ ] Add per-IP protections and anomaly flags
  - [ ] Add "lockdown mode" per tenant (temporarily disable widget/automation)
  - **AC:**
    - Abuse does not lead to unbounded cost or denial of service for other tenants

- [ ] [P1] Compliance: SMS opt-out end-to-end
  - [ ] Ensure STOP/HELP handling is correct and immediate
  - [ ] Ensure opt-out is enforced across all messaging flows (reminders, campaigns, owner alerts if applicable)
  - [ ] Add tests for opt-out edge cases (case-insensitive, punctuation, short codes)
  - **AC:**
    - Once opted out, no further non-critical SMS are sent until explicit opt-in

---

## [EPIC][P1] Integrations & Extensibility
**Labels:** epic, priority: P1, phase/open-beta, area/integrations, area/backend

### Sub-issues
- [ ] (Existing) #63 Email integration (Gmail/Workspace) - break down + execute
  - [ ] Implement Gmail OAuth token storage + refresh per tenant
  - [ ] Add email sending for owner alerts + optional customer confirmations
  - [ ] Add UI toggles for email notification preferences + status/error surfacing
  - [ ] Add tests for live vs stub behavior and failure paths
  - **AC:**
    - Tenants can connect Gmail and send emails; tokens refresh automatically

- [ ] [P2] QuickBooks Online sync hardening (customer + receipts)
  - [ ] Make OAuth flow robust with per-tenant token refresh
  - [ ] Add sync status + last successful sync time in dashboard
  - [ ] Add reconciliation for failed syncs (retry queue)
  - **AC:**
    - Accounting sync failures do not block core scheduling and are visible to the owner/admin

- [ ] [P2] Integration framework: generic webhook "events out"
  - [ ] Define event schema (appointment.created, call.emergency, etc.)
  - [ ] Add signed outbound webhooks with retries and DLQ
  - [ ] Add per-tenant subscriptions + secret rotation
  - **AC:**
    - Third parties can reliably consume system events without polling

---

## [EPIC][P2] Analytics & Reporting (post-closed beta)
**Labels:** epic, priority: P2, phase/open-beta, area/dashboard, area/backend

### Sub-issues
- [ ] [P2] Business insights dashboard (calls + bookings + revenue proxy)
  - [ ] Define attribution rules (call source + booking)
  - [ ] Add "missed call cost" estimator for owner
  - [ ] Add export (CSV) for calls/appointments
  - **AC:**
    - Owners can see weekly performance trends without manual spreadsheet work

- [ ] [P3] Advanced analytics backlog
  - [ ] Seasonal trend analysis
  - [ ] Customer LTV / repeat rate
  - [ ] Predictive booking forecasts
  - **AC:**
    - Metrics are stable and do not regress core performance

---

## [EPIC][P1] Developer Experience & Documentation
**Labels:** epic, priority: P1, phase/closed-beta, area/docs, area/ops, area/backend

### Sub-issues
- [ ] [P0] "First 30 minutes" contributor flow
  - [ ] Ensure local dev instructions are accurate (backend + dashboard + widget)
  - [ ] Add one-command bootstrap (optional) for dev env
  - [ ] Add troubleshooting section for common issues
  - **AC:**
    - A new dev can run backend + open dashboard + simulate a call flow in < 30 minutes

- [ ] [P1] API Reference correctness + examples
  - [ ] Add request/response examples for key endpoints (admin + owner + widget)
  - [ ] Add auth examples (tokens/headers)
  - [ ] Add "stub vs live providers" behavior table
  - **AC:**
    - Every public endpoint used by dashboard/widget has a documented example

- [ ] [P1] Beta operator documentation
  - [ ] Create "Closed beta operator guide" (how to onboard a tenant, test calls, verify calendar)
  - [ ] Create "Support playbook" with common tickets and resolutions
  - **AC:**
    - Support can resolve top 10 tickets without engineering involvement
