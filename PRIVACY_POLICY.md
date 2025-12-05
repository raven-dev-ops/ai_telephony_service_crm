Privacy Policy
==============

This Privacy Policy describes how an AI telephony assistant and CRM platform (the "Service") may
collect, use, and protect information when deployed by a trades business such as a plumbing
company. It is based on the design in this repository and the Bristol Plumbing business analysis.
This document is provided for informational purposes only and does **not** constitute legal advice.


Source PDFs & Traceability
--------------------------

This policy is derived from:

- `Bristol_Plumbing_Analysis.pdf` – the nature of customer interactions, booking flows, and data
  that Bristol Plumbing typically handles.
- `Bristol_Plumbing_Project_Plan.pdf` – requirements for customer history, scheduling, and
  analytics.
- `Bristol_Plumbing_Implementation.pdf` – how voice, SMS, and web channels are intended to work in
  practice.
- `Project_Engineering_Whitepaper.pdf` – expectations for data protection, logging, and safety.

Operators of a real deployment must adapt this policy to local law and their own governance.


1. Who This Policy Applies To
-----------------------------

The Service is intended to be operated by a trades business (for example, Bristol Plumbing) to help
manage customer calls, scheduling, and service history. In a typical deployment:

- The **business** is the data controller that decides what information is collected and how it is
  used.
- The **platform operator or implementer** (e.g., a team following this design) acts as a data
  processor on behalf of the business.


2. Information We May Collect
-----------------------------

Depending on configuration and the features enabled, the Service may process:

- **Contact information**
  - Caller and customer names.
  - Phone numbers.
  - Email addresses.
  - Service addresses.

- **Service and appointment information**
  - Type of service requested (e.g., tankless water heater installation, leak repair).
  - Preferred dates and times; booked appointments and changes.
  - Job notes and follow‑up details.

- **Conversation information**
  - Call and message metadata (time, duration, caller ID, channel).
  - Conversation summaries.
  - Optional call transcripts or recordings if configured and legally permitted.

- **Usage and analytics information**
  - Counts of calls, appointments, and common service requests.
  - Technical logs for reliability and security (error rates, latency, etc.).


3. How Information May Be Used
------------------------------

Information processed by the Service may be used to:

- Answer and route calls and messages.
- Schedule, modify, and cancel appointments on behalf of the business.
- Maintain a history of customers and jobs so the business can provide better service.
- Send appointment confirmations, reminders, and notifications.
- Generate aggregated analytics for the business (e.g., jobs per week, common service areas).
- Monitor and improve reliability and performance of the Service.

Information should **not** be used for unrelated purposes (such as selling marketing lists) without
clear, explicit consent and any required legal notices.


4. Legal Bases & Consent
------------------------

The legal basis for processing will depend on the jurisdiction and the business's policies. Common
bases include:

- Performance of a contract (e.g., providing requested plumbing services).
- Legitimate interests of the business (e.g., responding to inquiries, preventing fraud).
- Consent, particularly where call recording or marketing communications are involved.

When call recording or extensive logging is enabled, callers may need to be notified and, in some
jurisdictions, asked for consent. The business operating the Service is responsible for ensuring
compliance with applicable laws.


5. Third‑Party Services
-----------------------

The Service may integrate with third‑party providers, such as:

- **Google Calendar** (for scheduling and availability).
- **SMS providers** (e.g., Twilio) for text message notifications.
- **Cloud hosting providers** (e.g., Google Cloud Platform) for infrastructure, storage, and
  monitoring.

These providers will receive only the data necessary to perform their functions (for example,
calendar event details, phone numbers and message content for SMS). Their own privacy policies
and terms apply in addition to this document.


6. Data Retention
-----------------

Retention policies should be configured by the business operating the Service. In general:

- Contact and appointment information may be retained for as long as needed to:
  - Provide services.
  - Maintain records of jobs for warranty, safety, or tax purposes.
- Conversation summaries and technical logs should be kept no longer than necessary for:
  - Quality assurance.
  - Troubleshooting and incident analysis.
  - Improving the Service.
- Optional transcripts or recordings should be retained only as long as there is a clear business
  need and with appropriate safeguards.


7. Data Security
----------------

Security safeguards should include:

- Encryption in transit (TLS) and at rest.
- Restricted access to databases and logs based on role.
- Use of a secure secrets manager for credentials.
- Regular updates to software dependencies and infrastructure.
- Monitoring and alerting for suspicious or abnormal activity.

More detailed security expectations are outlined in `SECURITY.md` and the RavDevOps engineering
whitepaper.


8. Your Rights
--------------

Depending on the jurisdiction and the business's policies, individuals may have rights such as:

- Accessing a copy of their personal information.
- Requesting corrections to inaccurate data.
- Requesting deletion or restriction of certain data.
- Objecting to certain types of processing.

Requests of this nature should be directed to the business operating the Service (e.g., Bristol
Plumbing), which is responsible for responding in accordance with applicable law.


9. Changes to This Policy
-------------------------

As the Service or legal requirements evolve, this Privacy Policy may be updated. The business
operating the Service should:

- Keep an up‑to‑date version of the policy available to customers.
- Clearly indicate the date of the last update.
- Provide notice of material changes where appropriate.


10. SMS Notifications & Opt‑Out
-------------------------------

When configured, the Service can send SMS messages on behalf of the business, for example:

- Appointment confirmations and reminders.
- Notifications about schedule changes or follow‑ups.
- Urgent alerts related to emergency plumbing issues.

Callers and customers may also send SMS messages back to the same number. To respect their
preferences and comply with common messaging practices:

- The Service recognizes standard opt‑out keywords such as `STOP`, `STOPALL`, `UNSUBSCRIBE`,
  `CANCEL`, `END`, and `QUIT` (case‑insensitive) when they are sent as the full message body.
- When such a message is received, the system marks the associated phone number as opted out of
  customer SMS (for example, confirmations and reminders) and stops sending further customer
  messages to that number.
- Operational alerts to the business owner (for example, emergency notifications to the owner's
  phone) are not affected by a customer's opt‑out flag.

The business operating the Service is responsible for:

- Ensuring that use of SMS complies with applicable laws and carrier policies.
- Honoring opt‑out requests promptly and consistently.
- Providing any additional disclosures or consent flows required for marketing or promotional SMS
  beyond the transactional uses described here.

