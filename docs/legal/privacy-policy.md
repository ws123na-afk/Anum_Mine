# Privacy Policy

> **DRAFT, owner and a lawyer must review before publishing.** This text describes what the ANUM software does, checked against the code and deployment configuration on 18 October 2026. It is not legal advice and it does not say which law applies to you. Every `{{PLACEHOLDER}}` must be replaced and every statement confirmed before this page is marked published.

Last updated: {{EFFECTIVE_DATE}}

## Who we are

{{LEGAL_ENTITY_NAME}} ("we", "us") provides ANUM OS ("ANUM", "the Service"), a business operations workspace with AI assistants, at {{SERVICE_URL}} and in the ANUM desktop, Android and iOS apps.

- Registered address: {{REGISTERED_ADDRESS}}
- Licence or registration: {{TRADE_LICENCE_NUMBER}}, issued by {{LICENSING_AUTHORITY}}
- Privacy contact: {{PRIVACY_CONTACT_EMAIL}}
- Data protection officer or privacy lead, where one is appointed: {{DPO_OR_PRIVACY_LEAD}}

This policy is written to describe our processing in the terms used by the Saudi Personal Data Protection Law (PDPL, Royal Decree M/19 of 1443H, as amended, and its Implementing Regulations) and the UAE Federal Decree-Law No. 45 of 2021 on the Protection of Personal Data. Which law applies to a given person or customer, and whether a free zone regime (for example ADGM or DIFC) applies instead, is {{APPLICABLE_LAW_CONFIRMATION}}.

## Our two roles

- **For your account and our own business** (sign-in, billing, support, security of the Service) we decide why and how personal data is used. We are the controller for that data.
- **For the content an organization puts into its ANUM workspace** (tasks, files, conversations, contacts, emails, messages, calls) the organization decides what goes in and why. The organization is the controller and we process that content on its behalf, under our [Data Processing Addendum](data-processing-addendum.md). If you are a member of an organization's workspace, that organization's own privacy notice also applies to you, and requests about workspace content usually go to the organization first.

## What we collect

| Data | What it is | Where it comes from |
|---|---|---|
| Account | User id, name, email address, sign-in method, two-factor settings (authenticator app, recovery codes, passkeys). Kept by our sign-in service (Keycloak, run by us). | You, or the administrator who invited you. Google or Apple, if you choose "Continue with Google" or "Continue with Apple" and the deployment offers it. |
| Membership | Which organization and workspaces you belong to and your role. | Your administrator. |
| Workspace content | Tasks, runs, approvals, files, memories and notes, knowledge collections, reports, calendar events, contacts, leads, campaigns and the other records your workspace creates. | You and your colleagues. |
| Assistant conversations and voice | What you type or say to the assistant and its answers. Voice is turned into text on your device or, if the deployment configures one, by a speech service; ANUM stores text, not voice recordings of the assistant. | You. |
| Connected services | If your workspace connects them: email metadata (sender, recipients, subject, snippet, labels; email bodies are never stored by ANUM), WhatsApp messages, phone call logs, notes, transcripts and recordings, meeting transcripts, social media and sales channel data. Access tokens for these services are stored encrypted. | The services your workspace connects. |
| Phone push | The push token of each phone you register, platform and a label. | Your phone. |
| Preferences | Language, theme, notification and chat memory settings. | You. |
| Security and technical records | Audit records (who did what, when, with ids and counts, not your content), request logs with ids, times and error codes, and error reports if the deployment turns error reporting on (with user identity, request bodies, query strings and cookies removed). | Our servers. |

We do not sell personal data, do not use advertising identifiers and do not track you across other websites or apps.

## Why we use it

| Purpose | Data | Basis we rely on, to be confirmed by counsel |
|---|---|---|
| Provide the Service you or your organization signed up for | Account, membership, workspace content, connected services | {{LEGAL_BASIS_CONTRACT}} |
| Keep the Service secure: sign-in, two-factor, permissions, approvals, audit records, abuse prevention | Account, security records | {{LEGAL_BASIS_SECURITY}} |
| Answer support requests | What you send us | {{LEGAL_BASIS_SUPPORT}} |
| Meet legal obligations | What the law requires | Legal obligation |

We use workspace content only to provide the Service to that workspace. We do not use it to train AI models, and the AI providers we use are configured through their business APIs, whose terms are listed in the [Subprocessor list](subprocessors.md).

## AI features

When you ask the assistant something, run a task or use an AI feature, the text needed for that request (your question, recent turns of the conversation, relevant memories and workspace files, and the tool results the assistant reads) is sent to the language model configured for your workspace. Depending on configuration this is Anthropic, an OpenAI-compatible provider, or a model running on our own server (Ollama), in which case the text does not leave our infrastructure. Your Settings → Privacy page names the provider your messages go to.

After an answer, if "Remember things from my chats" is on, a second short request to the same provider picks out lasting facts you stated and saves them as private memories. You can turn this off, and an organization can turn it off for everyone.

