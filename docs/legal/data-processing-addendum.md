# Data Processing Addendum

> **DRAFT, owner and a lawyer must review before publishing.** The structure follows common practice for processor terms. The security measures in Annex 2 were checked against the code and deployment configuration on 18 October 2026. This draft does not decide which law applies, whether a transfer is lawful or whether these clauses satisfy any regulator. Every `{{PLACEHOLDER}}` must be replaced before this page is marked published.

Last updated: {{EFFECTIVE_DATE}}

This Data Processing Addendum ("DPA") forms part of the [Terms of Service](terms-of-service.md) between {{LEGAL_ENTITY_NAME}} ("Processor", "we") and the Customer ("Controller", "you").

## 1. Definitions

"Personal data", "processing", "controller", "processor", "data subject" and "personal data breach" have the meanings given by the data protection law that applies to the processing, which may include the Saudi Personal Data Protection Law and its Implementing Regulations and the UAE Federal Decree-Law No. 45 of 2021 ("Data Protection Law"). "Customer Personal Data" means personal data in content the Customer or its users put into ANUM. "Subprocessor" means a third party we engage to process Customer Personal Data.

## 2. Roles and instructions

- You are the controller of Customer Personal Data and we process it on your behalf.
- We process it only on your documented instructions. The Terms, this DPA and your configuration of ANUM (the integrations you connect, approval rules, retention settings, model choice) are your instructions. We will tell you if we believe an instruction breaks Data Protection Law.
- You are responsible for having a lawful basis for the processing, for the notices and consents your data subjects need, and for the lawfulness of the data and messages you send through ANUM.

## 3. Confidentiality

Everyone we authorize to process Customer Personal Data is bound by confidentiality. Access by our staff is limited to what is needed to run, support and secure the Service. {{STAFF_ACCESS_CONTROLS}}

## 4. Security

We maintain the technical and organizational measures in Annex 2 and may improve them, but will not lower the overall level of protection during the term.

## 5. Subprocessors

- You authorize the subprocessors on our [Subprocessor list](subprocessors.md).
- We will give at least {{SUBPROCESSOR_NOTICE_DAYS}} days notice of a new subprocessor by {{SUBPROCESSOR_NOTICE_METHOD}}. You may object on reasonable data protection grounds within that period; if we cannot resolve the objection, you may end the affected part of the Service.
- We impose data protection obligations on each subprocessor that are no less protective than this DPA, and remain responsible for them.
- Services you connect yourself with your own account (for example your own Brevo, Twilio, WhatsApp Business, email or social network accounts) act under your agreement with them and are not our subprocessors.

## 6. International transfers

Customer Personal Data is stored in the locations shown in the Subprocessor list, which may be outside the Kingdom of Saudi Arabia or the United Arab Emirates. {{TRANSFER_MECHANISM}}

## 7. Assistance with data subject requests

ANUM gives each user self-service access, export and deletion of their own data under Settings → Privacy. Workspace owners can remove shared records through their pages. For requests the Service cannot fulfil by itself we will help you, taking into account the nature of the processing, within {{DSR_ASSISTANCE_DAYS}} days of your request. If a data subject contacts us directly about Customer Personal Data, we will refer them to you.

## 8. Personal data breaches

We will notify you without undue delay, and in any case within {{BREACH_NOTICE_HOURS}} hours, after becoming aware of a personal data breach affecting Customer Personal Data. The notice will describe what happened, the data and people affected as far as known, the likely consequences and what we are doing about it, and we will update it as we learn more. Each workspace's audit log helps establish who did what.

## 9. Assessments and consultations

We will give you reasonable information to help with data protection impact assessments and with consultations with a regulator about the Service.

## 10. Return and deletion

- During the term, users can export their own data and owners can export the workspace audit log.
- At the end of the Service, at your choice, we will make your data available for export during {{EXPORT_WINDOW}} and then delete Customer Personal Data from the live systems within {{DELETION_DAYS}} days, unless the law requires us to keep it.
- Copies in backups are not edited; they expire on the backup schedule in Annex 2 (35 days for off-site copies as configured).
- ANUM does not yet offer a self-service deletion of an entire organization; we perform it as an operator task: {{TENANT_DELETION_PROCEDURE}}.

## 11. Audits

We will make available the information reasonably needed to show compliance with this DPA, including this DPA's Annex 2 and our security documentation. {{AUDIT_RIGHTS}} We hold no third-party security certification (such as ISO/IEC 27001 or a SOC 2 report) at the date of this draft.

