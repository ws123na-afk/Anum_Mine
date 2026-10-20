# Autonomy

ANUM should move fast: it acts on its own where that is safe, tells you when it did, lets you undo what can be undone, and asks where asking is wise. The autonomy policy is a workspace layer on top of the [tool policy and approvals](approvals-and-risk.md); it is evaluated by the runtime, outside the model, at planning, at execution (inline and in the Temporal worker) and on recovery. Code: `anum_api/autonomy.py` (evaluation, stores), `anum_api/autonomy_api.py` (routes), `anum_api/db/autonomy_repository.py` (PostgreSQL), the runtime hooks in `anum_api/runtime.py`.

## Levels

Every tool belongs to a category: `email`, `whatsapp`, `commerce` (prices, stock, listings), `developer`, `files`, `tasks` or `general`. Each workspace sets a level per category:

| Level | What happens |
|---|---|
| `ask_always` | The call pauses for an approval, like any high-risk action. |
| `act_and_notify` | ANUM does it, records it in the activity feed and announces it (`autonomy.action_taken`); when the tool is reversible the notification and the feed offer **Undo** until the undo window closes. |
| `act_silently` | ANUM does it and lists it in the activity feed, without a notification. Only for low-risk tools: a medium or high-risk tool at this level acts and notifies instead. |

The category level is a **ceiling** over ANUM's recommendation for the kind of action (default: `act_silently`, i.e. no extra restriction):

| Kind (`action_kind`) | Recommended |
|---|---|
| `read` (reading, analysis), `draft` | act silently |
| `archive`, `pause`, `create` | act and notify |
| `reply`, `send` | act and notify **only** to known recipients with an approved template; otherwise ask |
| `update`, `other` | ask, unless the platform already lets the tool run without approval (then silent for low risk, notify for medium) |
| `delete`, `price_change`, `stock_change`, `payment`, `merge_code`, `publish` (social posts, [Marketing](marketing.md)) | **always ask** (protected) |

A tool declares its kind and category on its `ToolDefinition` (`category`, `action_kind`). Undeclared values are guessed from the tool name (`gmail.send_message` is email/send, `noon.update_price` is commerce/price change), cautiously: a medium or high-risk tool whose name merely sounds like a read is `other` and asks. Connector tools declare both; the email and WhatsApp tools do:

| Tool | Category / kind | Risk | Without approval |
|---|---|---|---|
| `search_email`, `read_email`, `list_whatsapp_conversations`, `read_whatsapp` | read | low | silently |
| `draft_whatsapp_reply` | whatsapp / draft | low | silently |
| `draft_reply` | email / draft | medium | acts and notifies |
| `mark_email_read` | email / update | low | silently, **Undo** for 24 hours (sets the read state back) |
| `archive_email` | email / archive | medium | acts and notifies, **Undo** for 24 hours (`unarchive_email` moves it back) on Gmail and Zoho Mail; Outlook archives are not undoable from ANUM (Microsoft Graph gives a moved message a new id) |
| `unarchive_email` | email / update | low | the compensating tool |
| `send_whatsapp_template` | whatsapp / reply | medium | acts and notifies only with an approved template to a known recipient (below) |
| `send_whatsapp`, `send_email` | reply / send | high | **never**: free text always asks |

## Always asks

Whatever the level, a rule or the approval history, these always ask:

- money, prices, stock, deleting, merging code (the protected kinds);
- **first contact** with a recipient not contacted before in this category and not vouched for by the tool's connector (below), and a `reply` or `send` whose recipients ANUM cannot read from the arguments;
- **bulk** sends above the workspace's recipients threshold (default 5);
- an amount above the workspace's amount threshold (default 0) or a price change above its percentage threshold (default 0);
- free-text messages: a `reply` or `send` without a template always asks, whatever the level, a rule or the approval history. Only a message with a template can act on its own, and only when the template is approved for its category (below) or an owner made a rule for that exact tool-and-template pattern (not for WhatsApp, whose auto-send list is the only way).

Recipients become known when a call to them completes, approved or autonomous. They are stored only as SHA-256 digests of tenant, workspace, category and the normalized address (`autonomy_recipients`), never the address itself.

### Connector facts: who wrote first, and whose templates

