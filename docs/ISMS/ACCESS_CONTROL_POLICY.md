Access Control Policy (SSO/MFA, Joiner/Leaver)
===============================================

Principles
----------
- Least privilege for GitHub, CI, and cloud accounts.
- Strong authentication: SSO with MFA required for all human users; no shared accounts.
- Service accounts restricted to CI runners and Cloud Run with scoped roles.

Controls
--------
- **GitHub**: Enforce SSO + MFA, branch protection on `main`, required reviews + status checks (CI, gitleaks, pip-audit, CodeQL). Access review monthly; revoke stale collaborators. Disable PAT-based admin where possible; prefer fine-grained tokens for CI only.
- **CI secrets**: Stored in GitHub Actions secrets; limit write access to release managers. No plaintext secrets in repo (enforced by gitleaks).
- **Cloud**: IAM via Google Workspace/Cloud IAM groups; minimum roles for Cloud Run, Cloud SQL, GCS. Service accounts rotated quarterly; disable unused keys.
- **Joiner/Mover/Leaver**:
  - Joiner: manager approval, add to GitHub org with SSO/MFA, grant least-privileged roles in Google Cloud/IAM; document in access log.
  - Mover: adjust roles when responsibilities change; review API keys/service accounts owned.
  - Leaver: same-day access removal from GitHub, CI secrets, Cloud IAM; rotate shared tokens and Twilio/Stripe webhook secrets if applicable.
- **Periodic reviews**: Monthly access review for GitHub org, CI secrets, and Cloud IAM; track evidence in the access log (link tickets in GitHub).

Enforcement checklist (run now, then verify monthly)
----------------------------------------------------
- GitHub org: require SAML SSO + MFA for all members and outside collaborators; lock `main` with required checks (backend-ci, perf-smoke, dependency/code scanning) and two approvals for admin changes.
- GitHub repos: limit admin to release managers; disable third-party app access except approved CI; audit fine-grained PATs and remove unused tokens.
- GitHub Actions: restrict secret write access to admins; enable secret scanning and push protection (already on via dependency/security workflows).
- Cloud IAM: enforce Google Workspace SSO with MFA; remove broad `roles/editor` assignments; rotate service account keys quarterly; disable unused SAs.
- Joiner/leaver: same-day provisioning/deprovisioning with ticket reference; rotate shared tokens (Twilio/Stripe webhooks) after leavers.
- Calendar reminders: recurring monthly access review and quarterly secret rotation (owner: ISMS lead).

Evidence to collect
-------------------
- GitHub org audit export (monthly).
- Cloud IAM role export (monthly) with diffs.
- CI secret inventory and rotation log (quarterly).
- MFA/SSO enforcement screenshots.

Automation helpers
------------------
- GitHub access export: `ops/access-review/export-github-access.ps1` (repo collaborators/teams, secret name inventory, branch protection when permitted).
- GCP IAM export: `ops/access-review/export-gcp-iam.ps1` (project IAM policy + service accounts; optional org policies when permitted).
  - Store outputs in the secure evidence share and link them from `docs/ISMS/ACCESS_REVIEW_LOG.md`.