AI actions that send something to another person or change something outside ANUM pause for a person's approval according to the workspace's approval policy.

## Where data is stored

Our production servers run on {{HOSTING_PROVIDER_AND_REGION}}. Workspace files and encrypted backups are stored with Backblaze B2 in {{FILE_STORAGE_REGION}}. The full list of service providers, what each receives and where, is in the [Subprocessor list](subprocessors.md).

Some of these locations may be outside the Kingdom of Saudi Arabia or the United Arab Emirates. {{CROSS_BORDER_TRANSFER_STATEMENT}}

## Who receives it

- **Our service providers** listed in the [Subprocessor list](subprocessors.md), only for what each one does for us.
- **Services your workspace connects**, such as an email provider, WhatsApp, a phone provider, Brevo, social networks or sales channels. They receive data only when your workspace has connected them, and actions that send data out need an approval where the workspace's policy requires it.
- **Your organization's administrators**, who can see workspace records and the audit log according to their role.
- **Authorities**, when the law requires it.

## How long we keep it

| Data | Kept |
|---|---|
| Assistant conversations | Deleted automatically after {{CONVERSATION_RETENTION_DAYS}} days without activity (ANUM's default is 30), or earlier when you delete them. |
| Voice transcripts | You choose per session: deleted when the session ends, after 30 days, or until you delete them. |
| Private memories | Until you delete them. |
| What ANUM learned from your feedback | Feedback signals are deleted after {{LEARNING_SIGNAL_RETENTION_DAYS}} days (default 180). |
| Phone call recordings | For the period the workspace owner sets, from 1 to 365 days (default 30). |
| Calls between teammates in the app | Deleted {{CALLS_RETENTION_DAYS}} days after they end (default 365). |
| WhatsApp messages | For the period the workspace owner sets; by default until deleted. |
| Email | ANUM never stores email bodies. Message metadata is kept while the mailbox stays connected and is deleted when it is disconnected. Messages written in the Email Center follow each mailbox's retention setting. |
| Audit records | For as long as the workspace exists. They contain ids and counts, not your content. |
| Workspace records (tasks, approvals, files, shared notes and the rest) | For as long as the workspace keeps them. |
| Error reports, if turned on | {{ERROR_REPORT_RETENTION_DAYS}} days (the self-hosted default is 30). |
| Backups | Nightly database backups are kept on the server for {{LOCAL_BACKUP_DAYS}} days (default 7) and the encrypted off-site copies for {{OFFSITE_BACKUP_DAYS}} days (the configured bucket rule is 35). Data you delete stays in backups until they expire. |
| Account | Until your administrator removes it. |

## Your rights

Depending on the law that applies to you, you may have the right to be informed, to access your data and get a copy, to correct it, to have it deleted, to move it to another service, to object to or restrict some processing, and to withdraw consent where we rely on consent.

In ANUM, under **Settings → Privacy**, every signed-in user can:

- see a count of what is stored about them in the current workspace;
- download their data as one JSON file;
- delete their private data: conversations, private memories, voice transcripts, preferences, registered phones, command history, what ANUM learned from them, imported meetings, logged calls, private mailboxes and their own finance records, among others.

Records the whole workspace relies on (tasks, approvals, shared notes, files, workspace mailboxes) are kept and are counted on that page; a workspace owner can remove them. Your sign-in account itself is closed by your organization's administrator. For anything else, including correcting your details or a request you cannot make in the app, contact {{PRIVACY_CONTACT_EMAIL}}. We answer within {{RESPONSE_DAYS}} days.

If you are not satisfied with our answer you may complain to the authority that supervises us, for example the Saudi Data and Artificial Intelligence Authority (SDAIA) or the UAE Data Office, as applicable: {{SUPERVISORY_AUTHORITY}}.

## Security

Each organization's data is separated in the database by PostgreSQL row-level security, connections use HTTPS, stored service credentials are encrypted, password sign-in requires a second factor, and security-relevant actions are written to an audit log that nobody can edit. Our [Trust page](https://{{WEB_HOST}}/trust) lists these measures in more detail.

## Cookies and browser storage

The ANUM web app and API set no cookies. The sign-in service (Keycloak) sets the cookies it needs to keep you signed in and to protect the sign-in form; they are strictly necessary and are not used for analytics or advertising. The web app keeps your language and theme choices and the current workspace in your browser's local storage, and your sign-in session in session storage, which is cleared when you close the tab or sign out. We use no analytics, advertising or tracking scripts.

If you use the in-browser voice, your browser downloads the voice model files from Hugging Face and a pinned JavaScript runtime from the jsDelivr CDN. Those requests go from your browser to them directly and carry your IP address, as any web request does.

## Children

The Service is for businesses and is not meant for anyone under {{MINIMUM_AGE}}.

## Changes

We will post changes on this page and update the date above. {{CHANGE_NOTICE_METHOD}}

## Contact

{{LEGAL_ENTITY_NAME}}, {{REGISTERED_ADDRESS}}. Email {{PRIVACY_CONTACT_EMAIL}}.
