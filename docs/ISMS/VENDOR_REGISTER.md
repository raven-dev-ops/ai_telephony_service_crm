Vendor Register and DPAs
========================

Register
--------
| Vendor | Purpose | Data Processed | DPA/Security Review | Evidence | Last Review | Owner | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Twilio | Voice/SMS, webhooks | Phone numbers, call metadata, transcripts (short), voicemail URLs | DPA link recorded; trust center compliance noted | https://www.twilio.com/legal/data-protection-addendum, https://www.twilio.com/trust | 2026-01-06 | Eng | Use restricted webhook IPs and signature checks; rotate auth tokens quarterly. |
| Stripe | Billing/subscriptions | Customer name/email, subscription ids, payment links (no PAN) | DPA link recorded; security docs linked (PCI/SOC) | https://stripe.com/legal/dpa, https://stripe.com/docs/security/stripe | 2026-01-06 | Eng/Finance | Webhook signatures enforced; test mode by default. |
| Google (Calendar, OAuth, GCS) | Scheduling, storage | Calendar events, auth tokens, dashboard assets | Cloud + Workspace DPA links recorded; compliance docs linked | https://cloud.google.com/terms/data-processing-addendum, https://workspace.google.com/terms/dpa_terms.html, https://cloud.google.com/security/compliance | 2026-01-06 | Eng | OAuth tokens stored in Secret Manager; limited scopes. |
| QuickBooks Online | Invoicing/exports | Customer/contact details, invoice metadata | DPA link recorded; security portal link | https://www.intuit.com/terms/dpa/, https://security.intuit.com/ | 2026-01-06 | Finance | Sandbox by default; production only with owner approval. |
| OpenAI or other LLM APIs (optional) | Intent assist | Snippets of transcript | DPA link recorded; security review pending | https://openai.com/policies/data-processing-addendum | 2026-01-06 | Eng/Product | Off by default; enable only per-tenant with data policy acknowledged. |
| Email provider (e.g., Gmail/Workspace) | Owner/customer email | Owner email, summaries | Workspace DPA link recorded; compliance docs linked | https://workspace.google.com/terms/dpa_terms.html, https://cloud.google.com/security/compliance | 2026-01-06 | Eng | Disable if not needed; use service account with least privilege. |

Management
----------
- Review vendor list quarterly; add/remove entries when integrations change.
- Store signed DPAs in the shared secure drive; link to GitHub issue for traceability.
- For each vendor, record last penetration test date (if provided) and SOC2/ISO certificates when available.
- Set a quarterly reminder to refresh evidence links and attestations; mark `Last Review` accordingly.

Checklist
---------
- Use `docs/ISMS/VENDOR_DPA_SECURITY_REVIEW_CHECKLIST.md` to collect DPAs/security evidence and populate the Evidence links above.
