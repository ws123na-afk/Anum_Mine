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

`GET /api/v1/email/messages` (filters: mailbox, category, priority, unread, archived, `urgent` (true: priority high; false: anything else, untriaged included), `replied` (a reply from ANUM was sent), `q` over sender, subject and snippet; keyset paging with `before`), `GET /api/v1/email/messages/{id}` (thread, drafts, plain text of the body capped at `ANUM_EMAIL_BODY_MAX_BYTES`; HTML is converted to text and never rendered), `GET .../attachments/{attachment_id}` (download only: `Content-Disposition: attachment`, `nosniff`, a sandboxing CSP, a safe content-type allowlist, capped at `ANUM_EMAIL_ATTACHMENT_MAX_BYTES`).

Every id that reaches a provider URL path (message, attachment, draft, folder, reply and Zoho account ids) must match a strict id pattern (letters, digits, `+/=_-`, at most 2048 characters, no dots) and is percent-encoded; the hosts are fixed (Zoho's data centre from an allow-list, Microsoft's tenant from settings, checked as dot-separated labels). A bad attachment id in the request answers 422; a stored id that fails the check answers 409 (sync the mailbox again) and nothing is sent to the provider.

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
| `unarchive_email` | low | Back to the inbox (Gmail and Zoho Mail): the undo of `archive_email`. |
| `draft_reply` | medium | Model or given text, saved as a draft; sends nothing. |
| `send_email` | high | Sends an approved draft. |

Every tool declares its [autonomy](autonomy.md) category (`email`) and kind: `read` for search and read, `update` for read state and moving back, `archive`, `draft` and `send`. When a task's tool call runs on its own, it is listed under Activity: reading silently, marking read silently and archiving with a notification, both with **Undo** for 24 hours (`mark_email_read` sets the read state back; `unarchive_email` moves the message back). Outlook archives are not undoable from ANUM, because Microsoft Graph gives a moved message a new id. `send_email` is free text, so it always asks for approval, whatever the autonomy settings; the sender of the message being answered counts as a known recipient (anyone else on the reply is first contact), which only changes the recorded reason. The workspace's quiet hours, daily caps, autonomy level for email, blocked recipients and the medium-risk approval switch apply to archiving and drafting as to every tool.

