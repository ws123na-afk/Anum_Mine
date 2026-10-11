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
| Report preview | A tyre cost report with metrics, trend and high-cost vehicles | The tyre planning module (TyrePulse data) |
| Email summary | Urgent emails with priority filters | The email inbox module |
| Calendar and meeting scheduled | Next actions and the meeting with its attendees | The meetings and calendar modules |
| Agents, agent detail | Specialised agents, their skills, memory and settings | Custom agents and skills APIs |
| Tasks, automations, integrations | Lists with filters and toggles | Tasks, automations and integrations APIs |
| Proactive insights | Things ANUM noticed, with one-tap actions and dismiss | The learning and autonomy modules |
| Profile, settings, menu | Account, plan, security, language, theme, privacy | Settings, privacy and workspace APIs |

Numbers and names in the mockups (costs, vehicles, people) are illustrations only. The app shows the workspace's real data, and a screen with no data shows an empty state with the next action.
