# Mobile App Features

What the ANUM mobile app (Flutter, one codebase for Android and iOS, `apps/mobile`) does today, written for the owner. It was checked against the code in `apps/mobile/lib`, the tests in `apps/mobile/test` and the feature docs, not from memory. How the app is built, configured and released is in [Flutter mobile](mobile.md).

States used below:

- **Done**: works end to end against the real API, with loading, empty, error and permission-denied states, and has tests.
- **Partial**: works, but something a finished feature needs is missing; the gap is named.
- **Not built**: not in the app yet.
- **In progress**: being built on another branch, not in the app yet ([Coming next](#coming-next)).

## Where things are

On a phone the bottom bar has **Assistant**, **Home**, **Tasks**, **Resources** and **More**. More opens **Voice**, **Approvals**, **Automations**, **Developer** (owners only), **Policy packs**, **Marketplace**, **Advanced routing** and **Settings**. On a tablet or any screen 840 points wide or more, a side rail lists every destination and the top bar has Language, Organization, Settings and Refresh buttons.

## Summary

| Area | State | Main screens |
| --- | --- | --- |
| [Sign-in and accounts](#sign-in-and-accounts) | Done (company sign-in); Google and Apple sign-in in progress | Sign in to ANUM, Where do you want to work?, Switch workspace |
| [First run](#first-run) | Done | Set up your workspace, Connect a model |
| [Assistant chat](#assistant-chat) | Done | Assistant, Conversations |
| [Voice assistant](#voice-assistant) | Done (push-to-talk and wake word); live voice call in progress | Voice, Voice settings |
| [Memory](#memory) | Partial | Resources › Memory, Settings › Chat memory |
| [Tasks](#tasks) | Done | Home, Tasks, task detail |
| [Approvals](#approvals) | Done | Approvals, Home › Waiting for your decision |
| [Automations](#automations) | Partial | Automations, run sheet, New workflow, Add schedule |
| [Files](#files) | Done | Resources › Files, file details |
| [Sources used](#sources-used) | Done | Task detail › Sources used |
| [Governance and organization](#governance-and-organization) | Partial | Organization (Policy packs, Marketplace, Advanced routing) |
| [Budgets](#budgets) | Done | Settings › Model budgets |
| [Members, invitations and audit](#members-invitations-and-audit) | Done (creating a workspace is web only) | Members and invitations, Accept an invitation, Audit log, Approval policy, This workspace, Contact opt-outs, Setup plans, Org chart |
| [Developer mode](#developer-mode) | Done | Developer |
| [Privacy](#privacy) | Done | Settings › Your data and privacy |
| [Notifications and push](#notifications-and-push) | Done in the app; needs the owner's Firebase setup to deliver | Settings › Notifications, Phone push card |
| [Settings, language and right to left](#settings-language-and-right-to-left) | Partial | Settings, Language menu |
| [Integrations and skills](#integrations-and-skills) | Partial (connectors in progress) | Resources › Skills, Resources › Services, Home › Connected services |

## Sign-in and accounts

| What you can do | State |
| --- | --- |
| Sign in with the company account (Keycloak) in the system browser, stay signed in, refresh silently, sign out (also ends the browser session) | Done |
| Open the workspace from the account, pick one when you belong to several ("Where do you want to work?"), remember the choice per user | Done |
| Switch workspace from Settings; see your user, organization, workspace, roles and when the session expires | Done |
| Password, one-time code and password recovery screens | Development only: shown only when no company sign-in is configured; a store build never shows them |
| Sign in with Google or Apple | In progress |
| Two-factor sign-in (authenticator app, recovery code, passkey) | Done, in the company sign-in page when the Keycloak has it turned on |
| Rename, archive, restore or create a workspace | Web only |
| Close your own account | Not built (an administrator does it in Keycloak) |

Sessions and tokens are kept in the phone's encrypted storage and are excluded from Android backups. Details: [Identity and sign-in](identity.md#flutter).

## First run

| What you can do | State |
| --- | --- |
| A new user with no organization creates one and becomes its first owner ("Set up your workspace") | Done |
| An owner connects a model ("Connect a model"): Anthropic (Claude), OpenAI compatible, Ollama or the test mock. The key is tested before it is saved and is never shown again | Done |
| Skip the model step when the server or the workspace already has a model | Done |
| A member who is not an owner is told an owner must connect the model, with "Check again" | Done |
| A guided tour of the app after setup | Not built |

Details: [Flutter mobile, First run](mobile.md#first-run).

## Assistant chat

| What you can do | State |
| --- | --- |
| Chat with the assistant; the reply streams in as it is written | Done |
| Keep several conversations, start a new one, reopen or delete one (with confirmation) | Done |
| Stop a reply, retry a failed or stopped one, starter questions, Jump to latest | Done |
| Clear messages when the budget is used up (with Open Settings) or when your role cannot chat | Done |
| Rename or search conversations | Not built |
| Attach a file or photo to a message | Not built |
| Favorites, knowledge base and reports from chats | In progress |

Screen: **Assistant** (the conversation list sits beside the chat on tablets and opens from **Conversations** on phones). Details: [Conversations](conversations.md#mobile-flutter).

## Voice assistant

| What you can do | State |
| --- | --- |
| Name the assistant ("Anum" by default) and wake it by saying the name; or tap the orb to talk | Done |
| Ask a question by voice and hear the answer read out sentence by sentence as it streams | Done |
| Give a command: the app shows the transcript, lets you edit it, and creates a task only after you confirm | Done |
| English (US) or Arabic (Saudi Arabia) recognition and speech; mute; choose how long transcripts are kept | Done |
| Microphone permission recovery (opens the phone's settings) and a keyboard fallback | Done |
| Risky actions always go to Approvals; a spoken "yes" never approves | Done |
| A continuous, hands-free live voice call | In progress |
| Meeting recording and summaries | In progress |

Screens: **Voice**, **Voice settings**, Review command. Still needs testing on real phones with headsets and interruptions ([real-device checklist](mobile.md#real-device-test-checklist)). Details: [Voice](voice.md).

## Memory

| What you can do | State |
| --- | --- |
| See what the workspace's agents remember, add a memory (optionally tied to a task), delete one (with confirmation) | Done |
| Open a memory from a task's Sources used, scrolled to and marked "Used by the run you came from" | Done |
| Turn "Remember things from my chats" on or off; see why it is off when the server, organization or role turned it off; delete memories from your chats | Done |
| Edit a memory's text | Not built (delete and add again) |
| Search and paging through many memories | Not built: the app shows the first page the API returns |
| Organization memory rules (retention, who may write) | Web only |

Details: [Memory](memory.md).

## Tasks

| What you can do | State |
| --- | --- |
| Start a task from Home ("Run task") or from a voice command | Done |
| See counts (Needs you, Running, Done today, Failed) and recent work on Home | Done |
| List tasks with server-side status filters and counts (All, Active, Needs approval, Completed, Failed or cancelled), search, an Archived filter and "Load more tasks" | Done |
| Open a task: status, what the agent produced (copy), every step it took, the approval it is waiting on | Done |
| Cancel a task (with confirmation), resume it, retry a failed or cancelled one, and switch between its runs | Done |
| Rename a task; archive and restore it | Done |
| "Copy run log": the run's full record as JSON on the clipboard | Done |
| Open a task from a link (`/tasks/<id>`) or a push notification | Done |
| Date-range filter and selecting several tasks at once | Web only |
| Live updates without pulling to refresh | Not built: the screen refreshes after your own actions and on Refresh |

Details: [Tasks](tasks.md#flutter-tasks-screens).

## Approvals

| What you can do | State |
| --- | --- |
| See what is waiting, with the tool, its risk and what it will do; counts of waiting, approved, rejected | Done |
| Approve (with a second confirmation) or reject, with an optional reason kept in the audit trail | Done |
| See approval chains ("1 of 3 approvals", who approved) | Done |
| See the server's reason when you may not approve (for example the two-person rule) | Done |
| A badge on Approvals with the number waiting | Done |
| Approve right from the notification (tapping it opens the queue), or with the phone's fingerprint or face check | Not built |

Details: [Approvals and risk](approvals-and-risk.md).

## Automations

| What you can do | State |
| --- | --- |
| List workflows; create one (name, description, first action); run one now | Done |
| See recent runs and open one; resume, retry or cancel a run | Done |
| Add a schedule (workflow, name, cron expression, time zone) and list schedules | Done |
| Edit or delete a workflow; pause, edit or delete a schedule | Not built on mobile |
| Build multi-step workflows | Not built on mobile (one first action only) |

Screen: **Automations** with Workflows, Running now, Runs and Schedules. Details: [Automation](automation.md).

## Files

| What you can do | State |
| --- | --- |
| See how much of the workspace's storage is used | Done |
| Upload one or several files with the phone's picker, with progress and Cancel for each upload | Done |
| Search files by name, sort them, "Load more files" | Done |
| Open a file: index state, a preview for text and images, Download (save dialog), Rename (keeps the extension), Re-index, Delete (with confirmation); the buttons follow your role | Done |
| Text files are indexed so tasks can use them as sources | Done |
| Preview PDFs, Office files and archives inside the app | Not built: they are downloaded and opened in another app |
| File versions | Not built: an upload with the same name is a new file |

Screen: **Resources › Files**. Details: [Workspace files](files.md#mobile).

## Sources used

| What you can do | State |
| --- | --- |
| On a task's run, see which memories and files the agent used, with the passage number and match score | Done |
| Tap a file to open its details, or a memory to open it in Memory | Done |
| Clear messages when nothing matched, when retrieval was skipped or unavailable, and when you lack access | Done |

This panel is fully translated into Arabic. Details: [Memory, At run time](memory.md#at-run-time).

## Governance and organization

| What you can do | State |
| --- | --- |
| See policy packs, add the baseline policy, turn a pack on or off | Done for organization administrators; read-only for others with a notice saying who can change it |
| See counts of role templates, approval rules and active regions | Done (read-only) |
| Create or edit role templates and approval rules | Web only |
| Marketplace: install or uninstall published packages | Done (workspace owners) |
| Publish skill versions or marketplace packages | Web only |
| Advanced routing: see where AI requests go and mark a target healthy, degraded or offline | Done for administrators; read-only for others |
| Export the whole audit log as CSV or JSON | Done |

Screen: **Organization** (also reached as Policy packs, Marketplace and Advanced routing from More). Details: [Governance and scale](governance-and-scale.md#who-may-change-what).

## Budgets

| What you can do | State |
| --- | --- |
| See this month's model spending and tokens against the limit, for the workspace and the organization | Done |
| Set or remove monthly cost (USD) and token limits | Done (workspace owners for the workspace; organization administrators for the organization; others read-only) |
| When a budget is used up, tasks and chat say so and point to Settings | Done |
| Paying for the service inside the app | Not built (needs a billing decision, see [Needs the owner](#needs-the-owner)) |

Screen: **Settings › Model budgets**. Details: [Model gateway](model-gateway.md#monthly-budgets).

## Members, invitations and audit

| What you can do | State |
| --- | --- |
| List members with counts (active, owners, deactivated, pending invitations) and search them by user id or email | Done (owners) |
| Change a member's role or deactivate and reactivate them, each after a confirmation that says what the new role may do | Done (owners) |
| The only active owner cannot be demoted or deactivated; the reason is shown next to the control | Done |
| Invite by email with a role and expiry; copy the one-time token (and the web link when configured); resend (new token) or revoke an invitation | Done (owners) |
| Accept an invitation by pasting a token or link | Done |
| Audit log: search, filter by action and date range, "Load older records", a detail sheet per record | Done, read-only (owners and organization administrators) |
| Export a filtered part of the audit log | Web only (the Organization screen exports the whole log) |
| Set the workspace approval policy (two-person rule, approval for medium risk) | Done (owners; members read-only) |
| The invitation is emailed automatically | Not built: the owner sends the token or link |
| This workspace: see its name, id, organization, status and your role; rename, archive (type the name to confirm) and restore | Done (owners and organization administrators; others read-only). Creating another workspace is web only |
| Contact opt-outs: list, search by number, address or text, filter by channel, add with a reason, remove with a reason | Done (owners and organization administrators). CSV export is web only |
| Setup plans: list the plans and preview one (pilot or whole plan, counts, budget, rollout, every item with search, section filter and paging, gaps) | Done, read-only (owners and organization administrators). Applying and pilot checks are web only |
| Org chart: departments with agents, assistants and people, statuses, search and paging | Done, read-only. Profiles and exports are web only |

Screens: **Settings › Members and invitations**, **Accept an invitation**, **Audit log**, **Approval policy**, **This workspace**, **Contact opt-outs**, **Setup plans**, **Org chart** (`apps/mobile/lib/features/admin/`, English and Arabic, tested in `test/admin/org_admin_test.dart`). Non-owners get the server's explanation of who can do it. Details: [Identity](identity.md#client-screens), [Audit log](governance-and-scale.md#audit-log).

## Developer mode

Owners only; everyone else does not see it in the navigation and gets an explanation if they open it by link.

| What you can do | State |
| --- | --- |
| Ask for a change to ANUM in plain words (typed, in the Assistant, or by voice), with a confirmation before it starts | Done |
| Follow requests (search, paging), check now, cancel, open the pull request and the coding run in the browser | Done |
| Approve and merge a ready pull request with a tap (voice never approves) | Done |
| Setup checklist naming any missing settings | Done |

Screen: **Developer**. Details: [Developer mode](developer-mode.md#clients).

## Privacy

| What you can do | State |
| --- | --- |
| See what is stored about you (private to you, shared with the workspace) | Done |
| Download your data (saved with the system save dialog) | Done |
| Delete your private data after typing the confirmation phrase (the store-required in-app deletion path) | Done |
| Read the privacy policy (opens in the browser) | Done, once the owner publishes the policy |
| Export across every workspace at once, or close the account | Not built |

Screen: **Settings › Your data and privacy**. Details: [Privacy](privacy.md).

## Notifications and push

| What you can do | State |
| --- | --- |
| Choose what you hear about in this workspace: approvals waiting, task completed, run failed, automation failed; phone push on or off; email and desktop switches | Done (saved to your account) |
| Turn on phone notifications from Settings (never asked on first launch; offered once after your first approval decision); "Open phone settings" if they are blocked | Done |
| See the phones registered in this workspace, when each was last notified, and remove one | Done |
| Get a push when an approval waits for you, a task finishes or fails, or a developer pull request is ready; tapping it opens the approval queue, the task or Developer, also from a closed app | Done in the app; delivered only once the owner sets up Firebase (see [Needs the owner](#needs-the-owner)) |
| A push that arrives while the app is open shows as an in-app notice with Open | Done |
| Push for failed automations | Not built (automation runs emit no event yet) |
| Email and desktop notifications | Not built: the switches are saved, nothing is sent yet |
| Push in store builds from CI | Not built: the release workflow does not yet write the Firebase files, so CI builds have push off |

Screen: **Settings › Notifications** with the **Phone push** card. Without the Firebase files the app still runs and the card says push is not set up. Details: [Notifications](notifications.md), [Phone push](mobile.md#phone-push).

## Settings, language and right to left

| What you can do | State |
| --- | --- |
| See your account and session; switch workspace; sign out (with confirmation) | Done |
| Model connection: see the connected model (and routed quick-reply and planning models for Claude), change it, test it | Done (owners) |
| Light and dark theme following the phone | Done |
| Arabic: the app follows the phone's language; layout mirrors right to left | Done |
| Choose English or Arabic inside the app | Partial: the Language menu is on tablets and wide screens only; on phones the app follows the phone's language |
| Arabic text on every screen | Partial: navigation, Voice and Sources used are translated; most other screens (Settings, Tasks, Approvals, Automations, Resources, Members, Privacy, Assistant) still show English text in the mirrored layout |
| Large text (200 percent) and screen readers | Done for the tested screens; still to be checked on real devices |

## Integrations and skills

| What you can do | State |
| --- | --- |
| Skills: see published skills with their risk and installed version; install, turn on or off, uninstall (asks first) | Done (owners; others are told only owners can) |
| Services: each connected service's status, how to fix it and its last error (for example web search) | Done (read-only) |
| Skill details, rollback, publishing; testing a service, turning it off or changing its settings, integration tokens | Web only |
| Connect email (Gmail, Outlook, Zoho), WhatsApp Business, Amazon, Noon or the owner's own apps | In progress |

Details: [Skills](skills.md), [Tools and integrations](tools-and-integrations.md#workspace-configuration).

## Commerce

Amazon, noon and the owner's store ([Commerce](commerce.md)). Anyone with a role in the workspace reads; `commerce:operate` (owners and members) and `commerce:manage` (owners) decide what else shows, and the server checks both again.

| What you can do | State |
| --- | --- |
| Sales summary for a period, by marketplace, top products, low stock, the order list with filters, paging and order detail | Done |
| Catalogue: search and page, open a product with its listings per channel | Done |
| Catalogue: add a product by SKU, edit its titles, brand, cost (with a currency) and low-stock level, delete it (the server refuses while a channel lists the SKU) | Done (owners and members) |
| Prices and stock: search listings, propose a new price or quantity; nothing reaches the channel until it is approved in Approvals (a link opens them) | Done (owners and members) |
| Returns for the last 7, 30 or 90 days, paged | Done |
| Alerts: Open, Acknowledged and Resolved lists; acknowledge an open alert | Done (acknowledge: owners and members) |
| Channels: health, last sync and last error; sync now | Done (sync: owners and members) |
| Channels: test the connection, turn off or on, remove (with confirmation) | Done (owners) |
| Stock changes on listings fulfilled by Amazon or noon are not offered (the channel owns that stock) | Done |
| Connect a channel, replace credentials, alert rules, buyer details, tyre attributes of a product (size, load and speed index, DOT, season; the size in a title is read when a product is created) | Web only |

Screen: **Home › Sales and orders** with the tabs Sales, Catalogue, Prices and stock, Returns, Alerts and Channels (`apps/mobile/lib/features/commerce/`, English and Arabic; tests in `test/commerce_test.dart` and `test/commerce_ops_test.dart` cover every list state, the role differences, validation, the server's refusals and 320 and 390 point widths).

## Coming next

Work on other branches. None of it is in the app yet; each item is marked done here only after it merges with tests.

| Item | What it will do | State |
| --- | --- | --- |
| Email inbox | Read and answer Gmail, Outlook and Zoho Mail through the assistant; every sent email goes through approvals | In progress |
| WhatsApp | Connect WhatsApp Business through the official API; read chats and send replies after approval | In progress |
| Tyre planning | Work with the owner's tyre planning data (TyrePulse, Supabase and Firebase) from the assistant | In progress |
| Marketing | Draft and schedule marketing content, published only after approval | In progress |
| Autonomy | Let the assistant carry out longer jobs on its own within the approval rules | In progress |
| Learning | The assistant learns the owner's preferences and ways of working over time | In progress |
| Team work and customer organisations | Work together in teams and manage separate customer organisations | In progress |
| Custom agents | Create agents with their own instructions, tools and limits | In progress |
| Google and Apple sign-in | Sign in with a Google or Apple account | In progress |
| Favorites, knowledge base and reports | Save answers, build a knowledge base, produce reports | In progress |
| Meeting summaries | Record or import a meeting and get a summary with actions | In progress |
| Live voice call | A continuous, hands-free conversation with the assistant | In progress |
| ANUM 360 redesign | The new look from the owner's mockups across the app | In progress |

## Needs the owner

Things only the owner can provide. Values go into GitHub secrets or the server's settings, never into the code or a chat.

| Item | Why | Where |
| --- | --- | --- |
| Android upload key and Play service account | Signed builds and uploads to Google Play | [Owner checklist](mobile.md#owner-checklist-windows) |
| App Store Connect API key and Apple team ID | Signed iPhone builds and TestFlight uploads | [Owner checklist](mobile.md#owner-checklist-windows) |
| Production API and sign-in addresses | Store builds must point at the live server | [Release pipeline](mobile.md#release-pipeline) |
| Published privacy policy, store listings, Data safety and App Privacy forms | Required before leaving internal testing | [Store data safety](store-data-safety.md), [Privacy policy template](privacy-policy.md) |
| Firebase project (`google-services.json`, `GoogleService-Info.plist`), an Apple push key (APNs `.p8`) and the Push Notifications capability in Xcode, the server's `ANUM_FCM_SERVICE_ACCOUNT_JSON`, and CI secrets for the two files | Push notifications on Android and iPhone (the app side is built) | [Owner steps: Firebase project](mobile.md#owner-steps-firebase-project) |
| Google and Apple sign-in apps (OAuth client ids, Sign in with Apple service id and key) | Google and Apple sign-in | Keycloak identity providers |
| OAuth apps for Gmail, Microsoft (Outlook), Zoho Mail; WhatsApp Business (Meta) app; Amazon Selling Partner and Noon seller credentials | The email, WhatsApp and commerce connectors | Stored encrypted on the server once each connector merges |
| Billing decisions | Whether and how users pay (store in-app purchase or outside the app), plans and limits, and the default monthly model budgets | Owner decision; nothing is built until it is made |
