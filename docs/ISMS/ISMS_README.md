Information Security Management System (ISMS) - Working Draft
=============================================================

Purpose
-------
This folder documents the minimum viable ISMS for the AI Telephony Service & CRM as we move toward ISO 27001 certification. It captures scope, risk management, applicable controls, core policies, and operational evidence we can provide to auditors.

Quick map
---------
- Scope: `ISMS_SCOPE.md`
- Risk management: `RISK_METHOD.md`
- Statement of Applicability: `STATEMENT_OF_APPLICABILITY.md`
- Policies and processes:
  - Access control (SSO/MFA, joiner/mover/leaver): `ACCESS_CONTROL_POLICY.md`
  - Access review evidence: `ACCESS_REVIEW_LOG.md`
  - Secure SDLC + CI/CD controls: `SECURE_SDLC.md`
  - Incident response and alerting: `INCIDENT_RESPONSE_PLAN.md`
  - Disaster recovery & business continuity: `DR_BCP.md`
  - DR restore runbook (procedural): `DR_RUNBOOK.md`
  - Backup and restore testing (RPO/RTO): `BACKUP_AND_RESTORE.md` and `BACKUP_RESTORE_LOG.md`
  - Logging, monitoring, and alert rules: `LOGGING_AND_ALERTS.md`
  - Vendor inventory and DPAs: `VENDOR_REGISTER.md`
- Audit cadence and certification plan: `AUDIT_AND_MANAGEMENT_REVIEW.md`
  - Templates: `INTERNAL_AUDIT_REPORT_TEMPLATE.md`, `MANAGEMENT_REVIEW_MINUTES_TEMPLATE.md`

How to use
----------
- Keep these docs version-controlled; update after every tabletop, drill, or material change.
- Track open actions in the risk register template (`RISK_METHOD.md`) and in GitHub issues for accountability.
- Evidence (screenshots, exports) should be stored per release in the secure share; link from here when available.
