Access Review Log (Template)
============================

Purpose
-------
Record monthly access reviews for GitHub, CI secrets, and cloud IAM to prove SSO/MFA enforcement and least privilege.

How to run
----------
- Export current access lists (GitHub org members, repo collaborators, CI secret admins, Cloud IAM roles).
- Verify SSO + MFA enforced for human users; remove stale accounts; rotate shared tokens.
- Capture evidence (screenshots/exports) in the secure share and link below.

Log
---
| Date | Scope (GitHub/CI/Cloud) | Reviewer | Findings | Actions Taken | Evidence Link |
| --- | --- | --- | --- | --- | --- |
| 2026-01-05 | GitHub repo + CI secrets | damon.heath | Repo has 1 collaborator (admin), 0 teams; Actions secrets count 9, variables 0; environment `staging` present. Branch protection check returned "Branch not protected"; org member list not accessible (needs admin:org), so SSO/MFA enforcement not verified. | None; branch protection + org SSO/MFA verification still required. | gs://ravdevops-isms-evidence-mpf-dmxmytcubly9/access-review/2026-01-05/github-access-evidence.json |
| 2026-01-05 | Cloud IAM (project google-mpf-dmxmytcubly9) | damon.heath | 2 service accounts; 38 IAM bindings; `roles/editor` still assigned to `serviceAccount:215484517190@cloudservices.gserviceaccount.com` (needs review). | None; confirm if Cloud Services SA can be narrowed; verify org-level SSO/MFA policies. | gs://ravdevops-isms-evidence-mpf-dmxmytcubly9/access-review/2026-01-05/gcp-iam-evidence.json |
| 2025-12-12 | GitHub org + repo | damon.heath | Branch protection applied on `main` (2 approvals, required checks: backend-ci, perf-smoke, dependency-review, dependency-security, codeql, gitleaks; admins enforced). SSO/MFA enforcement not verified (needs org admin). | None (SSO/MFA pending). | Pending org export/screenshot |
| 2025-12-12 | GitHub Actions secrets | damon.heath | P0 alert secrets set (METRICS_URL, thresholds, callback endpoints). No secret sprawl observed in current list. | Added P0 thresholds and prod metrics URLs as secrets. | GitHub secret list (2025-12-12) |
| 2025-12-12 | Cloud IAM (prod/staging) | damon.heath | Project `google-mpf-dmxmytcubly9` IAM: removed broad `roles/editor` from `215484517190@cloudservices.gserviceaccount.com`; remaining roles are scoped to build/run/sql/storage. Applied org policies to enforce `iam.disableServiceAccountKeyCreation` and `iam.disableServiceAccountKeyUpload`. Attempt to set `iam.allowedPolicyMemberDomains` failed (domain format rejected). SSO/MFA status not visible from policy (needs org-level toggle). | Removed editor; enforced no SA key create/upload at org level. | `gcloud projects get-iam-policy google-mpf-dmxmytcubly9` (post-removal 2025-12-12); org policies set at `organizations/1010311725888` (2025-12-12) |
| 2026-02-05 (scheduled) | GitHub org + Cloud IAM | ISMS lead | Planned monthly review: verify org SSO/MFA toggles enabled, remove stale collaborators, export IAM role diffs, rotate shared tokens if needed. | Pending. | Calendar reminder set (monthly) |
