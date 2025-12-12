Backup and Restore Evidence Log
===============================

Use this log to record each backup/restore drill with RPO/RTO measurements.

| Date | Environment | Backup Timestamp | RPO (minutes) | RTO (minutes) | Validation (tests run) | Issues Found | Actions | Evidence Link |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2025-12-12 | Staging | Not run (pending drill) | N/A | N/A | Not run | Drill not yet executed; automated Cloud SQL backups available (last success 2025-12-11 06:29 UTC). | Plan restore to staging + run smoke tests. | `gcloud sql backups list --instance=ai-telephony-db` (2025-12-12) |
| 2025-12-12 | DR drill (new instance) | 2025-12-11 06:29 UTC (backup id 1765432800000) | N/A | N/A | Not run | Restore attempt blocked by org policy restricting public IPs; private/PSC connectivity required to create target instance. | Need network-approved private/PSC Cloud SQL instance for restores; retry with VPC/PSC configured. | `gcloud sql instances create ai-telephony-drill --no-assign-ip` failed (org policy) |

Checklist for each entry
------------------------
- Restore latest production backup to staging or DR environment.
- Run core tests: `pytest backend/tests/test_twilio_integration.py backend/tests/test_calendar_conflicts.py backend/tests/test_conversation.py`.
- Validate owner notification hub (SMS retry + email fallback) and webhook signature enforcement.
- Document RPO/RTO and attach logs/screenshots as evidence.