A task such as "ANUM, any urgent customer emails?" selects the `anum.email` skill and runs `search_email` with filters taken from the person's own words (urgent, customer, order, invoice, supplier, unread), never from model output. The web Inbox's buttons call the same tools under `ToolPolicy`: allowed calls run at once and are audited; a call that workspace or organization policy pauses becomes a task waiting for approval. Voice answers "any urgent customer emails?" from the synced metadata (read-only, no model call) and points to the Inbox for replies. The same question in the Assistant chat runs as a task through the runtime and comes back as a card listing the messages (links open them in the Inbox) above a short written answer; the mail's text reaches that answer's model only as labeled, untrusted data and can never select a tool or approve anything. Sending is never planned from chat: drafts are approved in the Inbox ([Conversations: tools in chat](conversations.md#tools-in-chat)).

### Who can do what

| Role | Read | Mark read, archive, draft, ask to send | Connect, disconnect | Approve sending |
| --- | --- | --- | --- | --- |
| Owner | yes | yes | yes | yes |
| Member | yes | yes | no | no |
| Viewer | no | no | no | no |

### Routing to department assistants

At the end of a sync, new mail in a **shared** mailbox (never a private one, never the first history sync, never sent, draft or bulk mail) is offered with its subject and preview to department assistants that opted into email; a keyword match becomes a work item, once per open thread. Best effort: a routing failure never fails the sync ([Team work](team-work.md#inbound-routing)).

## Web page layout

The Inbox follows the shared [page layout](design/anum360.md#page-layout): one header row with the mailbox status chip (count, last sync and any mailbox needing attention; it opens the mailbox setup in a drawer) and **Sync all**; a summary band from `GET /email/summary` (unread, high-priority unread, unread customer questions and orders) and `GET /email/drafts` (reply drafts still waiting: draft, waiting for approval or failed); then three panes that fill the window: filters with the **Drafts waiting** queue (opening a draft opens its message), the message list, and the message. From 1024 to 1279px the filters sit in a row above the list and message. On a wide screen the message pane opens on the first unread message. With no mailbox connected, the setup is the page's empty state.

## Email summary (mobile)

The Flutter route `/email/summary` (`?tab=urgent|replied|other`) is the ANUM 360 email summary: the urgent and unread counts from `GET /api/v1/email/summary`, the All / Urgent (`urgent=true`) / Replied (`replied=true`) / Other (`urgent=false&replied=false`) tabs with sender initials, subject and a High / Medium / Low priority pill, paging, and View all emails (the inbox). Opening a message shows it with its drafts and the approve-to-send card, as in the inbox.

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

`ANUM_SECRETS_KEY` encrypts the tokens; after rotating it, `python -m anum_api.rotate_secrets` re-encrypts every mailbox's tokens, including mailboxes waiting to be reconnected ([Runbooks](runbooks.md#rotating-anum_secrets_key)). Pending connections and OAuth `state` values expire within `ANUM_EMAIL_OAUTH_STATE_TTL_SECONDS` and are not rotated: keep the old key for at least that long.

The Helm chart, the Oracle compose stack and `render.yaml` pass these settings; client secrets go only into the deployment's secret store (the chart's `secrets.app`, refused in `config`).

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
- Message bodies are never stored, so retention applies to metadata only: it stays while the mailbox is connected. Privacy export and "Delete my data" cover the caller's mailboxes and drafts ([Privacy](privacy.md)); disconnecting deletes everything ANUM holds for a mailbox.

## Email Center (own mail server)

The Email Center reads and sends mail through the workspace's **own mail server** (for ANUM: Mailcow at `mail.anum360os.com`) over IMAP (implicit TLS, port 993) and SMTP submission (STARTTLS on 587, or TLS on 465). It is separate from the OAuth inbox above: its own tables, routes (`/api/v1/mail`) and tools. Code: `services/api/anum_api/mailbox/` (`config.py`, `server.py`, `mime.py`, `html_safe.py`, `store.py`, `service.py`, `api.py`, `outgoing.py`), `db/mailbox_repository.py`, migrations `0060_email_center`, `0061_mail_sharing_contacts` and `0062_mail_outgoing`; shares, identities and contacts in `mailbox/sharing_api.py`; tests `services/api/tests/test_mail_center.py`, `test_mail_outgoing.py`, `test_postgres_mail_outgoing.py`, `test_mail_sharing.py` and `test_postgres_mail_sharing.py`. The web Email page does not show attachments and scheduled send yet (in progress).

It is **off** until the operator sets `ANUM_MAILSERVER_ENABLED=true`.

### How it works

- **Connecting.** A person types their mailbox address and its password (use a mail server *app password* limited to IMAP and SMTP). ANUM signs in to the IMAP server once to check it, then stores the password only as Fernet ciphertext (`ANUM_SECRETS_KEY`, re-encrypted by `rotate_secrets`). The password is never returned, logged or audited. The server host and ports come only from operator settings, never from the person, so there is no way to point ANUM at another host. Only addresses on `ANUM_MAILSERVER_ALLOWED_DOMAINS` can be connected, and only organisations listed in `ANUM_MAILSERVER_ALLOWED_TENANTS` may use the mail server at all. Login checks are limited to `ANUM_MAILSERVER_CONNECT_ATTEMPTS_PER_HOUR` per person (5).
- **Private, unless its owner shares it.** A mailbox belongs to the person who connected it: forced RLS plus restrictive owner policies, and every query repeats the owner. Workspace owners do not see other people's mailboxes. A person may connect several mailboxes; the page lists their own first, then those shared with them.
- **Sharing.** The owner of a mailbox can share it with another active owner or member of the workspace (never a viewer, never someone outside it), to **read** or to **send**. Read: folders, lists, messages and attachment downloads, without marking anything read. Send: also flags, moves, drafts, replies and asking to send. The mailbox's settings, its password, its shares, its sender identities and deleting for good stay with the owner. Each share is a row in `mail_account_grants` that names the mailbox owner and is tied to the mailbox by a foreign key on (tenant, workspace, mailbox, owner), so nobody can write a share for a mailbox that is not theirs; the reading policy of `mail_accounts` admits the owner and people with a share, while inserting, updating and deleting a mailbox stay owner only. The owner changes or removes a share; the person it was shared with can remove their own (leave). Messages written in a shared mailbox belong to the person who wrote them: the owner does not see their drafts. Sending still waits for an owner of the workspace to approve the exact message, and at send time the share is checked again: a message approved after its writer lost send access is not sent. While a share is gone, the writer's drafts in that mailbox are hidden from them. The send limit counts each writer's own sends per mailbox.
- **Sender identities.** The owner can add up to 20 addresses (aliases) a mailbox sends as, each with a display name, a plain text signature and one default. Their domain must be on `ANUM_MAILSERVER_ALLOWED_DOMAINS`, and the mail server must let that mailbox send as the address (in Mailcow: an alias of the mailbox, or the address in the mailbox's "Allow to send as" list); otherwise the server refuses the send and the message shows as failed. A new message starts from the default identity with its signature (above the quoted text in a reply); picking another sender in the composer swaps the signature. The approval names the From address, and the message goes out with it in `From:` and as the envelope sender. People with send access pick from the identities; they cannot change them.
- **Contacts.** Each person has their own address book (`mail_contacts`, owner only): name, address (stored in lower case, one contact per address), company, phone and notes, at most 5000. The Contacts drawer searches name, address and company, pages by name, and adds, edits and removes contacts; the composer suggests contacts as a person types in To, Cc or Bcc.
- **Reading.** Folders, message lists and messages are read live from the server (nothing is copied into ANUM). Opening a message marks it read. HTML is sanitized on the server with an allowlist (no script, style blocks, frames, forms or event handlers; links limited to http, https and mailto; remote and `cid:` images removed and counted), and the web app shows it in a sandboxed iframe with `default-src 'none'`, so opening mail never loads anything from the sender. Attachments download only (`Content-Disposition: attachment`, `nosniff`, a sandbox CSP, at most `ANUM_MAILSERVER_ATTACHMENT_MAX_BYTES`). Messages over `ANUM_MAILSERVER_MESSAGE_MAX_BYTES` are refused with a pointer to webmail.
- **Writing.** New messages, replies, reply to all and forwards are drafts in ANUM (`mail_drafts`), each with a Message-ID made when it is created on the sender's own domain. Replies carry `In-Reply-To` and `References` and quote the original text.
- **Sending.** `POST /drafts/{id}/send` with an `idempotency_key` creates a task paused at the high-risk `send_mail` tool with exactly the sender, recipients, subject and text. Nothing is sent until an owner approves with the payload hash (`POST /api/v1/approvals/{id}/approve`). Then the draft is claimed atomically (`pending_approval` to `sending`), submitted once over SMTP to the workspace server (which relays it onward), and the same bytes are saved to the Sent folder (skipped if a message with that Message-ID is already there); a reply also sets `\Answered` on the original. Editing a draft withdraws its approval. The same key always returns the same message and approval; a key used for another message is refused (409). If the connection breaks while the server is taking the message, the draft becomes `unknown` and is never retried by itself: the person checks Sent. At most `ANUM_MAILSERVER_SEND_PER_HOUR` sends per mailbox per hour (30) and `ANUM_MAILSERVER_MAX_RECIPIENTS` recipients per message (50).
- **Attachments and scheduled send**: see [Attachments](#attachments) and [Scheduled send](#scheduled-send) below. Both are part of the approved call, so the payload hash covers the files and the time.
- **Deleting for good** (Trash and Junk only) pauses the high-risk `delete_mail` tool for approval of exactly those messages. Archive, Junk, Trash and back to the inbox are plain moves.
- **Audit.** Every state-changing route writes an audit record with ids and counts only (`tests/audit_probe.py` walks them all).
- **Privacy.** "Download my data" exports the caller's mailboxes (never the password, not even encrypted), the messages written in ANUM, the names, types, sizes and SHA-256 of their attached files, their mailboxes' sender identities and shares, the shares they received and their contacts; "Delete my data" deletes their mailboxes (with those shares and identities), every message they wrote (in shared mailboxes too) and the stored files, the shares they received and their contacts. Mail on the server is not ANUM's copy and is not touched.

### Who can do what

| Role | Read | Connect own mailbox, flags, moves, drafts, ask to send, contacts, share own mailbox | Approve sending or deleting for good |
| --- | --- | --- | --- |
| Owner | yes | yes | yes |
| Member | yes | yes | no |
| Viewer | no | no | no |

Within a mailbox, what a person may do depends on their access to it:

| Access | Read | Flags, moves, drafts, ask to send, use identities | Password, disconnect, shares, identities, delete for good |
| --- | --- | --- | --- |
| Its owner | yes | yes | yes |
| Shared to send | yes | yes | no |
| Shared to read | yes (nothing is marked read) | no | no |

### API (`/api/v1/mail`)

Errors use the API's usual body `{"error": {"code", "message", ...}}`; `message` is written for people. Status codes: 409 when the Email Center is off, not set up, the saved password was refused (`auth_failed`) or a message cannot change state; 403 when the role or organisation is not allowed; 503 when the mail server is unreachable (`offline`); 404 for a folder, message or attachment that is gone; 413 for a message or attachment that is too large; 422 for invalid input; 429 for the connect and send limits.

| Method and path | Body or query | Answer |
| --- | --- | --- |
| `GET /setup` | | `MailSetup {enabled, configured, tenant_allowed, imap_host, imap_port, smtp_host, smtp_port, smtp_security, allowed_domains[], can_read, can_act, max_recipients, send_per_hour, attachment_max_bytes, attachments_per_message, attachments_total_bytes, schedule_max_days, accounts: AccountView[]}` (own mailboxes first, then shared ones) |
| `POST /accounts` | `{address, password, display_name?}` | 201 `AccountView {id, address, display_name, status: connected\|auth_failed\|offline\|error, last_error, last_checked_at, created_at, access: owner\|send\|read, owner_user_id, shared_with, grant_id (the caller's own share of a mailbox shared with them, to leave it)}` |
| `PUT /accounts/{id}/password` | `{password}` | `AccountView` (checked against the server first) |
| `POST /accounts/{id}/check` | | `AccountView` with fresh health (sync status) |
| `DELETE /accounts/{id}` | | 204; forgets the password and drafts |
| `GET /accounts/{id}/folders` | | `FolderView[] {id, name, role: inbox\|drafts\|sent\|archive\|junk\|trash\|null, total, unread}`, special folders first |
| `GET /accounts/{id}/messages` | `folder` (default `INBOX`), `limit` (1 to 50, 25), `before` (the previous page's `next_before`), `q` (server-side text search), `unread`, `flagged` | `MessagePage {items: MessageView[], total, next_before}`; `MessageView {uid, folder, subject, sender: {name, address}\|null, to[], cc[], date, message_id, seen, flagged, answered, size, maybe_attachments}` |
| `GET /accounts/{id}/message` | `folder`, `uid` | `MessageDetail {message: MessageView, reply_to[], text, text_truncated, html (sanitized) \| null, remote_images_blocked, inline_images_hidden, attachments: {index, filename, content_type, size, downloadable}[]}`; marks the message read |
| `GET /accounts/{id}/attachment` | `folder`, `uid`, `index` | the file as a download |
| `POST /accounts/{id}/flags` | `{folder, uids[], flag: seen\|flagged, value}` | `ActionResult {status: done, summary}` |
| `POST /accounts/{id}/move` | `{folder, uids[], to}` (`to` is a role: inbox, archive, junk, trash, or a folder id) | `ActionResult` |
| `POST /accounts/{id}/delete` | `{folder, uids[]}` (Trash or Junk only) | `ActionResult {status: waiting_approval, approval}` |
| `GET /drafts` | `account_id?`, `limit` (100) | `DraftView[]` newest first |
| `POST /drafts` | `{account_id, identity_id?, kind: new\|reply\|reply_all\|forward, folder?, uid?, include_attachments? (forward, default true)}` | 201 `DraftView {id, account_id, identity_id, from_address, from_name, kind, to[], cc[], bcc[], subject, body, in_reply_to, message_id, status: draft\|pending_approval\|scheduled\|sending\|sent\|failed\|unknown, error, approval: Approval\|null, approval_note, saved_to_sent, sent_at, send_at, attachments: OutgoingFile[], attachment_note, created_at, updated_at}` |
| `GET /drafts/{id}` | | `DraftView` |
| `PATCH /drafts/{id}` | `{to?, cc?, bcc?, subject?, body?, identity_id?}` (`identity_id: null` sends as the mailbox address) | `DraftView`; withdraws a pending approval |
| `DELETE /drafts/{id}` | | 204 (mail on the server stays) |
| `POST /drafts/{id}/send` | `{idempotency_key, send_at?}` (key: 8 to 120 characters, letters, digits, `._:-`; `send_at`: ISO 8601 with a zone) | `{draft: DraftView, approval: Approval\|null}` |
| `POST /drafts/{id}/cancel-schedule` | | `DraftView` (back to a draft; 409 when not scheduled or already being sent) |
| `GET /scheduled` | `account_id?`, `limit` (50) | `DraftView[]` with a `send_at`, scheduled or waiting for approval, soonest first |
| `POST /drafts/{id}/attachments` | `?name=` and the file as the raw body | 201 `OutgoingFile {id, filename, content_type, size, sha256, scanned, created_at}`; withdraws a pending approval or a schedule |
| `DELETE /drafts/{id}/attachments/{file_id}` | | 204; withdraws a pending approval or a schedule |
| `GET /drafts/{id}/attachments/{file_id}` | | the file as a download |

| `GET /accounts/{id}/grants` | (owner) | `GrantView[] {id, account_id, grantee_user_id, grantee_email, access: read\|send, created_at, updated_at}` |
| `GET /accounts/{id}/share-candidates` | `q?` (owner) | `{user_id, email, role, grant_id, access}[]`: active owners and members other than the caller |
| `POST /accounts/{id}/grants` | `{grantee_user_id, access}` (owner) | 201 `GrantView`; sharing again with the same person changes the access |
| `PATCH /grants/{id}` | `{access}` (owner) | `GrantView` |
| `DELETE /grants/{id}` | | 204: the owner stops sharing, or the person it was shared with leaves |
| `GET /accounts/{id}/identities` | | `IdentityView[] {id, account_id, address, display_name, signature, is_default, created_at, updated_at}`, default first; empty for read access |
| `POST /accounts/{id}/identities` | `{address, display_name?, signature?, is_default?}` (owner) | 201 `IdentityView` |
| `PATCH /identities/{id}` | `{display_name?, signature?, is_default?}` (owner) | `IdentityView` |
| `DELETE /identities/{id}` | (owner) | 204; drafts that used it go back to the mailbox address |
| `GET /contacts` | `q?`, `limit` (1 to 100, 25), `cursor?` | `ContactPage {items: ContactView[], total, next_cursor}`; `ContactView {id, name, email, company, phone, notes, created_at, updated_at}` |
| `POST /contacts` | `{name?, email, company?, phone?, notes?}` | 201 `ContactView`; 409 when the address is already a contact |
| `GET /contacts/{id}`, `PATCH /contacts/{id}`, `DELETE /contacts/{id}` | | `ContactView`, `ContactView`, 204 |

`Approval` is the shape used everywhere (`action`, `arguments`, `payload_hash`, `expires_at`, ...); approve with `POST /api/v1/approvals/{approval.id}/approve {"payload_hash": approval.payload_hash}`.

### Operator view

The platform operator (realm role `platform_operator`, docs/customer-organisations.md) sees the mail health of the organisations they run, and nothing from the mail itself. Code: `services/api/anum_api/mailbox/monitor.py`, `mailcow.py`, `operator_api.py`; migration `0064_mail_operator_view`; tests `services/api/tests/test_mail_operator.py` and `test_postgres_mail_operator.py`.

- **What is counted.** The Email Center adds one to a per-day counter (`mail_ops_counters`: tenant, workspace, UTC day, metric, count) inside the caller's own tenant context when a send succeeds (`sent`), fails (`send_failed`) or ends unknown (`send_unknown`), when the hourly send limit refuses a send (`send_rate_limited`), when the connection-check limit refuses a login check (`connect_rate_limited`), and when the mail server refuses a saved password (`auth_failed`) or cannot be reached (`server_offline`). No person, address, subject or body is stored there. A counter that cannot be written is logged and never breaks the mail action.
- **Who reads it.** Only callers with `platform:operate`; everyone else gets 403 before anything is read. With PostgreSQL the read runs as `anum_platform_reader` (`SET LOCAL ROLE`), which sees only the organisations in `platform_organizations` with `operator_user_id` = the caller, and only `tenant_id, status, last_checked_at` of `mail_accounts` plus the counters. The restrictive owner policy of `mail_accounts` admits `current_user = 'anum_platform_reader'` for that (the same pattern as `anum_maintenance`); a login that merely inherits the role never matches, and the column grant leaves out the address, display name, password and error text.
- **Mailcow API (optional, read only).** Off unless `ANUM_MAILCOW_API_ENABLED=true`. `ANUM_MAILCOW_API_URL` is the Mailcow web host (https, host only) and `ANUM_MAILCOW_API_KEY` a Mailcow **read-only** API key, set only in the API's environment or secret store (never in git, chat or the web app). ANUM calls only `GET /api/v1/get/status/version`, `/get/status/containers`, `/get/mailq/all` and `/get/domain/all`, and returns the version, the containers that are not running, the number of queued messages (never their senders or recipients) and per domain the mailbox count and quota use. The key is never returned, logged or audited. Reading and sending mail never use this API: they stay IMAP and SMTP as each person.

| Method and path | Query | Answer |
| --- | --- | --- |
| `GET /api/v1/platform/mail/health` | `days` (1 to 90, 7) | `MailOperatorView {enabled, days, since, mailboxes, totals: Counts, organisations: {tenant_id, display_name, mailboxes, connected, auth_failed, offline, error, stale, last_sync_at, oldest_sync_at, counts: Counts, health: quiet\|ok\|attention}[]}`; `Counts {sent, send_failed, send_unknown, send_rate_limited, connect_rate_limited, auth_failed, server_offline}`. `stale`: last check older than two days. `attention`: a mailbox is not connected or stale, or a send failed or ended unknown in the window. |
| `GET /api/v1/platform/mail/server` | | `{server: {enabled, configured, imap_host, smtp_host, allowed_tenants (a count), send_per_hour, connect_attempts_per_hour}, mailcow: {state: off\|misconfigured\|unreachable\|refused\|ok, message, version, containers_total, containers_not_running[], queue_messages, domains: {domain, active, mailboxes, max_mailboxes, quota_used_percent}[]}}` |

### Operator settings (`ANUM_MAILSERVER_*`)

| Setting | Default | What |
| --- | --- | --- |
| `ENABLED` | `false` | Turns the Email Center on. |
| `IMAP_HOST`, `IMAP_PORT` | empty, `993` | The mail server's DNS name (its certificate name) and IMAPS port. |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_SECURITY` | empty, `587`, `starttls` | Submission; `tls` for 465. |
| `ALLOWED_DOMAINS` | `anum360os.com` | Mailbox domains that can be connected (comma-separated). |
| `ALLOWED_TENANTS` | empty | Organisations (tenant ids) allowed to use the mail server. |
| `TIMEOUT_SECONDS`, `MESSAGE_MAX_BYTES`, `ATTACHMENT_MAX_BYTES`, `SEND_PER_HOUR`, `MAX_RECIPIENTS`, `CONNECT_ATTEMPTS_PER_HOUR` | 20, 40 MiB, 15 MiB, 30, 50, 5 | Bounds. |
| `OUTBOUND_ATTACHMENT_MAX_BYTES`, `OUTBOUND_MAX_ATTACHMENTS`, `OUTBOUND_TOTAL_MAX_BYTES`, `OUTBOUND_STORAGE_PER_PERSON_BYTES` | 10 MiB, 10, 20 MiB, 200 MiB | Outgoing files: the largest file, files per message, their total, and what one person's unsent messages may hold. |
| `SCHEDULE_POLL_SECONDS`, `SCHEDULE_MAX_DAYS` | 30, 30 | How often each API replica sends what is due (0 is off: scheduled messages wait) and how far ahead a message may be scheduled. |

Outside `ANUM_ENVIRONMENT=local`, with `ENABLED=true` the API refuses to start (and the Helm chart refuses to render) when a host is missing, an IP address or not a DNS name, when the SMTP host is a relay service (Brevo, SendGrid and the like: ANUM submits to your own server, which relays), when `SMTP_SECURITY=none`, or when the allowed domains or organisations are empty.

### Attachments

Files are attached to a draft, a reply or a forward with `POST /drafts/{id}/attachments?name=<file name>` and the file as the raw body. Each file passes the same checks as a [workspace upload](files.md): the accepted types (decided by the extension; anything else is refused with 415 naming what is accepted), the content signature (a `.pdf` must be a PDF), not empty, and the [malware-scan hook](files.md#malware-scanning) (a refusal answers 422 with the scanner's reason; a scanner error answers 503 and nothing is kept). Then the caps: `OUTBOUND_ATTACHMENT_MAX_BYTES` per file, `OUTBOUND_MAX_ATTACHMENTS` and `OUTBOUND_TOTAL_MAX_BYTES` per message, and `OUTBOUND_STORAGE_PER_PERSON_BYTES` over a person's unsent messages (413). ANUM ships no scanner: each file records `scanned` (false when none is installed) and the composer says so.

The bytes go to the workspace's object storage (`tenants/<t>/workspaces/<w>/mail/<file id>/<sha256>`); the row in `mail_attachments` (name, type, size, SHA-256) is private to the person, like the draft (forced RLS plus the restrictive owner policy). The send approval lists every file's name, type, size and SHA-256, so the payload hash covers them; when the message is built the bytes are read back and checked against that SHA-256, and a file that is gone or changed is never sent (the message fails with a sentence saying which file). Adding or removing a file withdraws a pending approval or a schedule.

A forward carries the original's attachments (`include_attachments`, default true), each through the same checks and scan; a file that fails is left out and listed in `attachment_note`. Replies start without the original's files. Discarding a message, disconnecting the mailbox and "Delete my data" delete the stored files. Sent messages keep their files in ANUM until they are removed from the list (the copy in Sent holds them on the mail server).

### Scheduled send

`POST /drafts/{id}/send` with `send_at` (one minute to `SCHEDULE_MAX_DAYS` ahead, with a time zone; the web app sends the person's local time) asks to send at that time. The approval shows and binds the time with everything else. Approving it only marks the message `scheduled`; nothing is sent then. A message must be approved **before it is due**: if it is still waiting for approval more than five minutes after its time, the scheduler withdraws the approval and the message goes back to a draft that says so (and an approval that arrives that late does not send it either). `GET /scheduled` lists the person's scheduled messages.

The scheduler runs in every API replica every `SCHEDULE_POLL_SECONDS` (PostgreSQL or memory). It finds due work as `anum_maintenance`, which may read only the id, scope, owner, status, `send_at` and `send_lease_until` of messages that need work (a column grant and a row policy, migration `0062_mail_outgoing`), and acts on each in its owner's scope as the application role, never with `BYPASSRLS`:

- a `scheduled` message whose time has come is claimed with a compare-and-set to `sending` and a ten-minute lease (one replica wins), checked against the hash of the approved call (a message changed behind the API fails instead), the owner's membership (with OIDC), the mail server switch and the hourly send limit, and then sent once with the Message-ID it was given when it was written, then saved to Sent;
- a `sending` message whose lease ran out (the replica stopped mid-send) becomes `unknown` and is never sent again by itself: it may have reached the server, so the person checks Sent;
- a message waiting for approval past its time goes back to a draft, as above.

Editing a scheduled message, adding or removing a file, or **Cancel schedule** (`POST /drafts/{id}/cancel-schedule`) turns it back into a draft by compare-and-set, so a message the scheduler has just claimed is never turned back (that answers 409: it is being sent). Picking another time with a new key asks for a new approval; the message waits for it and is never sent on the old one. Each scheduled send is audited (`mail.send.scheduled`, `mail.send.interrupted`, `mail.send.not_approved`) with ids and counts only.

Rolling back past the release that added scheduled send: cancel scheduled messages first (the previous release cannot read a `scheduled` message); the migration's downgrade turns any left into drafts.

### Turning it on (Oracle VM)

1. In Mailcow, open the mailbox (for example `admin@anum360os.com`) and create an **app password** allowed for IMAP and SMTP only.
2. On the VM, add to `infra/oracle/.env` (`nano ~/anum/infra/oracle/.env`):

   ```
   ANUM_MAILSERVER_ENABLED=true
   ANUM_MAILSERVER_IMAP_HOST=mail.anum360os.com
   ANUM_MAILSERVER_SMTP_HOST=mail.anum360os.com
   ANUM_MAILSERVER_ALLOWED_DOMAINS=anum360os.com
   ANUM_MAILSERVER_ALLOWED_TENANTS=<your tenant id>
   ```

3. `cd ~/anum/infra/oracle && docker compose up -d api` (only the API restarts).
4. In the web app open **Email**, choose **Connect mailbox**, and enter the address and the app password. The password goes only to the API.
5. Send a test to an outside address (Gmail or Outlook), reply to it, and check the message headers show SPF, DKIM and DMARC as passed and that the sent copy is in Sent.

The API container must reach `mail.anum360os.com` on 993 and 587 by its public name, so the certificate name matches. If it cannot (some clouds do not route a VM back to its own public address), the fix is an owner-approved change: a private Docker network shared by the API and Mailcow's Postfix and Dovecot, with `mail.anum360os.com` as their alias. Never add that network to Postfix's trusted networks.

### Brute force

ANUM limits its own login checks (above). The mail server's own protection is the operator's: Mailcow's netfilter container is off on the ANUM VM, so failed logins are not banned there. Options for the owner to review: fail2ban on the host reading Mailcow's Dovecot, Postfix and SOGo logs; Mailcow's netfilter after testing it with the VM's firewall, with ANUM's and Caddy's addresses whitelisted (ANUM signs in from one address, so a ban of it would stop every mailbox); or, at least, Dovecot's and Postfix's own rate limits, app passwords and 2FA for webmail.

### The Email page (web)

**Email** in the main sidebar group (`apps/web/src/EmailView.tsx`, `MailActions.tsx`, `email.css`, `mailCenter.css`; helpers `lib/mail.ts`, `lib/mailApi.ts`, `lib/mailActions.ts`, `lib/mailActionsApi.ts`, `lib/mailUnread.ts`; strings `lib/messages/mail.*.ts` and `mailCenter.*.ts`).

- **States.** Loading; viewers are told owners and members read mail; the Email Center off, the server not set up, or the organisation not allowed each name the operator setting to change; no mailbox yet shows the connect form (address, app password, optional name) to owners and members; a refused password or an offline server says so and how to fix it, with **Try again**.
- **Layout.** From 1280px three panes fill the window and scroll on their own: folders, the list, the message. From 1024px the folders become a bar above list and message; below 1024px one pane at a time, the folders a select, and the message has **Back to the list**.
- **Folders and unread counts.** Special folders first with their unread count (not Trash). While the page is visible and online it reads the folder list again every minute (doubling to at most five minutes after failures, at once when the tab comes back), shows when it last checked, and offers **Show new mail** when the open folder gained unread mail. The inbox's unread count is the badge on **Email** in the sidebar (the last count the page saw in this tab; nothing polls the mail server while the page is closed).
- **List, paging and search.** Newest first, 25 at a time (**Load more** uses `next_before`). Search runs on the mail server (`q`, IMAP SEARCH over sender, recipients, subject and text) in the open folder, with **Unread** and **Starred** filters; a search with no match says so and offers **Clear search and filters**.
- **Flags and moves.** Each row has a checkbox (Shift+click selects a range) and a star. The bar over the list selects every loaded message and offers read or unread, star or remove star, and the moves the folder allows: Inbox: Archive, Junk, Trash; Archive: Move to inbox, Junk, Trash; Junk: Not junk, Trash, Delete for good; Trash: Restore, Delete for good. Moves to a folder the mailbox lacks are hidden. Selections over 200 messages are sent in calls of 200. Counts and the sidebar badge change at once; a failed call says what failed and leaves the list as it was.
- **Delete for good** (Trash and Junk only) asks to confirm, then shows the approval the API created for exactly those messages with its payload hash and expiry. An owner approves it there (the hash goes back with the decision); members are told an owner decides and can open Approvals.
- **Reading.** The sanitized HTML is shown in an iframe with `sandbox="allow-popups allow-popups-to-escape-sandbox"` (no scripts, no same origin), `referrerpolicy="no-referrer"` and its own CSP `default-src 'none'; img-src data:` so nothing loads from the sender; links open in a new tab. **Show plain text** switches to the text part. Blocked remote images and hidden inline images are counted; attachments download through the API, too-large ones point to webmail.
- **Writing and sending.** Reply, Reply all, Forward and Write create a draft in ANUM; the composer checks the addresses, saves (`PATCH`), and **Send** asks for approval with one idempotency key per attempt (a retry reuses it). The approval shows the sender, recipients and payload hash; an owner approves in place, and the draft then shows Sent, Not sent with the reason, or **Check Sent** when delivery is unknown. "Written in ANUM" lists drafts, messages waiting for approval and sent ones.
- **Keyboard.** j and k (or the arrow keys) move through the list, Enter or o opens, x selects, e archives, # moves to Trash, s stars, u marks read or unread, / searches. Keys are ignored while typing and in dialogs.
- **Tests.** Unit `apps/web/test/mailActions.test.ts`; Playwright `apps/web/e2e/email-center.spec.ts` with the fake API in `e2e/support/mailApi.ts` (desktop and phone, axe in English, Arabic and the light theme).

### Not done yet (Email Center)

- Push (IMAP IDLE) and a server-side background sync: the page refreshes folder counts while it is open; the sidebar badge is not updated while the Email page is closed.
- Undo for a move: a moved message gets a new UID in its new folder, so the way back is the folder itself (Restore from Trash, Not junk, Move to inbox).
- Labels.
- Shared address books (contacts are personal), sharing a mailbox's identities per person, and a workspace wide send limit for shared mailboxes (the limit counts each writer).
- Attaching an existing workspace file (only files from the device are attached), and an inline preview of attached files.
- The AI assistant tools (summarise, classify, draft) for this mailbox; the Flutter screens.
- A screen for the operator view: it is the API above.
- Bcc recipients are not kept in the Sent copy.
