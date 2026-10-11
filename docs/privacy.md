# Privacy: download and delete my data

Every signed-in user can see what ANUM stores about them, download it and delete their private data, from **Settings → Privacy** in the web app (`#privacy`) and **Settings → Privacy** in the Flutter app. The deletion path inside the app is what Google Play and the App Store require of apps with accounts ([Store data safety](store-data-safety.md)). The owner's public policy is written from the [privacy policy template](privacy-policy.md).

All three actions act on the caller's own data in the **current tenant and workspace**. A user who belongs to several workspaces switches workspace and repeats the action; other users' data is never read or changed.

## API

| Route | What it does |
|---|---|
| `GET /api/v1/privacy` | Counts (never content) of the caller's private data and of the shared records they created, retention from the server's configuration, the model provider their messages go to, whether their chats are remembered (`model_provider.conversation_memory`, below), `privacy_policy_url`, the confirmation phrase and the export limit. |
| `GET /api/v1/privacy/export` | The caller's data as one JSON file (`Content-Disposition: attachment`, `Cache-Control: no-store`). |
| `POST /api/v1/privacy/delete` | Body `{"confirm": "DELETE MY DATA"}`. Deletes the caller's private data; returns `deleted` and `kept` counts. Any other phrase is `422` and nothing is deleted. |

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
| `user_preferences` | The caller's own preferences (`GET /api/v1/me/preferences`, such as "Remember things from my chats"), or `null` when never saved. One row per user and tenant, so it applies in every workspace of the organization. |
| `push_devices` | The phones the caller registered for push in this workspace: platform, label, the token's last four characters and dates. Never the push token ([Notifications](notifications.md#devices)). |
| `files_uploaded` | Metadata of files the caller uploaded (name, type, size, SHA-256, time) with a `download_path` (`/api/v1/files/{id}/content`). |
| `not_included` | Plain sentences listing what is not in the file (below). |

**Files are linked, not embedded.** File bytes can be up to 25 MiB each and are workspace records; embedding them would make the export unbounded. The signed-in user downloads each file from its `download_path` (the Files page does the same).

Not included: audit records (the workspace's security log, ids and counts only; owners and organization administrators read and export it, [Audit log](governance-and-scale.md#audit-log)), other users' data, data in other workspaces, and the sign-in account itself (name, email, password, two-factor), which lives in Keycloak.

**Size cap.** The export is built in memory, section by section, and refused with `413` once it passes `ANUM_PRIVACY_EXPORT_MAX_BYTES` (default 25 MiB, between 64 KiB and 512 MiB). It is never truncated: a partial export would look complete. The `413` message says how to fix it (delete old conversations, or the administrator raises the limit).

## What deletion removes and keeps

Removed, in one transaction:

- conversations and their turns;
- private memories and their retrieval index rows (`retrieval_sources` with `owner_user_id` = caller; chunks cascade);
- voice sessions and their transcript segments;
- notification preferences;
- the phones registered for push in this workspace (`push_devices`, migration `0027_push_devices`): they get no more pushes for it;
- the caller's `user_preferences` row (migration `0025_user_preferences`). It is per user and tenant, not per workspace, so deleting from any workspace resets it everywhere in that organization; the defaults apply again ("Remember things from my chats" back on).

Kept, and counted in the response and on the page:

- tasks, runs, approvals (and approval-chain decisions), shared memories and files the caller created: they belong to the workspace and other members rely on them. A workspace owner can delete them through their own pages;
- audit records: they are the workspace's security record and hold ids and counts, not content;
- the Keycloak account: ANUM does not delete identity accounts. The page says to ask the workspace owner or organization administrator, who removes the user in Keycloak and deactivates the membership.

## Isolation

With PostgreSQL every read and delete runs in one transaction whose RLS context carries the caller's tenant, workspace **and** `anum.user_id` (`anum_api.privacy.open_privacy_store`, `anum_api/db/privacy_repository.py`). The user-private tables (conversations, turns, voice sessions and segments, owned memories and their index rows, and `user_preferences`, whose forced RLS checks tenant and `anum.user_id`) admit only that user's rows under their policies; the shared tables admit only that workspace. Each statement also names the scope and the user explicitly; `notification_preferences` is scoped by tenant and workspace in RLS, so the explicit user filter is what limits it, and the store is only ever opened with the caller's own context. No maintenance role, no `BYPASSRLS`, no `SECURITY DEFINER`. `services/api/tests/test_postgres_privacy.py` proves, below RLS, that another user in the same workspace, the same user in another workspace and a user in another tenant keep every row (except the same user's tenant-wide `user_preferences` row, which is the one deleted), and that the same user id in another tenant keeps its preferences. It also exports and summarises the same user in two workspaces of one tenant: each export holds only that workspace's conversations, private memories, voice sessions and tasks, and both carry the one tenant-wide preference.

## Audit

Both actions write an audit record in the caller's workspace with the caller as actor and target, and counts only:

- `privacy.export`, outcome `success` with `format`, `bytes` and per-section counts, or `refused_too_large` with the limit;
- `privacy.delete`, outcome `success` with the `deleted` and `kept` counts.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `ANUM_PRIVACY_POLICY_URL` | empty | Public `http(s)` URL of the deployment's privacy policy, shown on the Privacy page. Empty: the page says the organization has not published one. |
| `ANUM_PRIVACY_EXPORT_MAX_BYTES` | `26214400` | Largest export the API builds. |
| `ANUM_CONVERSATION_RETENTION_DAYS` | `30` | Shown as the conversation retention ([Conversations](conversations.md#retention)). |

The API exposes `Content-Disposition` to browsers (CORS `expose_headers`) so the web app can name the downloaded file.

## Clients

- Web (`apps/web/src/PrivacyView.tsx`, helpers in `src/lib/privacy.ts`): loading, ready, `403` and error states with a retry; the download button reports the saved file and size or the reason it failed; the delete button stays disabled until the phrase is typed exactly and reports what was deleted and kept; the chat memory sentence follows `conversation_memory` ([above](#chat-memory-on-the-privacy-page)); its sentences and the switch button label are the `privacy.memory.*` keys of the English and Arabic catalogs (`src/lib/messages/en.ts`, `ar.ts`). Covered by `test/privacy.test.ts` and `e2e/privacy.spec.ts` (every memory state, the way to the switch, and the Arabic text right to left).
- Flutter (`apps/mobile/lib/features/privacy/`): the same sections; the export is saved with the system save dialog (`file_picker`), and "Read the privacy policy" opens the link in the browser with `url_launcher` (https only, like developer mode's links); a non-https link, or one no browser opens, is copied instead and a message says so. A copy button is always there. The chat memory sentence and the way back to the switch work as on the web. Covered by `test/privacy_test.dart` (every memory state, Settings → Privacy → switch, and widths 320 to 1440 at 100% and 200% text).

## Not done yet

- Exports are per workspace; there is no single export across every workspace of a user.
- Deleting the identity account is an administrator task in Keycloak; ANUM has no self-service account closure.
- File bytes are links in the export, not a zip.
