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
| [Billing](#billing) | Done (billing contact is edited on the web) | Settings › Billing (Overview, Plans, Invoices, invoice detail) |
| [Privacy](#privacy) | Done | Settings › Your data and privacy |
| [Notifications and push](#notifications-and-push) | Done in the app; needs the owner's Firebase setup to deliver | Settings › Notifications, Phone push card |
| [Settings, language and right to left](#settings-language-and-right-to-left) | Partial | Settings, Language menu |
| [Tyre planning](#tyre-planning) | Done for everything an operator does; connecting the source is web only | Home › Tyre planning |
| [Integrations and skills](#integrations-and-skills) | Partial (connectors in progress) | Resources › Skills, Resources › Services, Home › Connected services |
| [Personal money](#personal-money) | Done for the phone screens, with Payslips, Send home, the salary profile, the Plan, the Money advisor, bank loans, trips and projects and foreign balances; live research by topic is web only | Finance › Money planner, Safe to spend (Salary), Bills (Fixed costs), Debts (People) |
| [Finance review](#finance-review) | Done: reconciliation from start to completion, statement import (CSV and PDF), iPhone SMS review, its set-up (safety check, forwarding, devices and their tokens, the Shortcut) and the ledger, owner rules, merchants, subcategories and automation, the net worth parts; the SMS settings form and Apply to similar are web only | Finance › Transactions, Suggestions, Connections, More in Finance |
| [Commerce](#commerce) | Done for everything an operator does; connecting a channel and alert rules are web only | Home › Sales and orders |
| [Business](#business) | Done on the phone for everything the desk edits; research, drafts, CSV import and proposal writing are web only | More › Business |
| [Marketing](#marketing-whatsapp-autonomy-learning-and-agents) | Done on the phone, except media upload and the month grid | Marketing (Posts, Calendar, Campaigns, Accounts, Analytics) |
| [WhatsApp](#marketing-whatsapp-autonomy-learning-and-agents) | Done on the phone, except sending media and header or button template parameters | WhatsApp inbox, Templates, Setup |
| [Autonomy](#marketing-whatsapp-autonomy-learning-and-agents) | Done | Settings › Autonomy (Activity, levels, quiet hours, caps, blocked lists, Rules, Organization guardrails) |
| [Learning](#marketing-whatsapp-autonomy-learning-and-agents) | Done (English only) | Settings › What ANUM learned |
| [Agents](#marketing-whatsapp-autonomy-learning-and-agents) | Done (knowledge sources are picked on the web) | Agents, agent page, Skills loadout |
| [Owner desk](#owner-desk) | Done for what a phone decides: the Executive desk with grants, Ready to sell, IBKR trading with order approval and the kill switch, the Action browser with step approval; HQ is web only | Menu › Executive desk, Action browser, IBKR trading, Ready to sell |

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
| Advanced routing: see where AI requests go, with the health the probe measured (never typed), probe a target or take it out of routing | Done for administrators; read-only for others |
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

## Billing

For workspace owners and organisation administrators ([Customer billing](billing.md)): Settings › Billing has three tabs. **Overview** shows the plan, its status and dates, usage against the plan's limits, the open balance, the bank transfer details (copy the IBAN) and the billing contact, with cancel and reactivate. **Plans** previews a change (amount, VAT, credit, now or at period end) and blocks it when current usage is over the new plan's limits. **Invoices** has search, a status filter and paging; an invoice shows its lines, VAT, totals, payments and credit notes, saves its PDF, and offers card payment when the issuer turned it on. Members and viewers see who can open billing. Code: `lib/features/billing/`, tests: `test/billing/billing_test.dart`.

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

## Personal money

A small set of phone screens inside **Finance** for the person's own money ([Lent and borrowed](finance.md#lent-and-borrowed), [Fixed commitments](finance.md#fixed-commitments), [Planning and advice](finance.md#planning-and-advice)). Everything is the caller's own records; the server checks `finance:read` and `finance:manage` on every call. **Advice only: nothing here pays, buys or transfers money.**

| What you can do | State |
| --- | --- |
| **Money planner**: type a money question in English or Arabic ("Can I afford a car of 60,000 SAR?", "أي دين أسدد أولا؟") or tap an example; the question goes to the Assistant, whose finance skill runs the planner (`finance.plan.*`) on your own records and, when web search is on, live sources. The answer shows with "Advice only", each live source's address as a link (opened in the browser) and the date it was read; Stop ends the answer. The question and answer stay in the Assistant's conversations | Done |
| **Lent and borrowed**: what they owe me and what I owe, per currency, the overdue count, each entry with its due date and an Overdue, Due soon, Open, Repaid or Closed badge, a filter (All, They owe me, I owe) and paging | Done |
| Lent and borrowed: add an entry (person, amount, currency, who owes whom, date, due date, notes) and record a repayment by linking a recorded transaction (money in when someone repays you, money out when you repay) | Done (owners and members) |
| Lent and borrowed: each entry's next repayment, monthly repayments, rate and the amount overdue in its currency; the totals with the overdue part per currency; **Schedule** (every repayment with its date, principal, interest, amount and Repaid, Part repaid, Overdue or Due); **Edit** and Add an entry with an interest rate, monthly repayments and the first repayment, the reminder and closed without full repayment | Done (owners and members) |
| **Possible repayments** under the entries: a transaction proposed as a repayment, Accept and link (with the payload hash, through the links API) or Dismiss, with paging | Done (owners and members) |
| **Expected one-off income** (on Safe to spend): list with Show received ones too and paging, add, edit, Mark received, delete after a confirm; the forecast reloads after each change | Done (owners and members) |
| One place per job, as on the web: Lent and borrowed is the **People** part of Debts, the monthly **Fixed costs** sit under the bills on Bills, the **Salary** card is on Safe to spend. The old separate phone sections are gone | Done |
| **Fixed costs** (on Bills): one total per currency with the share of income it takes (salary profile, else the records; "no income to compare with" when there is none) and the list (rent, bills, subscriptions, installments, debt minimums, money borrowed from a person due this month, regular transfers home), plus what is left out so nothing counts twice; empty points to Add bill above | Done (read only) |
| **Debt payoff** (top of Debts): avalanche against snowball per currency with months to debt-free, total interest, the order, which is cheaper and by how much, the debts with no rate, and which debts are money borrowed from a person (`source: person_loan`) | Done (read only) |
| **Salary** (on Safe to spend): the employer and pay day, the next pay day, this month's expected net against what was received with a Received, Late, Partly received, Missing or Not due yet badge, the count of months with no salary found, and Yes, it is or No, it is not for a month whose salary was found in the records (`POST /salary/months/{month}/decision`) | Done (owners and members decide) |
| **Safe to spend**: the next pay day with the net expected then, per currency, and the minimum payments overdue (`minimum_overdue`, still counted until a payment is matched) | Done (read only) |
| Loan reminders (`finance_loan_due`, in-app only) open Finance, Debts, where People is | Done |
| Lent and borrowed: delete an entry, unlink a repayment, link the money handed over, and **Split one payment across loans** with the same person (pick the payment, an amount per open loan in its currency, checked before sending, all parts or none) | Done (owners and members) |
| **More in Finance** (the app bar menu): **Money advisor** (the weekly note, its switch and weekday, Work it out now, past notes), **Plan a purchase or loan** (Can I afford it? and Cash, a loan or installments?), **Payslips** and **Send home** (list, search, paging, add, edit, delete, link the transaction and remove the link; providers compared), **Bank loans** (schedule, pick a payment, undo an accepted batch, add, edit or remove the terms), **Trips and projects** (add, edit, delete, tag and untag payments), **Foreign-currency balances** (read only) | Done (owners and members change) |
| **Salary profile** (More in Finance, or Finance settings): edit the employer, expected salary, pay day and account, add, edit and remove deductions and salary advances, delete the profile | Done (owners and members) |
| Live research by topic | Web only (the Money planner asks the Assistant, which may) |

Every screen has loading, empty with a next action, error with what failed and Retry, permission denied (who can), offline, Arabic right to left and 200 percent text. Code: `apps/mobile/lib/features/finance/finance_personal_*.dart` and the parity pages (`finance_more_*.dart`, `finance_advisor_screens.dart`, `finance_income_screens.dart`, `finance_loans_screens.dart`, `finance_split_screen.dart`, `finance_tags_screens.dart`); tests: `test/finance/finance_more_test.dart` and `test/finance/finance_money_test.dart` (every page against the API's shapes, each state, the bodies sent, 320 to 1024 px in English and Arabic, 200 percent text at 320), `test/finance_lending_test.dart` (schedules, overdue per currency, editing, proposals, expected income, every state, 390x844 and 768x1024 in English and Arabic), `test/finance_personal_test.dart` (model parsing, the API routes and bodies, the planner over the Assistant's stream with an error and Stop, every state, adding an entry and a repayment, the salary confirm, the income share, Arabic right to left, and 360x640, 390x844 and 768x1024 at 200 percent text in English and Arabic).

## Finance review

What waits for the owner in Finance, on the phone ([Transaction types](finance.md#transaction-types), [Suggestions](finance.md#suggestions), [Import history](finance.md#import-history), [Reconciliation sessions](finance.md#reconciliation-sessions), [Rules and learning](finance.md#rules-and-learning), [iPhone SMS](finance-sms.md#on-the-phone), [PDF statements](finance-statements.md#on-the-phone)).

| What you can do | State |
| --- | --- |
| **Transactions**: each row shows its transaction type next to its category; "N uncategorized, not counted in income or spending" with Show them, which lists only those rows (`GET /register?type=uncategorized`); **File it** on such a row picks the type and, optionally, the category (`PATCH /transactions/{id}`); a row recorded from an SMS or Apple Pay says why it is corrected on the web instead | Done (owners and members file) |
| **Suggestions** (`GET /suggestions`): one card per item with what was found, why, the money, how sure, and Accept, Mark as seen or Dismiss; an item resolved on its own page (a reconciliation exception, a bank message) has Finish on the web instead of Accept. The unresolved-suggestions notice (`finance_suggestions`) opens it | Done (owners and members decide) |
| **Connections**: the last statement import (file, date, account; imported, attached to a recorded payment, held for review, not in the ledger; the balance check) and reconciliation status per bank and card account (reconciled through, the open session with its unexplained difference and the items that need you, months with no statement, the next step); there is no live bank feed | Done (read only) |
| **Reconciliation** (More in Finance, or Open reconciliation on Connections): every open exception across accounts sorted by the server, paged and exported as CSV through the save dialog; the sessions by state; **Start a reconciliation** from an import or typed balances; a session's equation, card check and exceptions with Match, On or Not on this statement, Accept as reversal, Post correcting entry (its journal lines and payload hash, then Approve and post), Undo, **Complete**, **Reopen** with a reason and the history; **Edit figures** and **Delete** while it is open | Done (owners) |
| **Import a statement** (More in Finance): a CSV or PDF picked with the file picker and held in memory; the preview (layout, columns, date format, balance check, unreadable lines, each row's match and the choice per row; for a PDF the password, the column roles, row edits and import anyway), the result, and the earlier imports with **Undo import** | Done (owners) |
| **iPhone SMS review** and **Ledger** (More in Finance): messages by status with Record, Record the correction, confirm the format, learn it, AI fields (when on), Dismiss and Undo; Paste a message; learned formats switched on, off or deleted; the ledger month with its filters, figures, accounts, cards, loans, spending, twelve months, bills, debts, goals and the journal with each entry's chain | Done (owners) |
| **Rules and merchants** (More in Finance): rules with search, states, paging, the editor with Preview, Save and **Approve** with the payload hash shown, Reject, Apply to earlier rows with the preview hash, versions with Restore, recent changes with Undo; merchant spellings; subcategories (archive and restore); automation | Done (owners) |
| **Net worth** (Finance overview): each currency's net worth with its lines (accounts, money lent, holdings at cost, debts with no account, installments) and what is left out, never added across currencies | Done (read only) |
| **Statement keyword rules** (Finance settings, or More in Finance): add, edit and delete | Done (owners) |
| **iPhone SMS set-up** (iPhone SMS review, Set up forwarding; `finance_sms_setup.dart`): the safety check and its results, forwarding on and off once it passed, the Shortcut and Apple Wallet addresses, the devices with Pause, Resume, New token and Disconnect (both after a confirm), Connect a device with what its token may send, and the Shortcut steps; a token is shown once on its own protected page ([On the phone](finance-sms.md#on-the-phone)) | Done (owners; a viewer is refused by the API) |
| Apply to similar (from a corrected transaction), the iPhone SMS settings form (card and account numbers, Apple Wallet cards, extra senders, AI help, retention), finishing the rows an import left out of the ledger | Web only (Finish on the web links when the build has `ANUM_WEB_APP_URL`) |
| Business books on the phone: the P&L, bills and payments, the Accountant review (with Attach or Dismiss on a proposed receipt), adjustments, manual entries (with New manual entry), the fixed asset register and loans, the VAT filing settings ([Business books](business-books.md#mobile)) | Done (owners and accountants change) |
| Business books: the ledger and statements, the period close, rates, budgets, vendors and allocations | Web only |

Every part loads and fails on its own, with loading, empty with a next action, error with Retry, permission denied, Arabic right to left and 200 percent text. Code: `apps/mobile/lib/features/finance/finance_review_*.dart`, `finance_recon_*.dart`, `finance_import_screens.dart`, `finance_sms_screens.dart`, `finance_rules_*.dart`, `finance_tools_*.dart`; tests: `test/finance/finance_tools_test.dart` (each decision and the body sent, the payload and preview hashes, every state, 320 to 1024 px in English and Arabic, 200 percent text), the journey `integration_test/journeys/finance.dart`, `test/finance_review_test.dart` (models and catalogs, the routes and bodies against answers shaped like the API's, filing, accept and dismiss, every state, Arabic right to left, and 360x640, 390x844 and 768x1024 at 200 percent text in English and Arabic).

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

## Tyre planning

The owner's fleet tyre data ([Tyre planning](tyre-planning.md)). Everyone with a role reads; `tyre:operate` (owners and members) syncs, generates, deletes, sends and schedules; connecting the source is owners on the web. The server checks every call.

| What you can do | State |
| --- | --- |
| Dashboard figures, tyres due by month with search and paging, open alerts | Done |
| Acknowledge an alert | Done (owners and members) |
| Sync now (reads the mapped tables again and says how many alerts are new) | Done (owners and members) |
| Purchase plan for 3, 6 or 12 months: tyres to buy, estimated cost, lines without a price | Done |
| Reports: list with paging, open one (numbers, tables capped at 25 rows, notes, sending history) | Done |
| Reports: generate any of the eight kinds for a period, delete one (with confirmation) | Done (owners and members) |
| Reports: request sending by a connected email or WhatsApp integration; it waits in Approvals | Done (owners and members) |
| Report schedules: list, add (daily, weekly or monthly at a time), delete | Done (owners and members) |
| Connect the source, mapping, assumptions, CSV and PDF downloads, reopen an alert, schedule delivery to a recipient | Web only |

Screen: **Home > Tyre planning** with the tabs Dashboard, Due, Alerts, Plan and Reports (`apps/mobile/lib/features/tyre/`, English and Arabic; `test/tyre/tyre_test.dart` and `test/tyre/tyre_ops_test.dart` cover every state, the role differences, validation, the server's refusals and 320 and 390 point widths).

## Business

Leads from the WhatsApp sales desk are Business leads on the phone too: their row starts with **WhatsApp** and shows a chat icon ([Business: WhatsApp leads](business.md#whatsapp-leads)). Bringing waiting WhatsApp leads in and answering duplicate suggestions are on the web.

The internal sales desk ([Business](business.md)). Owners and members read it; viewers are told who can. Every change below goes to the API, which checks the role again: the phone hides controls the role cannot use and shows the server's sentence when it still refuses.

| What you can do | State |
| --- | --- |
| Overview figures, leads (search, status filter, paging, evidence, moving a lead through its states with a reason, recording what happened), pipeline, proposals waiting for acceptance, projects and milestone status | Done |
| Catalog: search products and service packages, filter by maturity, page through them; open one to see every priced version with cost, margin and a warning when the price is under the cost | Done |
| Catalog: create and edit a product, add and edit a draft version, activate it (the active one retires), retire or discard a version, delete a product (with confirmation; the server refuses when a lead, opportunity or support plan uses it) | Done (owners; members read only, with a notice) |
| Accounts: search, page, open one with its contacts and leads; create and edit an account | Done (owners and members) |
| Accounts: mark an account or a contact do not contact (with a reason) | Done (owners and members); clearing the mark is owners only |
| Accounts: delete an account | Done (owners, with confirmation; the server refuses when other records depend on it) |
| Contacts: add, edit and delete a contact with its consent basis; a contact with an unknown basis is shown as blocked | Done (add and edit: owners and members; delete: owners) |
| Support tickets: list, filter, open, create, edit, change status, escalate, notes, replies waiting for approval, delete | Done |
| Leads: create (for a new or an existing company, with an optional contact and offer), edit title, offer, contact and next step | Done (owners and members) |
| Leads: delete | Done (owners, with confirmation; the server refuses when the lead has an opportunity) |
| Opportunities: open one from a qualified lead, edit value, probability (your assumptions), note, next step and decision date, move between stages (lost needs a reason) | Done (owners and members); won comes only from an accepted proposal and is final |
| Research, drafts, CSV import, writing proposals, the support policy | Web only |
| Sending anything | Off by design: ANUM never contacts anyone from Business |

Screens: **More › Business** with the tabs Overview, Leads, Pipeline, Proposals, Projects, Catalog, Accounts and Support (`apps/mobile/lib/features/business/`, English and Arabic). Tests: `test/business_test.dart`, `test/business_support_test.dart` and `test/business_catalog_accounts_test.dart` and `test/business_deals_edit_test.dart` (every list state, create, edit and delete with the server's refusals, member against owner controls, Arabic and 320 and 390 point widths).

## Marketing, WhatsApp, autonomy, learning and agents

Each screen works against the same API routes as the web, in English and Arabic (learning: English), with loading, empty, error, permission-denied and long-list states.

| Area | On the phone | Web only |
| --- | --- | --- |
| Marketing | Compose and edit posts with live limit checks, AI ideas and hashtags, tracked links and files from Files; schedule, move, cancel, publish, retry; campaigns (create, edit, delete, auto-post for owners); accounts; brand voice; analytics with best times | Media upload, month grid |
| WhatsApp | Inbox, thread, send text and approved templates (with parameters), open media, Templates with the auto-send switch, Setup (status, connect, check, test message, disconnect) | Template header and button parameters, sending media |
| Autonomy | Activity, levels, thresholds, quiet hours, daily caps, blocked recipients and domains, approved templates, Rules, organization guardrails | None known |
| Learning | Switch, style (per-contact languages, no greeting, no sign-off), preferences, routines (time zone typed), notes, feedback | None known |
| Agents | List, page, editor, memory, Skills loadout (states, tools, fixes, add and remove) | Knowledge source picking, skill count on list cards |

Details: [Marketing](marketing.md), [WhatsApp](whatsapp.md), [Autonomy](autonomy.md), [Learning](learning.md), [Agents](agents.md).

## Owner desk

Four owner screens that were web only, opened from Menu, More (`lib/features/owner_desk/owner_desk_routes.dart`, each a signed-in named route with a controller that lives as long as its page). Each works against the same API routes as the web, in English and Arabic (right to left), with loading, empty with a next action, an error that says what failed with Try again, and permission denied that says who can; checked at 320, 390, 768 and 1024 px at 200 percent text (`apps/mobile/test/owner_desk/`) and by the journey `integration_test/journeys/owner_desk.dart`.

| Screen | On the phone | Web only, and why |
| --- | --- | --- |
| **Executive desk** `/executive` ([Executive PA](ANUM_MINE_EXECUTIVE_PA.md#mobile)) | The workspaces you may see in three groups (granted to you, not granted, could not be read), only the figures a grant shares (a scope not granted reads "Not shared", never 0), the current workspace's own summary, Switch to a workspace (the API confirms the membership, as Settings does), today's briefing in the app's language, and the grants of the current workspace for owners: request (person, what to share, an optional end date), approve the exact payload hash after a confirm that shows it, reject, narrow and revoke | Offices and agents and the headquarters assistant: they work inside the 3D office ([HQ](#hq-stays-on-the-web)) |
| **Ready to sell** `/ready-to-sell` ([Ready to sell](selling-readiness.md#mobile)), in the menu for owners | The checklist from `GET /platform/readiness`, missing first, each item with its state, why it matters, the lines the web shows (provider, empty company fields, backups, legal facts, plans, model, issuer), what to do next and where on the web; the SPF and DMARC check of the mail domain. Others see "Only the platform owner can see this checklist" | Payment keys, company and legal details, publishing the legal pages and plans: long forms with secrets or a lawyer's review |
| **IBKR trading** `/finance/ibkr` ([Live trading](finance-live-trading.md#on-the-phone)), behind the app lock's Finance area | Mode (PAPER or LIVE), market, kill switch, both connections (masked account, IBKR session, checked, last read, records match), the paper period with progress bars, the live gates, IBKR's own figures and positions, orders paged with Check at IBKR, Look up and send and Cancel; the order ticket with IBKR's price (the request waits for approval; making it sends nothing); the limits, changed by the owner; alerts, marked read; an order waiting for approval shows the exact order, account, total, a countdown and the payload hash and is approved with that hash after a confirm (refused on the phone once expired; LIVE in red with "real money"), or rejected; Stop everything with a reason, and Allow orders again by typing RESUME | Connecting, buy plans, the allowed ETF list, the live switch and its code, explaining a mismatch: setup done once, with secrets or typed codes |
| **Action browser** `/action-browser` ([Action browser](action-browser.md#on-the-phone)) | The workspace's browser runs newest first (`GET /action-browser/runs`, Load more), a run's status, sites, how many saved items it may use, the step log (Arabic from the step's detail), the result; the steps it waits for with the site, address, action and amount on the page, approved with the payload hash after a confirm, or rejected; Stop for the owner or the task's creator; refreshes every 4 seconds while it runs; for owners the live picture (masked, in memory only), taps, typing and keys for a "Needs you" step, and the saved logins and cards (names and masked hints; secrets typed once into hidden fields, sent and forgotten) | A step that needs the card security code (the phone says to approve it on the web; Reject works) |

### HQ stays on the web

The headquarters (the 3D office, `apps/web/src/HeadquartersCanvas.tsx`, [master plan](ANUM_MINE_VIRTUAL_HEADQUARTERS_MASTER_PLAN.md)) is not on the phone, on purpose: it is a raw WebGL scene in the browser whose frame time and memory budget are measured in desktop Chromium with the mouse orbiting the scene ([HQ performance budget](ANUM_MINE_HQ_PERFORMANCE_BUDGET.md)), Flutter has no WebGL to run it in (it would have to be rebuilt as a native 3D scene and kept in step with the web's), its navigation is built for a pointer and a keyboard, and everything it shows has a phone screen of its own (tasks, approvals, meetings, the executive desk and briefing). The Executive desk's Offices tab and the assistant that walks the headquarters stay with it for the same reason.

## Coming next

Work on other branches. None of it is in the app yet; each item is marked done here only after it merges with tests.

| Item | What it will do | State |
| --- | --- | --- |
| Email inbox | Read and answer Gmail, Outlook and Zoho Mail through the assistant; every sent email goes through approvals. The menu names both email screens by what they hold: **Email: Gmail, Outlook, Zoho** and **Email: own server** ([One Email module](email.md#one-email-module); the web has one Email page) | In progress |
| Team work and customer organisations | Work together in teams and manage separate customer organisations | In progress |
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
