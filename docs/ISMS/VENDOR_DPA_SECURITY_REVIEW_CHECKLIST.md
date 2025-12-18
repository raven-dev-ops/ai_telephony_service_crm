Vendor DPA + Security Review Checklist
======================================

Use this checklist to complete entries in `docs/ISMS/VENDOR_REGISTER.md` and to link evidence stored in the secure share.

Per-vendor checklist
--------------------
- [ ] Identify vendor legal entity + service used (prod vs sandbox).
- [ ] Data categories processed (PII/PHI/payment metadata/transcripts/tokens/etc.).
- [ ] DPA in place (or equivalent contractual terms); record effective date.
- [ ] Security attestations gathered (SOC 2, ISO 27001, pen test summary) when available.
- [ ] Sub-processor list reviewed (if vendor provides one).
- [ ] Data residency / region constraints recorded (if applicable).
- [ ] Access controls verified (SSO/MFA on vendor console accounts; least privilege).
- [ ] Secret rotation plan documented (API keys/webhook secrets/tokens).
- [ ] Incident notification clauses verified (SLA/notification window).
- [ ] Quarterly review reminder set; update `Last Review` in the vendor register.

Evidence pointers (suggested)
-----------------------------
Store the following artifacts in the secure evidence share and link them from the vendor register:
- Signed DPA / contract addendum (PDF)
- Latest SOC 2 report or ISO certificate (PDF)
- Security whitepaper / pen test summary (PDF/URL)
- Sub-processor list snapshot (PDF/URL)
- Internal approval record (ticket link or meeting minutes reference)

