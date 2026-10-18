# Privacy: download and delete my data

Every signed-in user can see what ANUM stores about them, download it and delete their private data, from **Settings → Privacy** in the web app (`#privacy`) and **Settings → Privacy** in the Flutter app. The deletion path inside the app is what Google Play and the App Store require of apps with accounts ([Store data safety](store-data-safety.md)). The owner's public policy is written from the [privacy policy template](privacy-policy.md).

All three actions act on the caller's own data in the **current tenant and workspace**. A user who belongs to several workspaces switches workspace and repeats the action; other users' data is never read or changed.

## API

| Route | What it does |
|---|---|
| `GET /api/v1/privacy` | Counts (never content) of the caller's private data and of the shared records they created, retention from the server's configuration, the model provider their messages go to, whether their chats are remembered (`model_provider.conversation_memory`, below), `privacy_policy_url`, the confirmation phrase and the export limit. |
| `GET /api/v1/privacy/export` | The caller's data as one JSON file (`Content-Disposition: attachment`, `Cache-Control: no-store`). |
| `POST /api/v1/privacy/delete` | Body `{"confirm": "DELETE MY DATA"}`. Deletes the caller's private data; returns `deleted` and `kept` counts and `notes` (sentences about what still needs the caller, such as a mail provider where ANUM could not withdraw its access). Any other phrase is `422` and nothing is deleted. |

Every workspace role (viewer, member, owner) may use them for their own data: owners see the same page and have no extra power here. A caller without an active role in the workspace gets `403`; with OIDC a deactivated membership is refused before the route runs. The web and mobile pages explain a `403` ("ask a workspace owner to restore your membership").

## Chat memory on the Privacy page

`model_provider.conversation_memory` is the caller's effective chat memory state, computed by the same function as the `conversation_memory` of `GET /api/v1/me/preferences` (`user_preferences.conversation_memory_state`, [Memory](memory.md#from-conversations)): the caller's preference row is read in the summary's RLS unit of work and the tenant's memory governance in its own. An unreadable governance policy reads as `off_for_organization`, as extraction treats it. `model_provider.conversation_memory_enabled` is only the deployment-wide `ANUM_CONVERSATION_MEMORY_ENABLED` and is kept for older clients; it does not say whether this caller's chats are remembered.

The web and Flutter pages state what happens for each value:

| `conversation_memory` | Post-reply extraction call | Memories added to questions (recall) | The page says |
|---|---|---|---|
| `on` | Yes: a second short call to the same provider picks out lasting facts and saves them as private memories | Yes | What is extracted and that matching memories are sent with later questions. |
| `off_by_user` | No | No | The caller turned off "Remember things from my chats"; saved memories stay until deleted. A **Change Remember things from my chats** button opens the switch (web: Settings → Workspace with the switch focused; Flutter: back to Settings with the chat memory card in view). |
| `off_on_server` | No | No | Memory is off on this server (`ANUM_CONVERSATION_MEMORY_ENABLED`). |
| `off_for_organization` | No | Yes, for memories the caller can read | The organization's memory policy excludes the `conversation` source; an organization administrator can allow it under Governance. |
| `not_for_role` | No | Yes, for memories the caller can read | The caller's role cannot create memories; a workspace owner can change it. |

A value the client does not know (an older server without the field) gets a neutral sentence that makes no claim either way.

## What the export contains

Format `anum.privacy-export/v1`, sections in this order:

