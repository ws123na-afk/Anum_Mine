# Email: the unified inbox

ANUM reads the owner's business mailboxes (Gmail / Google Workspace, Outlook / Microsoft 365, Zoho Mail) through each provider's official API and OAuth, shows them as one inbox, sorts and summarises messages, drafts replies with the model, and sends a reply only after an owner approves the exact message. Code: `services/api/anum_api/email_providers.py` (provider clients), `email_inbox.py` (settings, storage, sync, triage, tools), `email_api.py` (routes), `db/email_repository.py` and migration `0035_email_inbox`; web `apps/web/src/InboxView.tsx`; Flutter `apps/mobile/lib/features/email/`.

## What it does

| Part | Behaviour |
| --- | --- |
| Connect | `POST /api/v1/email/oauth/{gmail\|outlook\|zoho}/start` (owners) returns the provider's consent URL. Authorization code flow with PKCE (S256) for Google and Microsoft; Zoho's server-based clients use the code, the client secret and the state check (Zoho documents no PKCE). The `state` is Fernet-encrypted (`ANUM_SECRETS_KEY`) and expires after `ANUM_EMAIL_OAUTH_STATE_TTL_SECONDS` (15 min). The provider returns the browser to `GET /api/v1/email/oauth/callback`, which only stores the code (encrypted) on the pending request and sends the browser to the web app (`/?email_connect=<id>#inbox`). The web app then calls `POST /api/v1/email/oauth/complete` as the signed-in person: the code is exchanged only if that person started the connection (a link opened in someone else's browser connects nothing), once. |
| Tokens | Refresh and access tokens are stored only as Fernet ciphertext per mailbox (`email_accounts`), refreshed when they are about to expire (Microsoft's rotated refresh token replaces the stored one). They never appear in an API response, a log or an audit record. |
| Visibility | A mailbox is shared with the workspace, or *private* (only the person who connected it sees it, its messages and drafts). |
| Health | Each mailbox shows its status (`connected`, `error`, `reauth_required`), last sync time and last error; the Integrations page lists the three providers (`email_gmail`, `email_outlook`, `email_zoho`) with whether the operator set them up. An owner can switch a provider off for a workspace there: connecting, syncing, reading and the email tools then refuse that provider's mailboxes. |
| Disconnect | `DELETE /api/v1/email/accounts/{id}` (owners) revokes ANUM's grant at Google and Zoho, then deletes the tokens, synced messages and drafts. Microsoft has no per-app revoke call: the owner removes ANUM at myapps.microsoft.com (the response says so). |

### Sync

Inbox metadata only: sender, recipients, subject, snippet, labels, thread, read and archive state. Bodies and attachments are fetched from the provider when someone opens a message and are never stored.

- **Gmail**: first sync lists `in:inbox newer_than:<ANUM_EMAIL_INITIAL_SYNC_DAYS>d`; afterwards `users.history.list` from the stored `historyId` (new inbox messages, label changes: read, archived; deletions). An expired history id falls back to a full sync.
- **Outlook**: `me/mailFolders('inbox')/messages/delta` with a `receivedDateTime` filter; the `@odata.deltaLink` is the cursor; `@removed` items leave the inbox (archived). A `410` restarts from a full sync.
- **Zoho Mail**: no delta API. The cursor is the newest `receivedTime` seen; each pass re-reads the newest page (read-state changes) and pages back until it reaches it. Read-state changes to older Zoho messages show up only when they are re-read.

At most `ANUM_EMAIL_SYNC_MAX_MESSAGES` new messages per pass. Syncs run when the Inbox opens (mailboxes not synced in the last two minutes), on **Sync**, after connecting, and every `ANUM_EMAIL_SYNC_INTERVAL_SECONDS` (default 300; 0 is off) in the background with PostgreSQL. The background job discovers connected mailboxes as `anum_maintenance` (it sees only `id`, `tenant_id`, `workspace_id`, `connected_by`, `status`, `last_sync_at`) and syncs each as the application role inside its tenant and workspace context, never with `BYPASSRLS`. A lease column stops two replicas syncing one mailbox at the same time.

### Reading

`GET /api/v1/email/messages` (filters: mailbox, category, priority, unread, archived, `q` over sender, subject and snippet; keyset paging with `before`), `GET /api/v1/email/messages/{id}` (thread, drafts, plain text of the body capped at `ANUM_EMAIL_BODY_MAX_BYTES`; HTML is converted to text and never rendered), `GET .../attachments/{attachment_id}` (download only: `Content-Disposition: attachment`, `nosniff`, a sandboxing CSP, a safe content-type allowlist, capped at `ANUM_EMAIL_ATTACHMENT_MAX_BYTES`).

### AI triage and drafts

- At sync, deterministic rules put each message in one category (`order`, `customer_question`, `supplier`, `invoice`, `spam_marketing`, `personal`, `other`) and a priority (`high`, `normal`, `low`). No model, no cost.
- **Summarise and suggest a reply** (`POST .../analyze`) is one model call through the workspace's budgeted gateway (monthly budgets apply; a used-up budget answers 402). The email reaches the model only inside a provenance-labeled untrusted block (`source=email`) after `UNTRUSTED_DATA_RULES`: email content is data, never instructions. The result is stored with `triage_source=model` and the model's name.
- **Draft with AI** (`POST .../drafts`) writes a reply (optionally following the person's own instructions, which are trusted) and saves it to the provider's Drafts folder where the API allows it. Each draft records its provenance (`source=model` with provider and model, or `person`; a person's edit of a model draft is shown as such).

### Sending

`POST /api/v1/email/drafts/{id}/send-approval` creates a task paused at a `send_email` call carrying exactly the draft's sender, recipients, subject and body. Sending is high risk, so it always waits for approval; the owner approves with the payload hash shown (`POST /api/v1/approvals/{id}/approve {"payload_hash": ...}`), and only then does the runtime send that text to those recipients (Gmail `messages.send` with the thread and `In-Reply-To`, Graph `reply`/`sendMail`, Zoho `messages` with `action=reply`). Editing the draft withdraws a pending approval. Organization policy packs can deny `send_email` outright.

### Tools for tasks, chat and voice

| Tool | Risk | What |
| --- | --- | --- |
| `search_email` | low | Filters and search over the synced inbox; the task result lists sender and subject. |
| `read_email` | low | One message's text (capped) and attachment list. |
| `mark_email_read` | low | Read or unread at the provider. |
| `archive_email` | medium | Out of the inbox at the provider. |
| `draft_reply` | medium | Model or given text, saved as a draft; sends nothing. |
| `send_email` | high | Sends an approved draft. |

A task such as "ANUM, any urgent customer emails?" selects the `anum.email` skill and runs `search_email` with filters taken from the person's own words (urgent, customer, order, invoice, supplier, unread), never from model output. The web Inbox's buttons call the same tools under `ToolPolicy`: allowed calls run at once and are audited; a call that workspace or organization policy pauses becomes a task waiting for approval. Voice answers "any urgent customer emails?" from the synced metadata (read-only, no model call) and points to the Inbox for replies. The Assistant chat has no tools (docs/conversations.md); ask in a task or by voice.

### Who can do what

| Role | Read | Mark read, archive, draft, ask to send | Connect, disconnect | Approve sending |
| --- | --- | --- | --- | --- |
| Owner | yes | yes | yes | yes |
| Member | yes | yes | no | no |
| Viewer | no | no | no | no |

## Owner setup

Set these on the API (`services/api/.env` locally, `infra/oracle/.env` on the Oracle VM, the secret store elsewhere). Secrets are never committed.

| Setting | What |
| --- | --- |
| `ANUM_EMAIL_PUBLIC_API_URL` | The API's public https URL. The redirect URI is `<this>/api/v1/email/oauth/callback`. |
| `ANUM_EMAIL_WEB_URL` | The web app's URL the browser returns to. |
| `ANUM_EMAIL_GOOGLE_CLIENT_ID`, `ANUM_EMAIL_GOOGLE_CLIENT_SECRET` | Google OAuth client. |
| `ANUM_EMAIL_MICROSOFT_CLIENT_ID`, `ANUM_EMAIL_MICROSOFT_CLIENT_SECRET`, `ANUM_EMAIL_MICROSOFT_TENANT` | Azure app registration; tenant `common` (work, school and personal accounts), `organizations`, `consumers` or a tenant id. |
| `ANUM_EMAIL_ZOHO_CLIENT_ID`, `ANUM_EMAIL_ZOHO_CLIENT_SECRET`, `ANUM_EMAIL_ZOHO_DATA_CENTRE` | Zoho API console client; data centre `com`, `eu`, `in`, `com.au`, `jp`, `ca`, `sa` or `com.cn`. |
| Optional | `ANUM_EMAIL_INITIAL_SYNC_DAYS` (30), `ANUM_EMAIL_SYNC_MAX_MESSAGES` (200), `ANUM_EMAIL_SYNC_INTERVAL_SECONDS` (300, 0 = off), `ANUM_EMAIL_BODY_MAX_BYTES` (256 KiB), `ANUM_EMAIL_ATTACHMENT_MAX_BYTES` (10 MiB), `ANUM_EMAIL_OAUTH_STATE_TTL_SECONDS` (900). |

`ANUM_SECRETS_KEY` encrypts the tokens; rotating it with `python -m anum_api.rotate_secrets` does not cover mailbox tokens yet, so keep the old key in the list until mailboxes are reconnected.

### Gmail / Google Workspace (Google Cloud console)

1. console.cloud.google.com → create or pick a project → **APIs & Services → Library** → enable **Gmail API**.
2. **OAuth consent screen**: user type External (or Internal for a Workspace-only organisation), app name, support email; scope `https://www.googleapis.com/auth/gmail.modify` (read, label, draft and send; no permanent delete). While the app is in *Testing*, add the mailbox owners as test users (refresh tokens of apps in Testing expire after 7 days; publish the app, which for a restricted Gmail scope means Google's verification, to keep them).
3. **Credentials → Create credentials → OAuth client ID → Web application**; Authorized redirect URI `https://<API>/api/v1/email/oauth/callback`.
4. Put the client id and secret in `ANUM_EMAIL_GOOGLE_CLIENT_ID` / `ANUM_EMAIL_GOOGLE_CLIENT_SECRET`.

### Outlook / Microsoft 365 (Azure portal, Microsoft Entra ID)

1. portal.azure.com → **Microsoft Entra ID → App registrations → New registration**; supported account types to match `ANUM_EMAIL_MICROSOFT_TENANT` (any organisation plus personal accounts for `common`).
2. Redirect URI: platform **Web** (not SPA: the code is exchanged on the server with the secret), `https://<API>/api/v1/email/oauth/callback`.
3. **API permissions → Microsoft Graph → Delegated**: `offline_access`, `User.Read`, `Mail.ReadWrite`, `Mail.Send`. Grant admin consent in a work tenant if users may not consent themselves.
4. **Certificates & secrets → New client secret**; copy the value (shown once) to `ANUM_EMAIL_MICROSOFT_CLIENT_SECRET`, the Application (client) ID to `ANUM_EMAIL_MICROSOFT_CLIENT_ID`. Note the secret's expiry date: ANUM cannot refresh tokens after it expires.

### Zoho Mail (Zoho API console)

1. api-console.zoho.com (or the console of your data centre, e.g. `api-console.zoho.sa` for Saudi Arabia) → **Add Client → Server-based Applications**.
2. Homepage URL: the web app; Authorized Redirect URI `https://<API>/api/v1/email/oauth/callback`.
3. Copy the Client ID and Client Secret to `ANUM_EMAIL_ZOHO_CLIENT_ID` / `ANUM_EMAIL_ZOHO_CLIENT_SECRET`, and set `ANUM_EMAIL_ZOHO_DATA_CENTRE` to the domain your Zoho account uses (`accounts.zoho.<dc>`).
4. Scopes requested: `ZohoMail.accounts.READ`, `ZohoMail.folders.READ`, `ZohoMail.messages.ALL`.

Then restart the API, open **Inbox** in the web app and choose **Connect**.

## Limits and what is not done yet

- Mailboxes are connected from the web app; the Flutter app lists, reads, drafts and approves sending, and points to the web for connecting and attachment downloads.
- Composing a brand-new message (not a reply) exists in the `send_email` tool path but has no screen yet.
- Zoho drafts saved by ANUM stay in Zoho's Drafts after sending or discarding (deleting them needs the drafts folder id); Zoho read-state changes to older messages are picked up only when re-read.
- Mailbox tokens are not re-encrypted by `rotate_secrets` yet.
- Privacy export and "delete my data" do not include mailboxes; disconnecting deletes everything ANUM holds for a mailbox.
