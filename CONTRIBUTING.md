Contributing & Engineering Guide
================================

This document summarizes how to work on this repository in a way that is consistent with the
RavDevOps engineering standard and the Bristol Plumbing design documents.


Source PDFs & Traceability
--------------------------

All engineering expectations here are distilled from:

- `Project_Engineering_Whitepaper.pdf` - RavDevOps engineering culture, testing, and safety.
- `Bristol_Plumbing_Analysis.pdf` - business context, services, and call patterns.
- `Bristol_Plumbing_Implementation.pdf` - implementation concept, channels, and owner UX.
- `Bristol_Plumbing_Project_Plan.pdf` - phased feature plan and target architecture.

Before making non-trivial changes, skim these plus the root docs (`README.md`, `OUTLINE.md`,
`BACKLOG.md`, `WIKI.md`, `SECURITY.md`, `RUNBOOK.md`, `DEV_WORKFLOW.md`, `TOOLS.md`) so your work
stays aligned with the intended product and safety posture.


Engineering Principles (RavDevOps)
----------------------------------

When contributing:

- **Prefer boring, explicit code**
  - Choose clarity over clever abstractions, especially in:
    - Emergency detection and tagging.
    - Scheduling and calendar integration.
    - Twilio/telephony and SMS flows.
    - Tenant isolation and auth.
  - Keep business rules in well-named, testable functions rather than scattered conditionals.

- **Design before implementation**
  - For meaningful features, write a short design note (even a few paragraphs) and check it into
    the repo (e.g., under a `design/` or `docs/` area) before landing large code changes.
  - Make sure the design links back to the relevant sections of the PDFs and root docs.

- **Tests are non-optional**
  - New behavior should come with unit tests and, where appropriate, integration tests.
  - For changes touching safety-critical paths (emergencies, Twilio, SMS opt-out, tenant
    isolation), treat missing tests as a bug.

- **Small, reversible changes**
  - Prefer a series of small, reviewable patches over large, entangled refactors.
  - Keep changes easy to roll back; avoid mixing unrelated cleanups with behavioral changes.

- **Blameless learning**
  - When something breaks, focus on understanding and improving the system instead of blaming
    individuals.
  - Capture follow-ups as backlog items so problems do not recur silently.


Working in This Repo
--------------------

- **Backend (`backend/`)**
  - Python 3.11+, FastAPI, and pytest.
  - Use type hints and keep modules cohesive (e.g., routers thin, services encapsulating logic).
  - Before changing behavior in:
    - `owner.py` (owner schedule/summary),
    - `twilio_integration.py` (webhooks),
    - `conversation.py` (voice flow, emergency logic),
    update or add tests under `backend/tests/` and run:

    ```bash
    cd backend
    pytest
    ```

  - To match CI locally, see `.github/workflows/backend-ci.yml` and run:

    ```bash
    cd backend
    ruff check .
    black --check .
    pytest
    ```

- **Dashboard (`dashboard/`) and widget (`widget/`)**
  - Keep the HTML/JS simple and readable; avoid heavy frameworks unless the design explicitly
    calls for them.
  - Treat the backend APIs as the source of truth; do not duplicate business rules in the frontend.
  - Use clear error handling and surface failures (calendar, Twilio, auth) in a user-friendly way.

- **Docs**
  - When adding or modifying significant behavior, update the relevant root docs:
    - `README.md` for high-level behavior and architecture.
    - `API_REFERENCE.md` / `DATA_MODEL.md` for API and data model updates.
    - `DEV_WORKFLOW.md` / `TOOLS.md` if you add new dev scripts or common workflows.
    - `OUTLINE.md` / `BACKLOG.md` for product scope and phases.
    - `SECURITY.md`, `PRIVACY_POLICY.md`, `TERMS_OF_SERVICE.md` for policy implications.
    - `RUNBOOK.md`, `PILOT_RUNBOOK.md`, `DEPLOYMENT_CHECKLIST.md` for operational changes.
  - Link new documentation back to the PDFs where appropriate.


Security, Privacy & Safety
--------------------------

- **Secrets and credentials**
  - Never commit real secrets (API keys, Twilio tokens, DB passwords).
  - Assume secrets live in environment variables or a secrets manager as described in
    `DEPLOYMENT_CHECKLIST.md` and `SECURITY.md`.

- **Tenant isolation**
  - Always consider which tenant(s) a change affects:
    - Use `X-API-Key` / `X-Widget-Token` / `X-Business-ID` consistently.
    - Avoid new code paths that could leak data across tenants.

- **Telephony and SMS**
  - Keep Twilio signature verification and SMS opt-out behavior intact or improved.
  - If you change SMS or call flows, verify that:
    - Opt-out keywords are still honored.
    - Emergency messaging remains clear that the system does **not** contact 911.

For more detailed expectations, refer to `SECURITY.md`, `PRIVACY_POLICY.md`, and the
`Project_Engineering_Whitepaper.pdf`.
