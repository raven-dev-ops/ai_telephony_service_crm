Backup and Restore Evidence Log
===============================

Use this log to record each backup/restore drill with RPO/RTO measurements.

| Date | Environment | Backup Timestamp | RPO (minutes) | RTO (minutes) | Validation (tests run) | Issues Found | Actions | Evidence Link |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2025-12-12 | Staging | | | | | | | |

Checklist for each entry
------------------------
- Restore latest production backup to staging or DR environment.
- Run core tests: `pytest backend/tests/test_twilio_integration.py backend/tests/test_calendar_conflicts.py backend/tests/test_conversation.py`.
- Validate owner notification hub (SMS retry + email fallback) and webhook signature enforcement.
- Document RPO/RTO and attach logs/screenshots as evidence.
