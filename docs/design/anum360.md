# ANUM 360 redesign (owner mockups)

The owner's mockups for the ANUM 360 redesign are in [`anum360-mockups/`](anum360-mockups/). They set the target look and flow for the Flutter app and the web Command Center. This page says what each screen does, what it is built from, and which API it uses. Build rules: [CLAUDE.md](../../CLAUDE.md) "Definition of done" and "Responsive UI quality gate", and `.claude/skills/mockup-to-flutter/SKILL.md`.

| File | Shows |
|---|---|
| `mobile-flow-overview.jpg` | Splash, home, voice listening, understanding and planning, live execution, results, dynamic workspace (PMV tyre report), proactive insights |
| `mobile-14-screens.jpg` | Splash, onboarding, login, create account, home dashboard, voice/video call, chat, agents, agent detail, tasks, automations, integrations, profile and settings, mobile menu |
| `mobile-command-flow-12.jpg` | The ANUM Command flow: command screen, listening, plan and confirmation, live agents, task completed, report preview, email summary, calendar and next actions, voice call, quick commands |
| `mobile-command-flow-16.jpg` | The same flow step by step: home, command, listening, understanding, plan and agent selection, executing, live activity, results, report preview, email summary, meeting scheduled, next steps |
| `web-command-center.jpg` | The web ANUM Command Center: sidebar with workspaces, command orb with suggestion chips, composer, capability tiles, "ANUM is working", live agent activity, current tasks, recent commands, results preview, suggestions |

## Visual language

- A deep midnight navy background with a blue-to-violet aurora glow behind the command orb; content cards are raised surfaces (dark on command screens, light cards on report and list screens).
- One accent gradient (blue to violet) for primary actions and the orb; status colours for state (green done, amber pending, red high or stop), always with a text label too.
- The orb (the ANUM "A" mark with a live waveform) is the centre of the bottom navigation and of the command screens.
- Every colour, spacing and type value comes from the app's tokens (`AnumPalette`, `AnumSpacing`, the theme). Mockup values are mapped to the nearest token, never copied.

## Screens and what backs them

| Screen | Job | Backed by |
|---|---|---|
| Splash, onboarding, login, create account | Sign in with email, Google or Apple (OIDC through Keycloak) | `docs/identity.md`, the sign-in module |
| Home | Greeting, ask box with mic, Work/Personal/Custom quick commands, shortcut tiles, Today's focus, recent activity, pinned apps | Tasks, approvals, email, calendar and events APIs |
| ANUM Command | Speak or type one request; suggestion chips | Voice transcription and the task planner |
| Listening and understanding | Live transcript, language choice, the request read back with Edit | Voice API; planner preview |
| Plan and confirmation | The steps and agents ANUM will use; Edit, Proceed or Start execution | Planner preview; approvals for risky steps |
| Executing and live activity | Agents running in parallel with progress and a timeline; keep working in background | Task runs and the event stream (SSE) |
| Results and next steps | Summary, details and files; approve, share, ask a follow-up; suggested next actions | Run outputs, files, approvals |
| Report preview | A tyre cost report with metrics, trend and high-cost vehicles | [Reports](../reports.md) with typed sections (`/reports/<id>`), produced by the tyre planning module (TyrePulse data) |
| Email summary | Urgent emails with priority filters | The email inbox module (`/email/summary`, [email](../email.md)) |
| Calendar and meeting scheduled | Next actions and the meeting with its attendees | [Calendar](../calendar.md) (`/calendar`, `/calendar/events/<id>`); meeting summaries are the meetings module |
| Agents, agent detail | Specialised agents, their skills, memory and settings | Custom agents and skills APIs |
| Tasks, automations, integrations | Lists with filters and toggles | Tasks, automations and integrations APIs |
| Proactive insights | Things ANUM noticed, with one-tap actions and dismiss | The learning and autonomy modules |
| Profile, settings, menu | Account, plan, security, language, theme, privacy | Settings, privacy and workspace APIs |

Numbers and names in the mockups (costs, vehicles, people) are illustrations only. The app shows the workspace's real data, and a screen with no data shows an empty state with the next action.

## Web Command Center

The web app (`apps/web`) follows `web-command-center.jpg`. The Command Center is the home (`#home`, also the landing page; `#command` opens the same page with the composer focused). Code: `src/CommandCenter.tsx` and `src/commandCenter.css` for the page, `src/Shell.tsx` for the shell, `src/lib/commandCenter.ts` and `src/lib/shell.ts` for the pure logic (unit tested in `test/commandCenter.test.ts`), and `src/lib/commandApi.ts` for every API call the page makes.

### Theme

The tokens in `apps/web/src/styles.css` carry the ANUM 360 language: midnight navy surfaces (`--d-bg` `#070b1c`, `--d-surface-1..3`), the blue-to-violet primary gradient (`--d-primary-grad`, white text 5.5:1 to 6.3:1), the logo-mark colours (`--d-mark-a..c`), a glow for the active item (`--d-glow`), and light row cards for lists inside dark panels (`--l-card`, `--l-text` 17:1, `--l-muted` 7.5:1, and status colours `--l-ok`, `--l-warn`, `--l-stop` that pass AA on their tints). Every view picks the restyle up through the tokens: primary buttons use the gradient and the active navigation item is the gradient pill. The web app has only the dark theme. There was no light mode before this change and none was added.

