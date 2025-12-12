Closed Beta GitHub Issues Tasklist
==================================

Last updated: <!-- date is informational; keep in sync when editing -->2025-12-12

Summary
-------
- All tracked beta readiness issues (#71-#89) are closed in GitHub.
- Highest-risk buckets (P0) shipped: rate limiting/abuse (#89), webhook security (#88), PII policy/enforcement (#87), metrics/alerting (#86), structured logging/correlation IDs (#85), token hygiene (#84), subscription enforcement (#83), onboarding to ready-to-take-calls (#82), scheduling/cancel/reschedule correctness (#80-#81), owner notifications (#79), emergency detection v1.1 (#78).
- Supporting tracks (coverage gaps, AI/intent, missed-call flows, integrations) are also closed (#60-#77).
- Follow-on ISO/ops hardening issues still open: #90 (SSO/MFA + access reviews), #91 (central log pipeline + P0 alert rules), #92 (backup/restore drill evidence), #93 (vendor register + DPAs), #94 (internal audit/ISO partner).

Checklist by priority (source: GitHub issues)
---------------------------------------------
- [x] #89 Rate limiting and abuse prevention (P0)
- [x] #88 Webhook security hardening (P0)
- [x] #87 PII handling policy and enforcement (P0)
- [x] #86 Metrics and alerting for beta (P0)
- [x] #85 Structured logging and correlation IDs (P0)
- [x] #84 Token hygiene and rotation (P0)
- [x] #83 Subscription enforcement with graceful degradation (P0)
- [x] #82 Self-service onboarding to ready-to-take-calls (P0)
- [x] #81 Safe reschedule/cancel flows (P0)
- [x] #80 Calendar slot selection correctness (P0)
- [x] #79 Owner notification reliability for emergencies (P0)
- [x] #78 Emergency detection v1.1 (deterministic + configurable) (P0)
- [x] #77 STT/TTS resiliency and fallback plan (P0)
- [x] #76 Assistant guardrails for deterministic outcomes (P0)
- [x] #75 Canonical call state machine and idempotent webhooks (P0)
- [x] #74 Beta feedback loop and support intake (P0)
- [x] #73 Beta instrumentation baseline (P0)
- [x] #72 End-to-end smoke suite for closed beta (P0)
- [x] #71 Define "Closed Beta Ready" Definition of Done (P0)
- [x] #70 Test coverage gaps & end-to-end flows (P2/testing)
- [x] #69 AI/intent upgrades (P2/AI)
- [x] #68 Missed-call/voicemail fallback (P1/telephony)
- [x] #67 Subscription gating & lifecycle (P1/billing)
- [x] #66 User management UI (multi-tenant roles) (P2/frontend/ux)
- [x] #65 QuickBooks Online real integration (P2/integrations)
- [x] #64 Per-tenant Google Calendar OAuth (P1/integrations)
- [x] #63 Email integration (Gmail/Workspace) (P2/integrations)
- [x] #62 Twilio voice -> assistant bridge (P1/telephony)
- [x] #61 Production-grade Stripe billing (P1/integrations/billing)
- [x] #60 Complete onboarding UX for integrations (P2/frontend/ux/integrations)

What remains (beyond beta)
--------------------------
- Operational hardening for ISO 27001: ISMS docs, recurring risk reviews, access reviews/SSO, backup & restore drills, vendor register with DPAs, scheduled internal audit/management review, and external certification plan (see `docs/ISMS` folder). Track via #90-#94.
- Continue monitoring with the alert runbooks and owner notification hub; keep coverage/CI gates green for regressions.
