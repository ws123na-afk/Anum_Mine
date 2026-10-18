# Notifications

ANUM tells people about the things that wait for them: an approval only they can decide, a task they started that finished or failed, and a developer-mode pull request that is ready to merge. The approvals queue, the task list and the Developer screen are the record; notifications are reminders that bring a person back to them.

## What each person controls

Notification preferences are per user and per workspace (`GET`/`PUT /api/v1/notification-preferences`, table `notification_preferences`, RLS on tenant and workspace):

| Switch | Meaning | Phone push sends |
|---|---|---|
| `approval_required` | Approvals waiting for me | `approval.requested` to the members who may decide it, `developer_request.ready_for_review` to the request's owner |
| `task_completed` | Tasks finished | `agent_run.completed` to the task's creator |
| `run_failed` | Tasks failed | `agent_run.failed` to the task's creator, `developer_request.failed` to the request's owner |
| `commerce_alerts` | Store alerts (migration `0038_commerce`, default on; older clients that do not send it leave it on) | `commerce.alert.raised` to the workspace's owners and members ([Commerce](commerce.md#alerts)) |
| `tyre_alerts` | Tyre alerts (migration `0037_tyre_planning`, default on) | `tyre_alerts.raised` to every active member who may read tyre planning ([Tyre planning](tyre-planning.md#alerts)) |
| `automation_failed` | Automations failed | `automation_run.failed` to the people its payload names (`notify_user_ids`: the run's owner and the automation's creator, while active members with `automation:read`) |
| `push_enabled` | Phone push for this workspace (migration `0027_push_devices`, default on; older clients that do not send it leave it on) | The channel switch for all of the above; also gates a `notify` step that asked for push (`automation.notification` with `push: true`, to the run's owner) |
| `desktop_enabled` | Desktop notifications | Not a phone channel |
| `email_enabled` | Email | Team work notifications are emailed when SMTP is configured ([Team work](team-work.md#notifications)); nothing else is emailed yet |

In-app calls ([In-app calls](in-app-calls.md#phone-push)) push `incoming_call` (from `app_call.ringing`) and `missed_call` (from a `missed_call` notification) to the person rung; they have no switch of their own and follow `push_enabled`.

The web app shows these under **Settings › Notifications** with the person's registered phones; the Flutter app under **Settings › Notifications** with the **Phone push** card.

## Phone push

### Server

Push goes through Firebase Cloud Messaging HTTP v1 for both Android and iOS (iOS through the APNs key uploaded to the Firebase project). The API needs the Firebase project's service account key:

| Setting | Meaning |
|---|---|
| `ANUM_FCM_SERVICE_ACCOUNT_JSON` | The whole JSON key file of a service account with the *Firebase Cloud Messaging API Admin* role (one line). It carries a private key: keep it in the deployment's secret store (Helm `secrets.app` Secret, Render dashboard, Oracle `.env`). Empty or unset: push is off. A value that is not a service-account key (wrong `type`, missing `project_id`, `client_email` or `private_key`, unreadable PEM, non-https `token_uri`) is refused at startup; the error names the setting and the problem, never the key. The Helm chart refuses the setting in `config` or `extraEnv` (it would land in a ConfigMap). |
| `ANUM_PUSH_MAX_RECIPIENTS` (50), `ANUM_PUSH_MAX_DEVICES_PER_USER` (10) | Fan-out bounds per event. |
| `ANUM_PUSH_MAX_ATTEMPTS` (4), `ANUM_PUSH_TIMEOUT_SECONDS` (10) | Attempts per message (exponential backoff with full jitter from 1 s, capped at 30 s; FCM's `Retry-After` is honoured up to 60 s) and the HTTP timeout. |
| `ANUM_PUSH_MAX_PENDING` (1000) | Events waiting for delivery in one process; beyond it the oldest are dropped and logged. |
| `ANUM_DEVELOPER_WATCH_SECONDS` (120) | How often in-progress developer requests are refreshed from GitHub so "ready for review" reaches a closed app; `0` turns the watcher off. Runs only with push on and developer mode configured. |

`GET /api/v1/push/status` (any member) answers `{"available", "provider": "fcm", "project_id", "message"}`; clients use it to say "Phone push is off on this server" instead of asking for notification permission.

### Devices

| Route | What it does |
|---|---|
| `POST /api/v1/me/devices` `{"platform": "android"\|"ios", "token", "label"?}` | Registers the caller's phone in the current workspace. The same token again updates its row (200 instead of 201). Beyond `ANUM_PUSH_MAX_DEVICES_PER_USER` the least recently registered phone is removed. 409 while push is off on the server. |
| `GET /api/v1/me/devices` | The caller's own phones in this workspace: id, platform, label, the token's last four characters, created/updated and last-notified times. |
| `DELETE /api/v1/me/devices/{id}` | Removes one of the caller's phones (404 for anyone else's). |

There is no user id in any path or body. Rows live in `push_devices` (migration `0027_push_devices`) under forced RLS on `anum.tenant_id`, `anum.workspace_id` **and** `anum.user_id`: another user's phone is invisible even to a query without a predicate. The token is encrypted with `ANUM_SECRETS_KEY` (Fernet; every configured key decrypts, and each registration of the same phone re-encrypts it with the first key, which the app does on every start) and stored with a SHA-256 digest for de-duplication; responses, logs and audit records carry the last four characters at most. Registering and removing are audited as `push.device.registered` and `push.device.removed` with the platform only. "Download my data" lists the phones (never tokens) and "Delete my data" removes them ([Privacy](privacy.md)).

### Delivery

1. A unit of work commits domain events. `EventBusRuntime.after_commit` (the same hand-off that feeds the outbox, [Events](events.md)) passes them to `PushDispatcher.submit`, in the API and in the Temporal worker. Nothing is queued while push is off.
2. The dispatcher maps the event to a kind (table above) and, in a background task, resolves recipients as the application role inside the event's tenant and workspace (RLS applies):
   - approvals: active members whose role grants `approval:decide` (owners), only while the approval is still pending, and without the task's creator and the requester when the workspace's two-person rule covers the approval's risk;
   - task results: the task's creator, while an active member with `task:read`;
   - developer requests: the request's creator, while an active member with `developer:manage`;
   - automations (`automation.notification` with `push: true`, `automation_run.failed`): the users the event names, while active members with `automation:read`.
3. For each recipient it reads their notification preferences and their phones **inside that recipient's own RLS context**, decrypts the tokens and sends one FCM message per phone (ten in flight at most). A phone shared by two members gets the message once.
4. FCM `UNREGISTERED`, `INVALID_ARGUMENT` or `SENDER_ID_MISMATCH` removes the phone; a bare 403/404 does not (a wrong project must not wipe every phone). 429 and 5xx are retried; a 401 refreshes the OAuth token. Successful sends set `last_sent_at`.

Team work adds the kind `team_work`: every in-app team notification (assignment, comment, completion, blocked, overdue reminder, ANUM follow-up) emits `notification.created` naming its one recipient, who gets a push while an active member with `work:read` and with `push_enabled` on (it has no switch of its own). Text: "Team work" / "Something in your team work needs you. Open ANUM to see it."; data: `work_item_id`. See [Team work](team-work.md#notifications).

The payload never carries content: a fixed title and sentence ("Approval needed" / "An action is waiting for your decision.", "Task finished", "Task failed", "Change ready for review", "Developer request stopped", "Tyre alerts", "Store alert", "Automation update", "Automation failed") and string ids for the deep link: `anum_kind`, `tenant_id`, `workspace_id`, and `approval_id`, `task_id`, `run_id`, `developer_request_id`, `commerce_alert_id` or `automation_run_id` and `workflow_id` (the app opens Automations). A notify step's own message never travels in the push. Android groups by `tag` and iOS by `apns-collapse-id` (the approval, request or task id), so a later push about the same item replaces the earlier one.

The queue is in-process and bounded. A push is lost if the process stops before sending it (a restart, a scale-down); the item itself is unaffected and every client lists it. Two API replicas that refresh the same developer request at the same instant can each announce the change once.

### Developer mode

Developer requests learn their state from GitHub when someone looks at them. With push on, each API process also refreshes the configured tenant's requests that are still in progress every `ANUM_DEVELOPER_WATCH_SECONDS`. It discovers them as `anum_maintenance` (migration `0027_push_devices` grants four columns of unfinished `developer_requests` rows only) and refreshes each as the application role in its workspace, like the other [maintenance jobs](multi-tenancy.md#maintenance-role). The API login needs `anum_maintenance` (granted `WITH INHERIT FALSE, SET TRUE` by `infra/helm/bootstrap-database.sql`). A refresh that moves a request to `waiting_approval` records `developer_request.ready_for_review`; to `ci_failed` or `failed`, `developer_request.failed`.

### Phones

The Flutter app ([Flutter mobile](mobile.md#phone-push)):

- never asks for notification permission on first launch. It asks when the person taps **Turn on notifications** in Settings › Notifications, or accepts the one-time offer shown after their first approval decision;
- registers the token after sign-in when the phone already allows notifications, and again when Firebase rotates the token;
- before sign-out and before a workspace switch, removes its registration (while the session still works), and after sign-out deletes the Firebase token so the next person on the phone gets a new one; after a switch it registers in the new workspace;
- opens the approval queue, the task or the Developer screen when a push is tapped (from the background or a cold start), and shows a push that arrives while the app is open as an in-app notice with **Open**. A push for another workspace says so instead of opening it;
- builds and runs without the owner's Firebase files: the Phone push card then says push is not set up in this build. CI release builds get the files from the `ANUM_ANDROID_GOOGLE_SERVICES_JSON` and `ANUM_IOS_GOOGLE_SERVICE_INFO_PLIST` repository secrets (the file contents, not a copied file; [Owner steps: Firebase project](mobile.md#owner-steps-firebase-project)).

## Not done yet

- Email delivery and desktop notifications for these events.
- `python -m anum_api.rotate_secrets` does not re-encrypt push tokens; phones re-encrypt their own token when the app starts. Keep a retired key in `ANUM_SECRETS_KEY` until every phone has opened the app, or accept that a phone that never does stops getting pushes until it does (its token cannot be decrypted, it is skipped and logged by device id).
- iOS push needs the Push Notifications capability on the App ID and in Xcode; it is an owner step ([Flutter mobile](mobile.md#phone-push)), not committed, so CI's unsigned iOS build stays unchanged.
