Backup and Restore Evidence Log
===============================

Use this log to record each backup/restore drill with RPO/RTO measurements.

| Date | Environment | Backup Timestamp | RPO (minutes) | RTO (minutes) | Validation (tests run) | Issues Found | Actions | Evidence Link |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2025-12-12 | Staging | Not run (pending drill) | N/A | N/A | Not run | Drill not yet executed; automated Cloud SQL backups available (last success 2025-12-11 06:29 UTC). | Plan restore to staging + run smoke tests. | `gcloud sql backups list --instance=ai-telephony-db` (2025-12-12) |
| 2025-12-12 | DR drill (private instance) | 2025-12-11 06:29 UTC (backup id 1765432800000) | ~1150 (backup age) | ~6 (restore duration) | `pytest tests/test_twilio_integration.py tests/test_calendar_conflicts.py tests/test_conversation.py` (58 passed) | Private-IP connectivity required a Cloud SQL proxy; default reserve_mornings_for_emergencies caused false conflicts before adjustment. Schema gaps (token/lockdown columns) patched manually. | Used GCE bastion `dr-test` + Cloud SQL Auth Proxy (port 5434) to run tests; set `reserve_mornings_for_emergencies=false` on `businesses`; added missing business columns earlier; full suite now passes. | `dr-test:~/proxy.log`, `gcloud sql backups restore ... --restore-instance=ai-telephony-drill` |

Checklist for each entry
------------------------
- Restore latest production backup to staging or DR environment.
- Run core tests: `pytest backend/tests/test_twilio_integration.py backend/tests/test_calendar_conflicts.py backend/tests/test_conversation.py`.
- Validate owner notification hub (SMS retry + email fallback) and webhook signature enforcement.
- Document RPO/RTO and attach logs/screenshots as evidence.
