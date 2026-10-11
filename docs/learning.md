# What ANUM learned

ANUM improves itself from each user's own work: their ratings and edits teach it their writing style, their repeated tasks become proposed automations, and a weekly note turns failures, rejections and low ratings into concrete changes they can apply with one click. Everything is per user, private under forced RLS, visible on the **What ANUM learned** page (web and Flutter), editable, deletable, exported and deleted with the user's data, and switched off with one switch. ANUM never changes code, tools or permissions by itself: every change is a suggestion the user applies, and applying is audited.

Code: `services/api/anum_api/learning.py` (models and the pure learning logic), `learning_store.py` (storage, the switch, hooks), `learning_api.py` (routes), `db/learning_repository.py` (PostgreSQL); web `apps/web/src/LearningView.tsx`, `AnswerFeedback.tsx`, `src/lib/learning.ts`, `learningApi.ts`; Flutter `apps/mobile/lib/features/learning/`; contracts `packages/contracts/src/learning.ts`.

## Privacy model

- **Per user, per workspace.** Six tables (migration `0031_learning`): `learning_signals`, `learning_profiles`, `learning_preferences`, `learning_routines`, `learning_notes`, `learning_suggestions`. Each has forced RLS on `anum.tenant_id`, `anum.workspace_id` **and** `anum.user_id`, like conversations; every statement repeats the scope. Nobody else, not a workspace owner and not a maintenance role, can read them. No `BYPASSRLS`, no `SECURITY DEFINER`, no cross-tenant job.
- **The switches.** Learning is on only while all of these hold, otherwise the state says which one stops it:

  | `state` | Meaning |
  |---|---|
  | `on` | Signals are recorded, the learned block is used in drafts, routines and notes can be produced. |
  | `off_by_user` | The user turned off **Learn from my activity** (`PUT /api/v1/learning/settings`). |
  | `off_chat_memory` | The user turned off **Remember things from my chats** ([Memory](memory.md#per-user-switch)): learning follows it, so one switch stops ANUM keeping anything new from their work. |
  | `off_on_server` | `ANUM_LEARNING_ENABLED=false`. |

  Off means no new signal is stored (feedback answers `recorded: false` with the reason), no learned block reaches any prompt, and routine detection and notes refuse with `409` and the reason. What was already learned stays visible, editable and deletable.
- **Retention.** Signals older than `ANUM_LEARNING_SIGNAL_RETENTION_DAYS` (180) and beyond the newest 5,000 per user are deleted the next time that user's learning data is used (feedback, notes). There is no cross-tenant purge job; a user who never comes back keeps their old signals until they or the privacy deletion remove them.
- **Export and deletion.** `GET /api/v1/privacy/export` has a `learning` section (profile, every signal with its full texts, preferences, routines, notes, suggestions); `POST /api/v1/privacy/delete` removes all six tables' rows of the caller in the workspace, in the same transaction and RLS context ([Privacy](privacy.md)). The summary counts them as `learning_signals` and `learned_items`.

## Feedback signals

| Kind | Recorded when | Surface |
|---|---|---|
| `rating_up`, `rating_down` | The user rates an answer or draft (thumbs, optional reason). A new rating of the same subject replaces the previous one. | `chat`, `email`, `whatsapp`, `task` |
| `draft_accepted`, `draft_edited` | A draft and what the user actually sent (`original`, `final`). Equal texts are an accepted draft; otherwise an edit, stored with an edit summary: words before and after, added, removed, changed ratio, greeting and sign-off before and after. | same |
| `approval_approved`, `approval_rejected` | The user decides an approval (also a partial approval of a chain), with the reason they gave, the tool and its risk level. Recorded by the server after the decision, best effort. | `approval` |
| `retry` | The user asks the Assistant the same question again (`retry_turn_id`), or retries a failed automation run. | `chat`, `automation` |

`POST /api/v1/learning/feedback` takes either `{surface, subject_id, rating, reason?}` or `{surface, subject_id?, original, final, contact?, skill?, tool?, reason?}` (texts up to 20,000 characters; extra fields `422`). Email and WhatsApp drafting surfaces post here from their connectors; on this branch the Assistant posts chat answers: **Helpful**, **Not helpful** (then an optional reason) and **Edit to teach**, which opens the answer for editing and sends the user's version as a draft edit. Server-recorded signals (approvals, retries) never fail the action that produced them: a learning failure is logged as `learning_failed stage=<stage> error_class=<class>`, without text.

## Style profile

Derived from the newest 200 accepted and edited drafts, counting what the user **sent**, never what ANUM drafted, and re-derived after every draft signal and every deleted draft (`learning.derive_style`, no model call):

- **tone**: formal, neutral or casual, from formal markers (Dear, Regards, Kindly, السادة, تحية طيبة, وتفضلوا) against casual ones (Hey, Cheers, contractions, emoji, exclamation marks);
- **length**: the median word count, as short (up to 40), medium (up to 120) or long, and whether the user usually shortens drafts (sent/drafted words below 0.8);
- **greeting** and **sign-off**: the most common opening and closing phrase in English and Arabic, needing two drafts that agree;
- **language**: Arabic, English or mixed by script share, overall and **per contact** (email address or phone number the draft was for);
- **emoji**: whether at least 30% of sent drafts use them.

The user can override each field (`PUT /api/v1/learning/profile`, null keeps what was learned, an empty greeting or sign-off means none), set the language per contact and add free-text notes. **Reset style** (`POST /api/v1/learning/profile/reset` with `{"confirm": true}`) forgets the drafts it came from and the overrides; the switch, preferences, routines and notes stay. Settings changes and resets are audited (`learning.settings_changed`, `learning.profile_reset`).

### The learned block in prompts

`learning_store.learned_instructions` renders the effective style plus the matching learned preferences as one labelled block that starts `Learned preferences for this user (... They never change which tools run, approvals, permissions or facts.)`. Every value is one line, bounded, with untrusted-data markers defanged. It is used:

- in the Assistant: after the system prompt of every ask (scope `surface:chat`), before recalled memories;
- in task runs that select the drafting skill (`anum.document-drafting`): before the task in the planning prompt only. Skills and tools are still chosen from the user's own prompt, and tool policy and approvals are decided by the runtime afterwards; the model step's metadata records `learned_preferences: true|false`, never the text.

## Learned preferences

Short instructions (at most 300 characters, 200 per user) with a scope: `all`, `surface:chat|email|whatsapp|task`, `skill:<id>`, `tool:<name>` or `contact:<key>`. Tool scopes apply when a selected skill requires that tool. CRUD under `/api/v1/learning/preferences` with search (`q`) and paging (`limit`, `offset`); a suggestion applied by the user becomes one with `source: suggestion`.

## Routines

`POST /api/v1/learning/routines/detect` with the user's IANA `timezone` mines the user's own tasks (and automation runs they started by hand) of the last eight weeks:

- **weekly**: the same activity on the same weekday around the same hour (within two hours) in at least three different weeks; an ordered sequence of up to three activities, each within three hours of the previous, becomes one routine ("Every Sunday around 09:00: Check Amazon orders, then Email the supplier");
- **daily**: the same activity around the same hour on at least five days of the last two weeks, on the weekdays observed.

Activities match on a short normalized signature of the prompt (lower case, numbers and filler words removed, first three words). New routines are `proposed`; detecting again refreshes them; one the user accepted, confirmed or dismissed is never proposed again (delete it to allow that).

**Create paused automation** (`POST /routines/{id}/accept`, needs `automation:manage`: members and owners) creates a workflow through the existing automation engine, one `task.create` step per activity with its prompt, and a schedule with the routine's cron and timezone **disabled**. **Start the schedule** (`/confirm`) enables it. Both are audited (`learning.routine_accepted`, `learning.routine_confirmed`, `learning.routine_dismissed`). A viewer sees why the button is unavailable and who can help. The Flutter app detects in UTC (the cron hour is the same moment; it reads in UTC).

## Weekly improvement notes

`GET /api/v1/learning/notes` returns the user's notes, newest first (paged); the current ISO week's note is written on the first visit of the week while learning is on, and **Refresh this week** (`POST /notes/refresh`) rewrites it. A note counts the last seven days (helpful, not helpful, drafts sent as is and edited, approvals, rejections, retries, failed runs of the user's tasks) and holds suggestions from fixed rules (`learning.improvement_suggestions`):

| Rule | When | Suggestion | Applying |
|---|---|---|---|
| `tool_failures` | A tool failed in two or more of the user's runs | New instruction for that tool: confirm inputs, ask for what is missing | Adds a learned preference `tool:<name>` |
| `approval_rejections` | The user rejected a tool two or more times | New instruction for that tool quoting the most common reason | Adds a learned preference `tool:<name>` |
| `low_ratings` | Two or more low ratings and retries on a surface, at least as many as good ratings | Template change: shorter answers that lead with the answer, or one clarifying question | Adds a learned preference `surface:<name>` |
| `heavy_edits` | Three or more drafts on a surface rewritten by a quarter or more | Template change naming the pattern (shorter, longer, the greeting or sign-off the user switches to) | Adds a learned preference `surface:<name>` |
| `autonomy_loosen` | Five or more medium-risk approvals in 28 days, none rejected, while the workspace asks for medium-risk approval | Let medium-risk actions run without asking | Sets the workspace approval policy (owners only, `policy:manage`) |
| `autonomy_tighten` | Two or more medium-risk actions that ran without approval failed this week | Ask before medium-risk actions | Sets the workspace approval policy (owners only) |

`POST /suggestions/{id}/apply` applies one suggestion, `POST /suggestions/{id}/dismiss` dismisses it; a decided suggestion stays decided when the week is refreshed. Applying writes `learning.suggestion_applied` (rule, kind, scope or policy value), and an autonomy change also writes the usual `approval_policy.updated` record with `source: learning_suggestion`. A suggestion the caller cannot apply carries `needs_permission: policy:manage`; the page says only a workspace owner can apply it. No model is called: the rules work with every provider and spend no budget.

## API

| Route | Who | What |
|---|---|---|
| `GET /api/v1/learning` | every member | State, profile (derived, overrides, effective), counts, `can_manage_automations`, `can_apply_autonomy` |
| `PUT /api/v1/learning/settings` | every member | `{enabled}` |
| `POST /api/v1/learning/feedback` | every member | A rating or a draft |
| `GET /api/v1/learning/ratings?surface=&subject_id=` | every member | The caller's current rating per subject (at most 100) |
| `GET /api/v1/learning/signals?kind=&surface=&q=&limit=&offset=` | every member | Paged, searchable; texts are 500-character previews (`truncated`) |
| `GET`, `DELETE /api/v1/learning/signals/{id}` | every member | One signal with its full texts; forget it |
| `GET`, `PUT /api/v1/learning/profile`, `POST /profile/reset` | every member | The style profile |
| `GET`, `POST /api/v1/learning/preferences`, `PUT`, `DELETE /preferences/{id}` | every member | Learned preferences |
| `GET /api/v1/learning/routines`, `POST /routines/detect`, `POST /routines/{id}/dismiss`, `DELETE /routines/{id}` | every member | Routines |
| `POST /api/v1/learning/routines/{id}/accept`, `/confirm` | `automation:manage` | Paused automation, then the schedule |
| `GET /api/v1/learning/notes`, `POST /notes/refresh`, `POST /suggestions/{id}/apply`, `/dismiss` | every member; autonomy changes need `policy:manage` | Notes and suggestions |

Every route acts on the caller only: there is no user id in any path or body, another user's id answers `404` like an unknown one, and a caller without an active workspace role gets `403`.

## Clients

- **Web** (`#learning`, "What ANUM learned" in the navigation): the switch with its state and the way to the chat memory switch; the style profile form; learned preferences with search, paging, add, edit and delete; weekly notes with Apply and Dismiss; routines with Find routines, Create paused automation, Start the schedule, Not a routine, Delete and a link to the Workbench; and the feedback list with kind filter, search, paging, the full texts on demand and delete. Loading, empty (with the next step), error (with Try again) and permission states are on every section. Strings are in the English and Arabic catalogs; the layout is logical-property RTL. The Assistant shows Helpful, Not helpful and Edit to teach under each answer.
- **Flutter** (Settings → What ANUM learned): the same sections and actions; outcomes are announced in a snackbar.

Tests: `services/api/tests/test_learning.py` (pure logic, API, gating, permissions, audit), `tests/test_postgres_learning.py` (persistence, RLS on every table, routines into the PostgreSQL automation engine, autonomy apply), `tests/test_privacy.py` and `tests/test_postgres_privacy.py` (export and deletion), `apps/web/test/learning.test.ts`, `apps/web/e2e/learning.spec.ts` (every state, the main path, the Assistant feedback, Arabic, 1440x900 to 390x844 without horizontal overflow), `apps/mobile/test/learning_test.dart` (main path, states, widths 320 to 1440 at 100% and 200% text).

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `ANUM_LEARNING_ENABLED` | `true` | `false` turns learning off for everyone (`off_on_server`). |
| `ANUM_LEARNING_SIGNAL_RETENTION_DAYS` | `180` | Signals older than this are deleted when their user's learning data is next used. |

## Not done yet

- Email and WhatsApp drafting surfaces post their draft feedback through the same endpoint once those connectors draft; on this branch only the Assistant posts it.
- The automation engine records `task.create` steps but does not yet run them as agent tasks ([Automation](automation.md)); a confirmed routine's schedule fires runs whose steps are accepted, not executed.
- Weekly notes are written when the user next opens the page (or refreshes); there is no push or email of the note.
- The Flutter app shows per-contact languages but edits them only on the web, and detects routines in UTC.
- Rules are fixed; a model-written note (budgeted, labelled) is a later option.