| Section | Content |
|---|---|
| `identity` | User, tenant and workspace ids, roles from the request, the persisted membership (role, active, dates). |
| `conversations` | Every conversation of the caller with all turns (role, content, status, time). |
| `memories.private` | Private memories (facts remembered from the caller's chats, `owner_user_id` = caller), with provenance and retention. |
| `memories.shared_created_by_you` | Workspace-shared task notes the caller added. |
| `voice_sessions` | Sessions with their transcript. A 30-day transcript past its expiry is empty, as in the voice API. |
| `tasks_created` | Tasks the caller created, each with its runs and steps. The run checkpoint (internal resume state) is left out. |
| `approvals_decided` | Approvals the caller approved or rejected, or approved as one of a chain (`your_approval`). Tool `arguments` are left out: they belong to the run and may carry redacted values. |
| `notification_preferences` | The caller's notification preferences in this workspace, or `null`. |
| `learning` | What ANUM learned from the caller in this workspace ([What ANUM learned](learning.md)): the style profile, every feedback signal with its full texts, learned preferences, routines, improvement notes and suggestions. |
| `commands` | The caller's ANUM Command history (each request with its confirmed steps, the task id of each step and its attachments) and saved quick commands ([ANUM Command](command.md)). |
| `library` | The caller's favorites, private knowledge collections (each with its items) and private reports ([Knowledge base](knowledge-base.md), [Reports](reports.md)). Shared collections and reports belong to the workspace and are left out. |
| `home` | Home ([Home and proactive insights](home.md)): the greeting name, the pinned apps in this workspace and which insights were dismissed or snoozed. |
| `user_preferences` | The caller's own preferences (`GET /api/v1/me/preferences`, such as "Remember things from my chats"), or `null` when never saved. One row per user and tenant, so it applies in every workspace of the organization. |
| `push_devices` | The phones the caller registered for push in this workspace: platform, label, the token's last four characters and dates. Never the push token ([Notifications](notifications.md#devices)). |
| `files_uploaded` | Metadata of files the caller uploaded (name, type, size, SHA-256, time) with a `download_path` (`/api/v1/files/{id}/content`). |
| `meetings_imported` | Meetings the caller imported ([Calls & Meetings](meetings.md#privacy)): notes, transcript lines and the recording's metadata with a `download_path`. |
| `meeting_connections` | The caller's connected Zoom and Microsoft accounts (provider, account, dates). The OAuth tokens are never exported. |
| `email.mailboxes_connected` | Every mailbox the caller connected ([Email](email.md)), private and workspace: provider, address, visibility, status, scopes and sync health. Never the OAuth tokens, the sync cursor or the token expiry. |
| `email.drafts_written` | Every reply draft the caller created, with its full text, recipients, status and provenance (`source`, model). |
| `email.private_mailbox_messages` | The message metadata synced from the caller's **private** mailboxes (sender, recipients, subject, snippet, labels, triage). Bodies are never stored by ANUM, so none are exported; messages of workspace mailboxes belong to the workspace and are not included. |
| `whatsapp_messages_sent` | WhatsApp messages the caller sent or asked to send ([WhatsApp](whatsapp.md)), with their text and status. Customer messages are the workspace's and are not included. |
| `autonomy_actions_requested` | Actions ANUM took on its own for the caller's requests ([Autonomy](autonomy.md)): tool, summary, target, level and undo state. Never the undo token or the tool's undo arguments. |
| `team_work` | The caller's team member profile, their in-app notifications, the work items they created or that are assigned to them, and the comments they wrote on those items ([Team work](team-work.md)). |
| `tyre_planning` | The tyre data connection the caller set up (without its secret) and the metadata of the tyre reports they created; report contents are workspace planning data and are left out ([Tyre planning](tyre-planning.md)). |
| `commerce_connectors_created` | The sales channels the caller connected (channel, name, marketplaces, health); never the channel credentials or sync cursors ([Commerce](commerce.md)). |
| `workspace_records_created` | The social accounts the caller connected (never their tokens) and the marketing campaigns they created ([Marketing](marketing.md)), the calendar events they created, and the automation triggers they added (never a webhook secret, [Automation](automation.md#triggers)). |
| `agents_owned` | The custom agents the caller owns, private and shared with the workspace, each with its memories (`agent_profiles`, `agent_memories`, migration `0041_agent_profiles`, [Agents](agents.md#privacy)). Agents hold no secrets. |
| `not_included` | Plain sentences listing what is not in the file (below). |

**Files are linked, not embedded.** File bytes can be up to 25 MiB each and are workspace records; embedding them would make the export unbounded. The signed-in user downloads each file from its `download_path` (the Files page does the same).

Not included: audit records (the workspace's security log, ids and counts only; owners and organization administrators read and export it, [Audit log](governance-and-scale.md#audit-log)), other users' data, data in other workspaces, and the sign-in account itself (name, email, password, two-factor), which lives in Keycloak.

**Size cap.** The export is built in memory, section by section, and refused with `413` once it passes `ANUM_PRIVACY_EXPORT_MAX_BYTES` (default 25 MiB, between 64 KiB and 512 MiB). It is never truncated: a partial export would look complete. The `413` message says how to fix it (delete old conversations, or the administrator raises the limit).

## What deletion removes and keeps

Removed, in one transaction (after the mailbox revocations below):

- conversations and their turns;
- private memories and their retrieval index rows (`retrieval_sources` with `owner_user_id` = caller; chunks cascade);
- voice sessions and their transcript segments;
- notification preferences;
- the phones registered for push in this workspace (`push_devices`, migration `0027_push_devices`): they get no more pushes for it;
- the caller's ANUM Command history and quick commands in this workspace: every row of `commands` and `quick_commands` (migration `0044_commands`; counted as `commands` and `quick_commands`). The tasks a command created are workspace records and stay;
- what ANUM learned from the caller in this workspace: every row of `learning_signals`, `learning_profiles`, `learning_preferences`, `learning_routines`, `learning_notes` and `learning_suggestions` (migration `0036_learning`; counted as `learning_signals` and `learned_items`). Automations created from an accepted routine are workspace records and stay;
- the meetings the caller imported, with their transcript lines and recording files (rows in the transaction, bytes after it commits), and the caller's Zoom and Teams connections (migration `0043_meetings`, [Calls & Meetings](meetings.md#privacy)); meetings other members imported are kept;
- the caller's favorites, private knowledge collections (with their items) and private reports (migration `0042_library`; counted as `favorites`, `private_collections` and `private_reports`). Notes in a private collection are private memories and go with them; shared collections and reports stay;
- the caller's whole finance ledger: settings, accounts, transactions, imports, bills, debts, goals and holdings (migration `0056_finance_ledger`, counted as `finance_records`, exported as the `finance` section), and from Finance stages 3 to 5 the safe-to-spend settings, every Guardian rule version and proposed change, the read-only connections (without their credentials, which are never exported, not even encrypted) and the recorded exchange rates (migration `0057_finance_plan_guard_conn`), and from Finance stages 6 to 9 the recorded prices, the SIMULATED paper and shadow portfolios and their orders, every execution request with its approval, the person's own execution settings (kill switch, live limits, opt-in, wider-live tier), wider-live proposals and incidents (migrations `0058_finance_paper_exec` and `0059_finance_wider_live`; the organisation's kill switch is not personal data and is not exported). Every finance row is private to its owner ([Finance](finance.md#privacy));
- the caller's Home data: pinned apps and insight states in this workspace and their tenant-wide greeting name (`home_pins`, `home_insight_states`, `home_profiles`, migration `0046_home`; counted as `home_items`);
- the caller's `user_preferences` row (migration `0025_user_preferences`). It is per user and tenant, not per workspace, so deleting from any workspace resets it everywhere in that organization; the defaults apply again ("Remember things from my chats" back on);
- the **private** mailboxes the caller connected, with their tokens, synced message metadata and every draft in them (counted as `email_mailboxes`, `email_messages`); before the transaction ANUM revokes each mailbox's grant at the provider where the provider has a revoke call (Google, Zoho), as disconnecting does. Where it cannot (Microsoft has no per-app revoke call; a provider error; an unreadable token) the deletion still happens and the response's `notes` tell the caller to remove ANUM in that account's security settings;
- the caller's email drafts that never left ANUM (status `draft`, `failed` or `discarded`, in any mailbox; counted with the drafts of their private mailboxes as `email_drafts`), and their pending mailbox connections (`email_oauth_requests`);
- the caller's Email Center data on the workspace mail server: the mailboxes they connected (never the password, not even encrypted, in the export), with those mailboxes' shares and sender identities, every message they wrote in ANUM (in mailboxes shared with them too), the shares they received and their address book (migrations `0060_email_center` and `0061_mail_sharing_contacts`; counted as `mail_mailboxes`, `mail_messages_written` and `mail_contacts`, exported as the `mail` section, [Email Center](email.md#email-center)). Mail on the server is not ANUM's copy and stays;
- the caller's in-app team notifications and their team member profile in this workspace (`user_notifications`, `team_member_profiles`, migration `0039_team_work`; counted as `team_notifications` and `team_profile`);
- the caller's **private** custom agents and their memories (sharing `private`; counted as `private_agents` and `private_agent_memories`, [Agents](agents.md#privacy)). Tasks those agents ran are workspace records and stay, without the agent.

Kept, and counted in the response and on the page:

- tasks, runs, approvals (and approval-chain decisions), shared memories and files the caller created: they belong to the workspace and other members rely on them. A workspace owner can delete them through their own pages;
- workspace mailboxes the caller connected (`email_mailboxes_connected`) and the caller's emails sent or waiting for approval from them (`email_drafts_sent`): they are the workspace's correspondence. An owner disconnects a mailbox under Inbox;
- WhatsApp messages the caller sent (`whatsapp_messages_sent`): they are part of the customer's conversation with the business, and follow the workspace's [WhatsApp retention](whatsapp.md#retention);
- actions ANUM took for the caller (`autonomy_actions_requested`): the workspace's activity record ([Autonomy](autonomy.md));
- work items the caller created or that are assigned to them (`work_items_created`, `work_items_assigned`), the tyre connection and reports they created (`tyre_connections_created`, `tyre_reports_created`) the sales channels they connected (`commerce_connectors_created`), the social accounts and campaigns (`marketing_accounts_connected`, `marketing_campaigns_created`), calendar events (`calendar_events_created`) and automation triggers (`automation_triggers_created`) they created: workspace records other members rely on;
- custom agents the caller owns and shared with the workspace, with their memories (`shared_agents_owned`, `shared_agent_memories`): other members use them, like a shared collection. The caller (or a workspace owner) deletes such an agent on its page;
- audit records: they are the workspace's security record and hold ids and counts, not content;
- the Keycloak account: ANUM does not delete identity accounts. The page says to ask the workspace owner or organization administrator, who removes the user in Keycloak and deactivates the membership.

## Contact suppression

The contact suppression list is the tenant's record of people who asked not to be messaged (pilot check CC04, owner decision). Code: `services/api/anum_api/suppression.py`, `db/suppression_repository.py`, table `contact_suppressions` (migration `0054_routing_suppression`, forced RLS on `anum.tenant_id`, no new grants).

- **What is stored.** A phone number or e-mail address, normalized (`00` and UAE `05x` numbers become `+971...`, e-mail lower case), is stored only as a per-tenant SHA-256 hash plus a masked form for people to recognise it (`+971•••4567`, `a•••@example.com`). Each entry has a channel (`email`, `whatsapp`, `sms` or `all`), a purpose (`marketing`: only marketing sends are refused; `all`: every business-initiated message), a source (`manual`, `whatsapp_keyword`, `email_unsubscribe`), a reason, who added it and when. One entry per contact and channel; adding a wider purpose widens it.
- **Keywords.** An inbound WhatsApp text that is exactly an opt-out keyword (`STOP`, `STOP ALL`, `UNSUBSCRIBE`, `OPT OUT`, `CANCEL`, `END`, `QUIT`, `إيقاف`, `توقف`, `إلغاء الاشتراك`, `لا ترسلوا لي رسائل` and close variants, case, diacritics and punctuation ignored) adds a WhatsApp entry for every purpose, before automations hear about the message. `START`, `UNSTOP`, `SUBSCRIBE`, `ابدأ` or `اشتراك` removes only entries a keyword added; an entry a person added stays until a person removes it. An inbound e-mail whose subject is exactly `unsubscribe`, `stop` or `إلغاء الاشتراك` (after `Re:`/`Fwd:`) adds an e-mail marketing entry. These changes are audited with the actor `system:inbound`.
- **Enforcement.** Every tool call that may deliver something to a person (any kind but read and draft) is checked in the runtime's tool mediation (`AgentRuntime.evaluate`: at planning, at execution, also after an approval, and on recovery) and again by the tool registry right before the handler runs (`ToolRegistry.execute`). A recipient on the list for the call's channel and purpose blocks the call with the reason ("+971•••4567 opted out of messages on whatsapp (date), so ANUM did not send this.") and writes a `suppression.send_refused` audit record. No autonomy level, approval or agent setting lowers it. A failure to read the list fails the call; nothing is sent on a guess. A pending approval lists such recipients (`suppressed`) so the approver sees the send will be refused.
- **API.** `GET /api/v1/contact-suppressions` (search by a full number or address, which is hashed, or by masked text, reason or source; filter by channel; `limit` up to 200 and `offset`), `POST /api/v1/contact-suppressions` (`recipient`, `channel`, `purpose`, `reason` of 3 to 300 characters; `201`, or `200` when already listed), `DELETE /api/v1/contact-suppressions/{id}` with a `reason`, and `GET /api/v1/contact-suppressions/export` (CSV of the masked list, cells guarded against spreadsheet formulas). Workspace owners (`suppression:manage`) and organization administrators may view, add and remove; only owners (`suppression:export`) export. Each change writes `suppression.added`, `suppression.removed` or `suppression.exported`.
- **Web.** The **Contact opt-outs** page (`apps/web/src/SuppressionView.tsx`, helpers `src/lib/suppression.ts`, messages `src/lib/messages/routing.{en,ar}.ts`) lists entries with paging and search, adds one with a channel, purpose and reason, removes one after a confirmation with a reason, exports for owners, and explains the empty, permission-denied and error states. Tests: `services/api/tests/test_suppression.py`, `test_setup_plan_pilot_checks.py` (CC04), `test_postgres_routing_suppression.py`, `apps/web/test/suppression.test.ts`, `apps/web/e2e/suppression.spec.ts`.

Not done yet: no Flutter screen for the list, no reply to the contact confirming the opt-out, and no consent records for campaigns.

## Isolation

With PostgreSQL every read and delete runs in one transaction whose RLS context carries the caller's tenant, workspace **and** `anum.user_id` (`anum_api.privacy.open_privacy_store`, `anum_api/db/privacy_repository.py`). The user-private tables (conversations, turns, voice sessions and segments, owned memories and their index rows, the six learning tables, and `user_preferences`, whose forced RLS checks tenant and `anum.user_id`) admit only that user's rows under their policies; the shared tables admit only that workspace. Each statement also names the scope and the user explicitly; `notification_preferences` is scoped by tenant and workspace in RLS, so the explicit user filter is what limits it, and the store is only ever opened with the caller's own context. No maintenance role, no `BYPASSRLS`, no `SECURITY DEFINER`. `services/api/tests/test_postgres_privacy.py` proves, below RLS, that another user in the same workspace, the same user in another workspace and a user in another tenant keep every row (except the same user's tenant-wide `user_preferences` row, which is the one deleted), and that the same user id in another tenant keeps its preferences. It also exports and summarises the same user in two workspaces of one tenant: each export holds only that workspace's conversations, private memories, voice sessions and tasks, and both carry the one tenant-wide preference.

## Audit

Both actions write an audit record in the caller's workspace with the caller as actor and target, and counts only:

- `privacy.export`, outcome `success` with `format`, `bytes` and per-section counts, or `refused_too_large` with the limit;
- `privacy.delete`, outcome `success` with the `deleted` and `kept` counts and `mailboxes_revoked_at_provider`.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `ANUM_PRIVACY_POLICY_URL` | empty | Public `http(s)` URL of the deployment's privacy policy, shown on the Privacy page. Empty: the page says the organization has not published one. The web host serves one at `https://<web host>/privacy` ([Public legal pages](legal-pages.md)). |
| `ANUM_PRIVACY_EXPORT_MAX_BYTES` | `26214400` | Largest export the API builds. |
| `ANUM_CONVERSATION_RETENTION_DAYS` | `30` | Shown as the conversation retention ([Conversations](conversations.md#retention)). |

## Retention of email and WhatsApp data

- **Email bodies are never stored.** `email_messages` holds metadata and triage only (no body, HTML or raw column; `test_email.py::test_message_bodies_are_never_stored` guards it); a body is fetched from the provider when a message is opened and never written to the database. Synced metadata stays while the mailbox is connected and is deleted with it.
- **WhatsApp messages** follow the workspace's retention ([WhatsApp](whatsapp.md#retention)): an owner chooses how long history is kept, or the deployment default `ANUM_WHATSAPP_MESSAGE_RETENTION_DAYS` applies (default `0`, kept until deleted). The daily retention job deletes older messages.
- **What ANUM learned**: feedback signals past `ANUM_LEARNING_SIGNAL_RETENTION_DAYS` (default 180) are deleted ([What ANUM learned](learning.md)).

The API exposes `Content-Disposition` to browsers (CORS `expose_headers`) so the web app can name the downloaded file.

## Clients

- Web (`apps/web/src/PrivacyView.tsx`, helpers in `src/lib/privacy.ts`): loading, ready, `403` and error states with a retry; the download button reports the saved file and size or the reason it failed; the delete button stays disabled until the phrase is typed exactly and reports what was deleted and kept; the chat memory sentence follows `conversation_memory` ([above](#chat-memory-on-the-privacy-page)); its sentences and the switch button label are the `privacy.memory.*` keys of the English and Arabic catalogs (`src/lib/messages/en.ts`, `ar.ts`). Covered by `test/privacy.test.ts` and `e2e/privacy.spec.ts` (every memory state, the way to the switch, and the Arabic text right to left).
- Flutter (`apps/mobile/lib/features/privacy/`): the same sections; the export is saved with the system save dialog (`file_picker`), and "Read the privacy policy" opens the link in the browser with `url_launcher` (https only, like developer mode's links); a non-https link, or one no browser opens, is copied instead and a message says so. A copy button is always there. The chat memory sentence and the way back to the switch work as on the web. Covered by `test/privacy_test.dart` (every memory state, Settings → Privacy → switch, and widths 320 to 1440 at 100% and 200% text).

## Not done yet

- Exports are per workspace; there is no single export across every workspace of a user.
- Deleting the identity account is an administrator task in Keycloak; ANUM has no self-service account closure.
- File bytes are links in the export, not a zip.
