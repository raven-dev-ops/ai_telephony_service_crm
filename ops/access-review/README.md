# Access review exports (evidence helpers)

These scripts help generate **monthly access review evidence** for GitHub/CI and Google Cloud IAM (issue `#90`).

They are designed to:
- Export *who has access* (users/teams) and *what is configured* (branch protection, secret inventory names).
- Avoid exporting secret values (GitHub APIs never return secret values).
- Produce a JSON file you can store in the secure evidence share and link from `docs/ISMS/ACCESS_REVIEW_LOG.md`.

## Prerequisites

- GitHub CLI: `gh` (authenticated: `gh auth status`)
- For GCP IAM export: `gcloud` (authenticated: `gcloud auth list`)

## Export GitHub repo access (users/teams/secrets)

From repo root:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File ops/access-review/export-github-access.ps1 `
  -Repo raven-dev-ops/ai_telephony_service_crm `
  -Branch main `
  -OutputJson github-access-evidence.json
```

Notes:
- Some endpoints require elevated permissions (e.g., branch protection). The script records failures in the output JSON instead of stopping.

## Export GCP IAM policy + service accounts

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File ops/access-review/export-gcp-iam.ps1 `
  -ProjectId YOUR_GCP_PROJECT_ID `
  -OutputJson gcp-iam-evidence.json
```

Optional org policy export (if you have access):

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File ops/access-review/export-gcp-iam.ps1 `
  -ProjectId YOUR_GCP_PROJECT_ID `
  -OrganizationId YOUR_ORG_ID `
  -OutputJson gcp-iam-evidence.json
```

## Evidence handling

- Do **not** commit generated evidence files to git.
- Upload to the secure evidence share and paste the link into `docs/ISMS/ACCESS_REVIEW_LOG.md`.