## 12. Liability and order of precedence

Liability under this DPA is subject to the limits in the Terms. If this DPA conflicts with the Terms on the processing of personal data, this DPA prevails.

## Annex 1: Details of processing

| Item | Description |
|---|---|
| Subject matter and duration | Providing ANUM OS for the term of the Terms, plus the deletion period in section 10. |
| Nature and purpose | Hosting, storing, organizing, retrieving, transmitting and deleting Customer content to provide the Service, including sending text to the configured AI model to answer requests and run tasks the Customer's users start, and to connected services the Customer chooses. |
| Data subjects | The Customer's users; people whose details the Customer puts into ANUM, such as its customers, leads, contacts, message and call participants, and employees. |
| Categories of personal data | Names, email addresses, phone numbers, job and company details, message and email metadata, message, chat, call and meeting content, transcripts and recordings, files the Customer uploads, and any other personal data the Customer chooses to process. |
| Special categories | None intended. The Customer must not put special categories into ANUM unless it has the basis and safeguards Data Protection Law requires. |
| Retention | As set out in the [Privacy Policy](privacy-policy.md) section "How long we keep it" and the Customer's own retention settings. |

## Annex 2: Technical and organizational measures

These describe ANUM as built and as deployed by the configuration in the repository; the owner must confirm each item for the production deployment.

**Separation of customers.** Every customer table in PostgreSQL uses forced row-level security on the tenant id, and workspace and user scope where it applies. Each request runs with its tenant context set. Cross-tenant background jobs find work through narrow database roles that cannot bypass row-level security and then act inside each tenant's context. Automated tests check that one tenant cannot read or change another's rows.

**Access control.** Sign-in through Keycloak with OpenID Connect (authorization code with PKCE). Every password sign-in requires a second factor (authenticator app or recovery code); passkeys with user verification are accepted. Workspace roles (owner, member, viewer) and organization administrators decide what each user can do, checked on the server for every request. Development sign-in shortcuts are refused outside development environments.

**Approvals and AI safety.** Every AI tool call goes through the runtime's mediation, is checked against policy, logged and paused for a person's approval when its risk requires it; approvals are bound to the exact payload shown to the approver. Contact opt-outs are enforced before any send. Model endpoints are guarded against server-side request forgery, and monthly model budgets can cap spend.

**Encryption in transit.** Public traffic uses HTTPS with certificates from Let's Encrypt and HTTP Strict Transport Security. Traffic between services on the same server runs on private container networks.

**Encryption at rest.** Stored service credentials (AI provider keys, integration and OAuth tokens, webhook secrets) are encrypted with a Fernet key held outside the database, with a re-encryption procedure for key rotation. Workspace files are stored in Backblaze B2 with its server-side encryption turned on. Off-site backups are encrypted on our server (rclone crypt) before upload and stored with server-side encryption. The database disk relies on the hosting provider's volume encryption: {{HOST_DISK_ENCRYPTION_CONFIRMATION}}.

**Logging and audit.** Security-relevant actions (membership and role changes, approvals, governance and budget changes, privacy exports and deletions, integration changes, sends) are written to an append-only audit table that the application can insert into and read but not change or delete. Owners and organization administrators can search and export it. Telemetry carries ids, never prompts, replies or credentials.

**Backups and recovery.** A nightly database backup (02:41 UTC) is kept on the server for 7 days and copied off-site, encrypted, where a storage lifecycle rule expires copies after 35 days. A restore drill restores into a scratch database and verifies row counts per tenant and row-level security.

**Application hardening.** Request size limits, per-client rate limits, strict security headers and a content security policy, signed short-lived file links, an allow list of file types, and a startup check that refuses insecure configuration outside development.

**Secure development.** Every change runs automated tests and security scans in CI: dependency audits (pip-audit, pnpm audit, OSV-Scanner, cargo audit), static analysis (Bandit, CodeQL, Flutter analyze, Clippy), secret scanning of the full history (gitleaks) and container image scanning (Trivy). Deployed images are signed and verified before they run on Kubernetes deployments.

**Data minimization.** Email bodies are never stored. Exports leave out secrets and tokens. Push notifications carry fixed text, never content. Error reports, when turned on, remove user identity, request bodies, query strings and cookies.

**Gaps the owner must review.** No external penetration test has been performed yet. There is no third-party certification. Organization-wide deletion is an operator task, not a self-service feature. The one-server deployment keeps all secrets in one file on that server.
