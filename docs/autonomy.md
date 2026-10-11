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
| `delete`, `price_change`, `stock_change`, `payment`, `merge_code` | **always ask** (protected) |

A tool declares its kind and category on its `ToolDefinition` (`category`, `action_kind`). Undeclared values are guessed from the tool name (`gmail.send_message` is email/send, `noon.update_price` is commerce/price change), cautiously: a medium or high-risk tool whose name merely sounds like a read is `other` and asks. Connector tools on other branches should declare both.

## Always asks

Whatever the level, a rule or the approval history, these always ask:

- money, prices, stock, deleting, merging code (the protected kinds);
- **first contact** with a recipient not contacted before in this category, and a `send` whose recipients ANUM cannot read from the arguments;
- **bulk** sends above the workspace's recipients threshold (default 5);
- an amount above the workspace's amount threshold (default 0) or a price change above its percentage threshold (default 0);
- free-text messages (a `reply` or `send` without a template, unless an owner made a rule for that exact pattern with a template).

Recipients become known when a call to them completes, approved or autonomous. They are stored only as SHA-256 digests of tenant, workspace, category and the normalized address (`autonomy_recipients`), never the address itself.

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

A workspace (`PUT /api/v1/autonomy/settings`, owners) may be stricter but never looser: a setting above a floor answers `422` naming each problem; a floor lowered later caps the stored value at evaluation. Workspaces add their own caps, quiet hours and blocked lists (the union applies), approved templates and `proposal_after` (approvals before a suggestion, default 3).

## Rules and "ask once, remember"

A rule sets the level for one exact pattern: a tool, optionally with a template (`whatsapp.send_template|template=order_status`). Owners add rules that raise autonomy (`POST /api/v1/autonomy/rules`); anyone who runs tasks can add an `ask_always` rule, and "Always ask for this" on an activity entry does that for its pattern (`POST /api/v1/autonomy/actions/{id}/tighten`). Only owners delete rules. A raising rule is refused (`422`) for protected kinds, for messages without a template, for `act_silently` on a tool that is not low risk, and above the category's ceiling. Rules never lift first contact, bulk, thresholds, quiet hours, caps or guardrails.

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
- Flutter: Settings › Autonomy › **Activity** and **Autonomy levels** (`apps/mobile/lib/features/autonomy/`): the feed with category filter, search, paging, Undo, "Always ask for this" and suggestions; levels per area, recipients and amount thresholds and approved templates for owners, read-only for others. Quiet hours, caps, blocked lists and guardrails are edited on the web.

## Storage

Migration `0034_autonomy` (expand only): `autonomy_guardrails` (one row per tenant, JSON settings), `workspace_autonomy_settings`, `autonomy_rules` and `autonomy_proposals` (unique per pattern), `autonomy_actions` (feed index on tenant, workspace, created_at, id) and `autonomy_recipients`. Every table has forced RLS on `anum.tenant_id` (and `anum.workspace_id` for the workspace tables); nothing is granted to a cross-tenant role. The in-memory backend keeps the same data in process. Every change is audited in the same transaction (`autonomy.settings_updated`, `autonomy.guardrails_updated`, `autonomy.rule_created`, `autonomy.rule_deleted`, `autonomy.proposal_*`, `autonomy.action_*`, `autonomy.undo_failed`).

## Tests

`services/api/tests/test_autonomy.py` (evaluation, floors, thresholds, quiet hours, caps, classification, policy combination, runtime inline and durable, proposals, undo, feed, tighten, permissions), `test_postgres_autonomy.py` (the whole loop as the app role, RLS isolation between workspaces and tenants, check constraints), `test_migrations.py`; web `test/autonomy.test.ts` and `e2e/autonomy.spec.ts` (including the five responsive-gate sizes), the accessibility gate for both views; Flutter `test/autonomy_test.dart` (models, controllers, screens at 320 to 1440 wide and 200% text).

## Not built yet

- The email, WhatsApp and commerce connector tools live on their own branches; they become autonomous once their `ToolDefinition`s declare `category`, `action_kind` and, where reversible, `undo_tool` and `undo_window_seconds` with `output["undo"]`.
- Push delivery of the notification to phones (the event reaches the web; the Flutter app shows the feed).
- The Flutter client does not edit quiet hours, caps, blocked lists or organization guardrails, and its autonomy strings are English only.