### Shell

- **Sidebar.** The brand row has the ANUM 360 mark (`AnumMark`, an SVG drawn in the mark gradient). Next come the mockup's entries: Home, ANUM Command, AI Chat (`#assistant`), Agents, Voice, Call (`#call`), Automations, Tasks, Team (`#team`, [team work](../team-work.md)), Assistants (`#assistants`, department assistants), Integrations, Commerce (`#commerce`, store sales), Documents (`#files`), Knowledge Base (`#memory`) and ANUM Store (`#skills`, the skill catalogue). After them come **Workspaces** (`GET /api/v1/me/workspace-memberships`; switching goes through the existing membership check; **Add workspace** opens a dialog that checks `GET /workspaces/current/permissions` and creates the workspace with `POST /organization/workspaces`, and anyone who is not an organization admin is told who can do it), **More** (Inbox, WhatsApp, What ANUM learned, Approvals, Activity, Autonomy, Settings) and **Admin** (Governance, Enterprise, Members, Audit log, Budgets, Approval policy, Developer for owners, Organisations for platform operators: [Customer organisations](../customer-organisations.md)). The brand row shows the organisation's logo and name when its branding sets them, and the top bar's notifications menu also lists the person's team notifications (assignments, comments, reminders) with their unread count. Each group can be collapsed, and the choice is remembered in this browser. A new view is added in `src/lib/shell.ts` (its id and group), an icon in `Shell.tsx`, a `nav.<id>` label in both catalogs and its render line in `main.tsx`. On phones, every view sits in one horizontal strip and the workspace list moves to the top-bar switcher.
- **Top bar.** Global search opens the command palette on Cmd+K or Ctrl+K (or "/" outside a text field). It searches views, tasks (`GET /tasks?q=`), documents (`GET /files?query=`) and emails (`GET /email/messages?q=`). A section that cannot be searched says so. Enter opens the result: an email opens in the Inbox with that message selected. The top bar also holds the workspace switcher, refresh, the **notifications bell** (pending approvals, automation alerts and "ANUM acted" notices, with a count) and the **user menu** (the name from the sign-in token, else the user id, plus the role; links to Settings, Notifications, Language and Privacy; Sign out with OIDC).
- **Not built.** Calendar and Reports are left out of the sidebar because no API backs them yet. The upgrade card is omitted because no plan or billing API exists, so the user menu shows the workspace role where the mockup shows the plan.

### Command Center page

| Mockup part | What it does here | Backed by |
|---|---|---|
| Mode tabs | Voice, Text and Files. Vision is not offered: the API stores images, but no model reads them yet | none |
| Orb, "Listening... Speak now, name" | Push to talk through the browser's speech recognition (`createPushToTalk`). The transcript fills the composer, and the person checks it and presses Send. Browsers without recognition are told to type instead | Web Speech; the voice API on send |
| Floating chips | Open the real view (Inbox, Tasks, WhatsApp, Automations), prefill a request the agent can carry out (report, web research, analyse documents), or open the palette | none |
| Language pills | The speaking language: English or العربية. Urdu is not offered because the web catalogs have no Urdu yet | none |
| Composer | Text, mic, attach a document (uploaded to Documents and named in the request), pick from Documents, example line, quick commands (the person's own recent requests first, then examples; refresh moves through the examples) | `POST /files`, task create-and-run |
| Capability tiles | Email, Data analysis, Reports, Voice assistant, WhatsApp, Tasks, Web research, Automations. Meetings and Phone calls are not offered: there is no calendar or telephony module | none |
| ANUM is working... | Five steps (understanding, planning, agents working, reviewing, presenting) worked out from the run's steps and status, with timings from the step timestamps. Shows Stop (cancel), Review the approval while a step waits, the answer when the run completes, Try again when it fails | `GET /tasks/{id}/latest-run`, the event stream |
| Live agent activity | Tasks in flight with their latest step and progress | Tasks, latest runs, SSE |
| Current tasks, Recent commands | Open tasks with their count, and the newest commands with "use again" | `GET /tasks` |
| Results preview | The newest documents | `GET /files` |
| Suggestions for you | Pending approvals, failed tasks (retry), unread mail, connect a mailbox, tasks not started, add documents, first command. Built only from the workspace's data | Approvals, tasks, email accounts, file usage |
| Bottom bar | Talk, Chat, Automate, Agents. Video is not offered (no video API) | none |

A typed command creates a task and runs it (`POST /api/v1/tasks`, then `POST /tasks/{id}/run`). A spoken command goes through the voice API first: a session with `session` retention, the transcript segment, then `POST /voice/sessions/{id}/commands` creates the task, which is then run. Every call is in `src/lib/commandApi.ts`. When the command plan-preview API (`docs/command.md`, being built separately) lands, `submitCommand` there becomes "preview the plan, then confirm". The page does not change.

Tests: `e2e/command-center.spec.ts` covers the panels with workspace data, a typed command end to end, shortcuts and tiles, the palette, notifications and the user menu, the sidebar workspaces and Add workspace, and axe plus no horizontal overflow at 1440x900, 1366x768, 1024x768, 768x1024 and 390x844 in English and Arabic, on the desktop and mobile projects. `#home` is also in `e2e/accessibility.spec.ts`.
