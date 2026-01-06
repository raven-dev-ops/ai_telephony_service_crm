Internal Audit & Management Review
==================================

Cadence
-------
- **Internal audit**: Twice per year (and before external certification). Scope: access control, CI/CD controls, backup/restore evidence, incident handling, vendor DPAs, risk register updates.
- **Management review**: Quarterly with engineering lead, product lead, and executive sponsor. Inputs: risk register, incidents/MTTR, access review results, DR/backup drill results, open nonconformities.
- **Governance review**: Review the engineering whitepaper and any documented exceptions annually (RGOV2).

Plan (upcoming)
---------------
- Schedule next internal audit: January 2026 (owner: ISMS lead). Use this folder as the control library.
- Select external certification partner: shortlist and engage by February 2026; target ISO 27001 audit readiness by end of Q1 2026.
- Evidence pack: collect logs (access reviews, DR drills, CI runs, vulnerability scans) per quarter and store in secure share.

Action items
------------
- Create calendar invites for the January 2026 internal audit and quarterly management reviews (attach agenda and evidence links).
- Build an evidence index in the secure share (access logs, DR drill results, CI scan exports, incident log) and link from GitHub issues.
- Track audit findings/nonconformities as GitHub issues labeled `audit` with owners and due dates.
- Finalize shortlist of ISO partners and select target external audit window (Q1 2026).

Internal audit checklist (per cycle)
------------------------------------
- Confirm scope vs `docs/ISMS/ISMS_SCOPE.md` and `docs/ISMS/STATEMENT_OF_APPLICABILITY.md`.
- Review access review log and evidence (`docs/ISMS/ACCESS_REVIEW_LOG.md`).
- Validate CI/CD controls and policy-as-code gates (required checks, security scans).
- Review logging/alerting rules and incident postmortems (`docs/ISMS/LOGGING_AND_ALERTS.md`, `docs/ISMS/INCIDENT_RESPONSE_PLAN.md`).
- Review backup/restore evidence and DR runbook (`docs/ISMS/BACKUP_AND_RESTORE.md`, `docs/ISMS/DR_RUNBOOK.md`).
- Review vendor register and DPAs (`docs/ISMS/VENDOR_REGISTER.md`).
- Review risk register updates (`docs/ISMS/RISK_METHOD.md`).
- Review documented exceptions and their time-bound approvals (Project_Engineering_Whitepaper RGOV1/RGOV2).

Management review checklist (quarterly)
----------------------------------------
- Confirm ISMS scope changes and resource needs.
- Review internal audit findings and corrective actions.
- Review risk register changes and accepted risks.
- Review access review and security event trends.
- Review DR/backup drill outcomes and SLO/latency notes.
- Review policy exceptions and governance review status.
- Confirm ISO partner selection progress and target audit window.

ISO partner selection
---------------------
- Maintain a shortlist and scoring rubric for ISO 27001 partners.
- Require evidence of similar SaaS audits, audit approach, timeline, and cost model.
- Record selection decisions and target audit windows.
- See `docs/ISMS/ISO_PARTNER_SELECTION.md`.

Outputs
-------
- Internal audit report with findings, severities, and due dates; track as GitHub issues labeled `audit`.
- Management review minutes with decisions on risk acceptance and resource allocations.

Templates
---------
- Internal audit report: `docs/ISMS/INTERNAL_AUDIT_REPORT_TEMPLATE.md`
- Management review minutes: `docs/ISMS/MANAGEMENT_REVIEW_MINUTES_TEMPLATE.md`