A connector registers a facts function with its tools (`ToolRegistry.register(..., autonomy_facts=...)`, returning `AutonomyFacts`); the runtime reads it at every evaluation (planning, execution, the Temporal worker's re-check), in the caller's tenant context. A failure to read it propagates, so a call never acts on a guess. Facts can only make a recipient known and name a category's approved templates; they never lift protected kinds, bulk, thresholds, levels, quiet hours, caps, blocked recipients or the tool policy.

- **WhatsApp**: the customer of the conversation a send names is known when they have written to the business in it (an inbound message, so the 24-hour window has a start). A number that never wrote, or a conversation holding only echoes from the phone app, is first contact and asks. The whatsapp category's **approved templates are the owner's auto-send list** (WhatsApp > Templates, *Send without asking*): that list is the one place they are kept, and the workspace's approved templates below do not apply to WhatsApp, so there is no second list to keep in sync. Because that list owns the category's templates, no rule can add a WhatsApp template beside it and ANUM does not suggest one from repeated approvals (put the template on the list instead); a rule can still make a listed template always ask ("Always ask for this"). Ticking a template off makes it ask again at once.
- **Email**: the sender of the message being answered (`in_reply_to`, a message the caller may see) is known; anyone else on the reply is first contact unless ANUM wrote to them before. Email has no templates, so `send_email` is free text and always asks; the fact only changes the recorded reason.

## Guardrails (organization floors)

Organization administrators (`organization_policy:manage`, the `org_admin` role; see [Governance and scale](governance-and-scale.md#who-may-change-what)) set floors every workspace of the tenant keeps (`PUT /api/v1/autonomy/guardrails`):

| Guardrail | Effect |
|---|---|
| `spending_limit` | An action carrying a larger amount is **blocked**, even with an approval. |
| `blocked_recipients`, `blocked_domains` | A call to them (subdomains included) is **blocked**, even with an approval; the re-check at execution fails an already approved run. |
| `category_ceilings` | The most a workspace may set per category. |
| `max_auto_amount`, `max_auto_recipients`, `max_auto_price_change_percent` | The highest thresholds a workspace may set. |
| `daily_caps` | Autonomous actions per day per category or tool name; above the cap the call asks. Without a cap a category allows 200 a day. Reads and drafts are not counted. |
| `quiet_hours` | `{start, end, timezone}` (a window may cross midnight); inside it nothing with an effect runs on its own, it asks. |

A workspace (`PUT /api/v1/autonomy/settings`, owners) may be stricter but never looser: a setting above a floor answers `422` naming each problem; a floor lowered later caps the stored value at evaluation. Workspaces add their own caps, quiet hours and blocked lists (the union applies), approved templates (for templated tools whose connector does not own its list; WhatsApp's are its auto-send list) and `proposal_after` (approvals before a suggestion, default 3).

## Rules and "ask once, remember"

A rule sets the level for one exact pattern: a tool, optionally with a template (`whatsapp.send_template|template=order_status`). Owners add rules that raise autonomy (`POST /api/v1/autonomy/rules`); anyone who runs tasks can add an `ask_always` rule, and "Always ask for this" on an activity entry does that for its pattern (`POST /api/v1/autonomy/actions/{id}/tighten`). Only owners delete rules. A raising rule is refused (`422`) for protected kinds, for messages without a template, for `act_silently` on a tool that is not low risk, and above the category's ceiling; a message without a template asks even if such a rule exists. Rules never lift first contact, bulk, thresholds, quiet hours, caps or guardrails.

When an owner approves the same pattern repeatedly (`proposal_after` times), ANUM records a **suggestion** (`autonomy.proposal_created` event and audit record) to let that pattern act and notify, for example "auto-send order-status replies on WhatsApp". Nothing changes until an owner accepts it (`POST /api/v1/autonomy/proposals/{id}/accept`, which creates the rule); declining keeps asking and the pattern is never suggested again. Protected kinds, unclassified (`other`) tools, free-text messages and patterns a policy keeps asking for are never counted.

## How it combines with the tool policy

1. The tool policy decides first ([order](approvals-and-risk.md#organization-approval-rules-and-policy-packs)). Autonomy never unblocks.
2. Blocked recipients and the spending limit block.
3. If the tool policy asks: autonomy may let the call run only when approval was required **solely** by the platform's high-risk default (`ToolPolicyDecision.lowerable`): never when an organization approval rule or policy pack asks, never with the workspace's two-person rule on, never for the medium-risk approval switch.
4. If the tool policy allows: autonomy may still ask (a level, a rule, a protected kind, a guardrail).

The decision is recorded on the run's `tool_proposal` step (`autonomy_mode`, `autonomy_reason`) and on the checkpoint (`checkpoint.autonomy`), and re-evaluated by the Temporal worker before execution (quiet hours starting in between make it ask).

## Activity and undo

Every call that ran on its own is recorded in `autonomy_actions` with an `autonomy.action_taken` audit record; `act_and_notify` ones also emit the `autonomy.action_taken` event (`action_id`, `task_id`, `tool`, `category`, `level`, `summary`, `undo_until` when reversible).

A tool is reversible when its definition names a compensating tool (`undo_tool`) and an `undo_window_seconds`, and its result carries the compensating tool's arguments in `output["undo"]`, for example archive email (undo: move back), pause listing (undo: resume), or a send scheduled with a short delay (undo: cancel the scheduled send). The action keeps a random undo token. `POST /api/v1/autonomy/actions/{id}/undo` with `{"undo_token"}`:

- may be called by an owner (`approval:decide`) or the person whose task did it (`403` otherwise, naming who can);
- `409` when already undone, not reversible or the token does not match, `410` after the window, `404` in another workspace;
- runs the compensating tool through the tool registry (the tool policy can still block it), and on success marks the action `undone` (`autonomy.action_undone` event and audit record). A failed undo answers `502`, keeps the action in effect and records `undo_error` and an `autonomy.undo_failed` audit record.

`GET /api/v1/autonomy/actions` lists the workspace's actions newest first, with `category`, `level`, `status` and `q` (summary, tool, template) filters and keyset paging (`limit` up to 100, `cursor`); each item says whether the caller may undo it now (`can_undo`, and `undo_token` only then) and the current rule level for its pattern. `GET /api/v1/autonomy/actions/{id}` reads one (a notification's Undo uses it).

## Clients

- Web: **Activity** (`#activity`) and **Autonomy** (`#autonomy`) in the navigation (`apps/web/src/AutonomyViews.tsx`, helpers in `src/lib/autonomy.ts`, calls in `src/lib/autonomyApi.ts`). Activity: suggestions with Accept / Keep asking (owners), filters, search, Load more, Undo while open, "Always ask for this", and the empty, filtered-empty, error and access states. Autonomy: levels per area (options above the organization ceiling disabled), the always-ask list, thresholds, approved templates, daily caps, quiet hours, blocked lists, rules (add, remove), and the organization guardrails (editable only by organization administrators; read-only with the reason otherwise). An `autonomy.action_taken` event shows an "ANUM acted on its own" notification with Undo and View activity. English and Arabic catalogs.
- Flutter: Settings › Autonomy › **Activity** and **Autonomy levels** (`apps/mobile/lib/features/autonomy/`, English and Arabic): the feed with category filter, search, paging, Undo, "Always ask for this" and suggestions. **Autonomy levels** edits, for owners (read-only with the reason for others), the levels per area, the recipients, amount and price change thresholds, `proposal_after`, quiet hours (start, end, IANA time zone), daily caps (an area or tool name, 0 to 100000, add and remove rows), blocked recipients and domains (normalized like the server, searchable past 20 entries) and approved templates; a save keeps stored fields the phone does not edit, and a `422` "Above the organization's guardrails" answer is shown as the API sent it. Two screens open from it: **Rules** (`GET/POST /rules`, `DELETE /rules/{id}`; owners add any level and remove, anyone who runs tasks adds "always ask" rules, the server's refusals are shown verbatim, search and Show more past 20) and **Organization guardrails** (`GET/PUT /guardrails`: spending limit, highest amount, recipients and price change thresholds, level ceilings, quiet hours, daily caps, blocked lists), editable only when the overview says `can_manage_guardrails`, read-only with the reason otherwise. Tests: `test/autonomy_policy_test.dart` (main paths, refused saves, read-only members, every state, Arabic, 320 to 1440 wide at 200% text).

## Storage

Migration `0034_autonomy` (expand only): `autonomy_guardrails` (one row per tenant, JSON settings), `workspace_autonomy_settings`, `autonomy_rules` and `autonomy_proposals` (unique per pattern), `autonomy_actions` (feed index on tenant, workspace, created_at, id) and `autonomy_recipients`. Every table has forced RLS on `anum.tenant_id` (and `anum.workspace_id` for the workspace tables); nothing is granted to a cross-tenant role. The in-memory backend keeps the same data in process. Every change is audited in the same transaction (`autonomy.settings_updated`, `autonomy.guardrails_updated`, `autonomy.rule_created`, `autonomy.rule_deleted`, `autonomy.proposal_*`, `autonomy.action_*`, `autonomy.undo_failed`).

## Tests

`services/api/tests/test_autonomy.py` (evaluation, floors, thresholds, quiet hours, caps, classification, connector facts, free text, policy combination, runtime inline and durable, proposals, undo, feed, tighten, permissions), `test_whatsapp.py` (the auto-send list as approved templates, first contact, quiet hours, caps, blocked numbers, the medium-risk switch and the category level), `test_email.py` (declared kinds, archive and mark-read acting with Undo at Gmail and Zoho, no Undo for Outlook, quiet hours, the answered sender being known while free text asks), `test_postgres_autonomy.py` (the whole loop as the app role, RLS isolation between workspaces and tenants, check constraints), `test_migrations.py`; web `test/autonomy.test.ts` and `e2e/autonomy.spec.ts` (including the five responsive-gate sizes), the accessibility gate for both views; Flutter `test/autonomy_test.dart` (models, controllers, screens at 320 to 1440 wide and 200% text).

## Not built yet

- The commerce connector tools live on their own branch; they become autonomous once their `ToolDefinition`s declare `category`, `action_kind` and, where reversible, `undo_tool` and `undo_window_seconds` with `output["undo"]`.
- An Outlook archive cannot be undone from ANUM (the moved message gets a new Graph id that ANUM does not track); Gmail and Zoho Mail archives can.
- A person's own clicks in the email inbox (mark read, archive) go through the same policy but are the person's actions, so they are audited, not listed in the autonomy activity feed.
- Push delivery of the notification to phones (the event reaches the web; the Flutter app shows the feed).
- The phone has no tool picker for rules: the tool name is typed as shown on an activity entry (the API refuses unknown names), because there is no route that lists the available tools.
