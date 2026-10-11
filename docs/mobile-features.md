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
| [Tasks](#tasks) | Partial | Home, Tasks, task detail |
| [Approvals](#approvals) | Done | Approvals, Home › Waiting for your decision |
| [Automations](#automations) | Partial | Automations, run sheet, New workflow, Add schedule |
| [Files](#files) | Partial | Resources › Files, file details |
| [Sources used](#sources-used) | Done | Task detail › Sources used |
| [Governance and organization](#governance-and-organization) | Partial | Organization (Policy packs, Marketplace, Advanced routing) |
| [Budgets](#budgets) | Done | Settings › Model budgets |
| [Members, invitations and audit](#members-invitations-and-audit) | Partial | Members and invitations, Accept an invitation, Approval policy |
| [Developer mode](#developer-mode) | Done | Developer |
| [Privacy](#privacy) | Done | Settings › Your data and privacy |
| [Notifications and push](#notifications-and-push) | Partial (preferences only; no push) | Settings › Notifications |
| [Settings, language and right to left](#settings-language-and-right-to-left) | Partial | Settings, Language menu |
| [Integrations and skills](#integrations-and-skills) | Partial | Resources › Skills, Resources › Services, Home › Connected services |

## Sign-in and accounts

| What you can do | State |
| --- | --- |
| Sign in with the company account (Keycloak) in the system browser, stay signed in, refresh silently, sign out (also ends the browser session) | Done |
| Open the workspace from the account, pick one when you belong to several ("Where do you want to work?"), remember the choice per user | Done |
| Switch workspace from Settings; see your user, organization, workspace, roles and when the session expires | Done |
| Password, one-time code and password recovery screens | Development only: shown only when no company sign-in is configured; a store build never shows them |
| Sign in with Google or Apple | In progress |
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
| List tasks, search them, filter (All, Active, Needs approval, Completed, Failed or cancelled) | Done |
| Open a task: status, what the agent produced (copy), every step it took, the approval it is waiting on | Done |
| Cancel a task (with confirmation) and resume it | Done |
| Open a task from a link (`/tasks/<id>`) | Done |
| Paging through a long task history | Not built: the app shows the first page the API returns |
| Live updates without pulling to refresh | Not built: the screen refreshes after your own actions and on Refresh |

## Approvals

| What you can do | State |
| --- | --- |
| See what is waiting, with the tool, its risk and what it will do; counts of waiting, approved, rejected | Done |
| Approve (with a second confirmation) or reject, with an optional reason kept in the audit trail | Done |
| See approval chains ("1 of 3 approvals", who approved) | Done |
| See the server's reason when you may not approve (for example the two-person rule) | Done |
| A badge on Approvals with the number waiting | Done |
| Approve from a notification, or with the phone's fingerprint or face check | Not built |

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
| Upload a file with the phone's file picker | Done |
| Open a file's details (type, size, date), download it with the save dialog, delete it | Done |
| Files are indexed so tasks can use them as sources | Done |
| Search and paging through many files | Not built |
| Preview a file inside the app | Not built |

Screen: **Resources › Files**. Details: [Workspace files](files.md).

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
| Advanced routing: see where AI requests go and mark a target healthy, degraded or offline | Done for administrators; read-only for others |
| Export the audit log as CSV or JSON | Done |

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
| List members with counts (active, owners, deactivated, pending invitations) | Done (owners) |
| Change a member's role; deactivate and reactivate a member | Done (owners) |
| Invite by email with a role and expiry; copy the one-time token (and the web link when configured); revoke an invitation | Done (owners) |
| Accept an invitation by pasting a token or link | Done |
| Set the workspace approval policy (two-person rule, approval for medium risk) | Done (owners; members read-only) |
| The invitation is emailed automatically | Not built: the owner sends the token or link |
| Read the audit log inside the app | Not built: export only (see Governance) |

Screens: **Settings › Members and invitations**, **Accept an invitation**, **Approval policy**. Non-owners get the server's explanation of who can do it. Details: [Identity](identity.md#client-screens).

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
| Choose which events you want to hear about: task completed, approval required, run failed, automation failed; email on or off; device notifications on or off | Done (saved to your account) |
| Receive push notifications on the phone | Not built: there is no push service in the app yet (needs Firebase and Apple push keys, see [Needs the owner](#needs-the-owner)) |
| Receive the emails | Depends on the server's email setup; not part of the app |

Screen: **Settings**, notifications card.

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
| Skills: see published skills with their risk, install one | Done (the server checks who may install) |
| Services: see each connected service's health (for example web search) | Done (read-only) |
| Turn a service off for the workspace or change its settings | Web only |
| Connect email (Gmail, Outlook, Zoho), WhatsApp Business, Amazon, Noon or the owner's own apps | In progress |

Details: [Skills](skills.md), [Tools and integrations](tools-and-integrations.md).

## Coming next

Work on other branches. None of it is in the app yet; each item is marked done here only after it merges with tests.

| Item | What it will do | State |
| --- | --- | --- |
| Email inbox | Read and answer Gmail, Outlook and Zoho Mail through the assistant; every sent email goes through approvals | In progress |
| WhatsApp | Connect WhatsApp Business through the official API; read chats and send replies after approval | In progress |
| Commerce | Amazon.sa, Amazon.ae, Noon Saudi and UAE and the owner's own store: orders, listings, prices and stock, with changes approved first | In progress |
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
| Firebase project (`google-services.json`, `GoogleService-Info.plist`) and an Apple push key | Push notifications on Android and iPhone | Not wired yet; the files are kept out of the repository |
| Google and Apple sign-in apps (OAuth client ids, Sign in with Apple service id and key) | Google and Apple sign-in | Keycloak identity providers |
| OAuth apps for Gmail, Microsoft (Outlook), Zoho Mail; WhatsApp Business (Meta) app; Amazon Selling Partner and Noon seller credentials | The email, WhatsApp and commerce connectors | Stored encrypted on the server once each connector merges |
| Billing decisions | Whether and how users pay (store in-app purchase or outside the app), plans and limits, and the default monthly model budgets | Owner decision; nothing is built until it is made |
