# Email campaigns (Brevo)

ANUM connects a workspace to the owner's [Brevo](https://www.brevo.com) account so the team can read contact lists, senders and campaign reports, write campaign drafts, and send or schedule them. **Nothing reaches a contact without an owner's approval** bound to the exact draft. Claude agents use the same capabilities as mediated tools under the runtime's tool policy.

Code: `services/api/anum_api/brevo/` (API, client, tools), `services/api/anum_api/db/brevo_repository.py` (PostgreSQL), migration `0074_brevo_campaigns`, `apps/web/src/BrevoCampaigns.tsx` (web, Marketing's **Email campaigns** tab), `apps/mobile/lib/features/brevo/` (Flutter, read-only).

## How ANUM talks to Brevo

- Brevo API v3 over REST with `httpx` (no Brevo SDK). The base URL is fixed: `https://api.brevo.com/v3`; paths are constants with integer ids only and redirects are not followed (SSRF, threat model G1).
- The key is sent only in the `api-key` header. Each call has a 15 second timeout, asks for an uncompressed answer and reads at most 2 MB; a larger, compressed or redirected answer fails with a plain message. Errors never carry the key, headers or body.
- Brevo's answers (list names, campaign names, subjects) are untrusted data: parsers keep only the fields ANUM shows and cut every string; the web and the app show them as text, never as HTML; tools return them with provenance (`tool_output`, origin `api.brevo.com`) and they reach a model only through `tool_output_prompt_block` (`prompt_provenance.label_untrusted`).

Endpoints used: `GET /smtp/templates?templateStatus=true` (the planner's email layout), `GET /account` (check the key, the account's own address and plan), `GET /contacts/lists`, `GET /contacts/lists/{id}`, `GET /senders`, `GET /emailCampaigns?statistics=globalStats`, `GET /emailCampaigns/{id}` (no statistics filter, so the answer has the global and per-list numbers), `POST /emailCampaigns`, `PUT /emailCampaigns/{id}`, `POST /emailCampaigns/{id}/sendNow`, `POST /emailCampaigns/{id}/sendTest`.

## Connection (owners)

The owner creates an API key in Brevo (their name, **SMTP & API**, **API keys**, **Generate a new API key**; it starts with `xkeysib-`) and pastes it **only** in the web form (Marketing, Email campaigns, Connection). An SMTP key (`xsmtpsib-`) is refused with that explanation.

- `PUT /api/v1/brevo/connection {api_key}` calls `GET /v3/account` first; only a key Brevo accepts is kept, as a Fernet ciphertext in `brevo_connections.api_key_ciphertext` (`ANUM_SECRETS_KEY`). It is never returned, logged or audited: views and audit records show its last four characters.
- `POST /connection/check` calls `GET /v3/account` again. A refused key marks the connection `needs_key` (with Brevo's reason and the fix); other failures `error`. Any read that Brevo refuses for the key does the same.
- `DELETE /connection` forgets the key. Drafts stay; campaigns already in Brevo are not touched.
- `python -m anum_api.rotate_secrets` re-encrypts the key (reported as `brevo_connections`); discovery runs as `anum_maintenance`, which may read only `tenant_id` and `workspace_id` of the table.

## Reading

- `GET /status`: connected or not, the connection state, the account's address, company and plan, and what the caller may do.
- `GET /lists?limit&offset`, `GET /senders`: from Brevo, paged by Brevo (at most 50).
- `GET /campaigns?limit&offset&status`: campaigns with sent, delivered, unique opens and clicks, unsubscribes, bounces, complaints and the open and click rates over delivered. `GET /campaigns/{id}`: one campaign's report, with the numbers per list.

## Drafts

A draft is written in ANUM: name, subject, preview text, a sender, up to 10 contact lists and the HTML (up to 512 KB). `POST /drafts` and `PUT /drafts/{id}` check with Brevo that the sender is one of the account's **verified, active** senders and that every list exists. Saving creates nothing in Brevo and sends nothing.

- The HTML goes to Brevo exactly as written. The detail read (`GET /drafts/{id}`) also returns a sanitized preview (`mailbox/html_safe.py`: no scripts, handlers or remote images) that the web shows in a sandboxed frame under a `default-src 'none'` policy.
- `content_hash` binds the name, subject, preview text, sender, HTML and lists.
- Editing a draft that waits for approval retires that approval first and returns it to draft. Drafts the assistant wrote say so (`source: assistant`).
- `GET /drafts?q&status&limit&offset` searches names and subjects; `DELETE /drafts/{id}` deletes from ANUM (not while sending or scheduled, and not while `unknown`: that draft is the record of a send that may have gone out).

## Test send (owners, no approval)

`POST /drafts/{id}/test` sends the draft only to **the Brevo account's own address** (from `GET /account`; it cannot be chosen), so it reaches no contact and needs no approval. The draft's one Brevo campaign is created on the first test (a Brevo draft, which sends nothing), its id recorded before the test is sent, and updated on later tests; the approved send updates and sends that same campaign.

## Sending and scheduling (always an owner's approval)

1. `POST /drafts/{id}/send {scheduled_at?}` (members and owners; a request without `scheduled_at` reuses the time saved on the draft, an explicit `null` means as soon as approved) reads the lists and the sender from Brevo again and pauses a `brevo.send_campaign` call for approval. The approver sees the campaign, subject, preview text, sender, lists, how many contacts they hold, when, the Brevo account and the text of the email; the payload hash binds all of it plus the content hash. A scheduled time must be 10 minutes to one year away.
2. An owner approves with the payload hash shown (`POST /api/v1/approvals/{id}/approve`). The tool is `always_ask` and of the protected `publish` kind: workspace autonomy can never let it run without a person.
3. At execution the tool checks again, in one short transaction, that the draft still hashes to the approved content and has the approved lists, sender and time, and claims the send (`sending`). Then, with no transaction open, it checks with Brevo that the sender is still verified and active and every list still exists, creates (or updates) the draft's one Brevo campaign with the approved content (preview text included as `previewText`) and the tag `anum:<draft id>`, records the campaign id at once, and calls `sendNow` or leaves Brevo to send at `scheduledAt`.
4. **One Brevo campaign per draft**: the id is stored as soon as Brevo returns it and a partial unique index allows one draft per campaign in a workspace. A later attempt reuses that campaign (and, after an interrupted first attempt, finds it by its tag). Running the same approved call again sends nothing more.
5. Failures say what to fix and are never retried blindly: a refusal leaves the draft `failed` (ask again after fixing it); a timeout while creating the campaign, while updating it with a schedule, or on `sendNow` leaves it `unknown`, which cannot be edited, sent again or deleted ("check this campaign in Brevo"). A claim older than 15 minutes is shown as interrupted.

A rejected or lapsed approval returns the draft to draft with the reason; `POST /drafts/{id}/cancel` withdraws a request.

## Agent tools

Registered in the API's and the Temporal worker's tool registries, under `ToolPolicy`, with `target` `api.brevo.com` and category `marketing`:

| Tool | Risk | Kind | What it does |
| --- | --- | --- | --- |
| `brevo.list_lists` | low | read | Contact lists with subscriber counts. |
| `brevo.list_campaigns` | low | read | Recent campaigns with their numbers. |
| `brevo.campaign_report` | low | read | One campaign's report. |
| `brevo.draft_campaign` | low | draft | Writes a draft in ANUM (`source: assistant`); nothing in Brevo, nothing sent. Owners and members. Takes `list_ids` and `html`, or the planner's `list_names`, `outline`, `brief`, `language` and `schedule`, and then writes the full email (below). |
| `brevo.send_campaign` | high | publish, `always_ask` | Sends or schedules one draft after an owner's approval, checked again as above. |

The read tools declare an input schema, so the voice assistant's grounded lookups can use them; every result carries its provenance and is labelled untrusted before any prompt.

## Planner

The `anum.brevo` skill (`agent_skills.py`) is selected by the words Brevo, newsletter(s), mailing, mailshot or subscribers (and نشرة, المشتركين), only where the four tools below are registered and, for an agent, allowed. `brevo/planning.py` then turns the user's own prompt (never retrieved text or model output, threat model G5) into one call, deterministically, so a task, chat and the command preview agree (`choose_tool_call`, hooked before the marketing planner in `AgentPlanner.plan`):

| Request | Planned call |
| --- | --- |
| "send the winter newsletter to the customers list" (draft, write, prepare, compose, create, send, schedule) | `brevo.draft_campaign` with the name ("Winter newsletter"), subject, preview text, a four-part outline, the user's words as `brief`, `language`, `list_names` and `schedule` |
| "أرسل نشرة الشتاء إلى قائمة العملاء غدا الساعة 9 صباحا" (أرسل, اكتب, جهز, جدول) | the same in Arabic: "نشرة الشتاء", lists `["العملاء"]`, tomorrow 09:00 UTC |
| "how did newsletter campaign 101 perform?" | `brevo.campaign_report {campaign_id: 101}` |
| "show the Brevo campaign reports" | `brevo.list_campaigns {status: sent}` |
| "how many subscribers are on our lists?" | `brevo.list_lists` |
| A draft request that names no list | `anum.respond` asking which list |
| "send brevodraft_..." | `anum.respond` pointing to Ask to send on the Email campaigns page |

- **The planner never plans `brevo.send_campaign`.** A request to "send" becomes a draft for review; the send is asked for on the page and waits for an owner's approval bound to the payload hash, as above. The send tool stays `always_ask`, so workspace autonomy cannot let it run.
- Every planned call goes through the runtime's `ToolPolicy` like any other: the reads are low risk and allowed, the draft needs an owner or member.
- `list_names` are resolved at execution against the account's lists (the first 50): one exact match per name, ignoring case, a trailing "s" and the Arabic article (so "عملاء" finds "العملاء"); anything else fails with "No single Brevo contact list is named ..." and nothing is written.
- **Lists in English and Arabic:** "to the customers and VIP lists", "the list named Trade Partners"; "إلى قائمة العملاء", "لقائمتي العملاء والموزعين" (a name ends at a time word or a number, and "و" starts the next one).
- **Dates in English and Arabic** (UTC): "tomorrow at 9am", "today at 6pm", "2026-11-02 14:30", "on 2026-12-01 at 08:15"; "غدا الساعة 9 صباحا", "اليوم الساعة 7 مساء", "بتاريخ 2026-11-02 الساعة 14:30", with Arabic-Indic digits too. Tomorrow without a time is 09:00. Anything else, a past time or one less than 10 minutes away is "as soon as approved".
- **The schedule is saved on the draft** (`scheduled_at`, the column from 0074; no new migration) when it is 10 minutes to a year away. Asking to send reuses it (the web pre-fills it; an API request without `scheduled_at` takes it), the approval binds it, and withdrawing a request keeps it.

### The full email

At execution `brevo.draft_campaign` writes the complete email (`brevo/compose.py`), still only a draft that an owner approves before anything is sent:

- **Brand:** the organisation's display name (Settings, Branding; `organisations.branding_for`) heads and signs it; Marketing's brand voice and the user's learned style steer the wording.
- **Logo:** when the organisation has a logo and the API has a public address (`ANUM_MARKETING_PUBLIC_API_URL`), the header shows it (`<img>`, 160 px wide, alt text "<name> logo" or "شعار <name>"). The address is public and content-addressed, `/api/v1/public/logos/{org_public_id}/{sha256}.png` ([public logos](customer-organisations.md#public-logos)): no secret is involved, so it survives `ANUM_SECRETS_KEY` rotation, and a new logo gets a new address while old emails keep showing the old one. An owner's template places the logo with the marker `{{ params.anum_logo }}`. Without a logo or a public address, the brand name heads the built-in layout and the marker is removed.
- **The owner's Brevo template, when there is one:** the newest active template (one tagged `anum` first) that contains the marker `{{ params.anum_content }}` is the layout; the body replaces the marker and the rest stays as the owner wrote it. Templates without the marker are not used, so the owner chooses where the body goes. Without one, a table-based layout (600 px, the brand name, hidden preview text, the subject, the sections and Brevo's `{{ unsubscribe }}` link) is used; Arabic drafts are right to left.
- **The words:** with a connected model, through the workspace's budgeted gateway, the model writes each section from the user's own words and the brand voice as JSON plain text; every heading, paragraph and button label is escaped before it goes into the HTML. Without a model, or when its answer cannot be read, the sections come from English or Arabic templates and the tool says to edit them. Template HTML from Brevo never reaches a model, and nothing remote is added except the logo link above (no scripts). The button's address is left as `#` for the reviewer to set.
- The tool's answer says which (`email: {generator, brevo_template_id, brand}`).

- **Not configured:** before a Brevo call reaches the runtime, `AgentPlanner.plan` reads the connection in its own thread and short unit of work (`brevo.planning.refusal`). With no connection, or a key Brevo refused, the plan is an `anum.respond` that says nothing was drafted or sent and how an owner fixes it. The tools refuse the same way if called directly.

Tests: `tests/test_brevo_planning.py` (intent to plan in English and Arabic, list names and dates in both, the full email from the brand, the owner's Brevo template and a model with its text escaped, the saved schedule reused when asking to send, reads as allowed tool calls, never a send, a planned draft that sends nothing and whose send waits for an owner's approval with the right payload hash, list names that must exist, refusal when Brevo is not connected or its key was refused).

## Permissions

| Action | Viewer | Member | Owner |
| --- | --- | --- | --- |
| See the connection state, lists, senders, campaigns, reports and drafts | yes | yes | yes |
| Write, edit and delete drafts; ask to send; withdraw a request | no | yes | yes |
| Paste, check or remove the API key; send a test | no | no | yes |
| Approve a send (`approval:decide`) | no | no | yes |

The marketing permission keys apply (`marketing:read`, `marketing:write`, `marketing:manage`).

## Audit

Every state-changing route writes an audit record without the key, the HTML or any recipient: `brevo.connection.saved|checked|removed`, `brevo.draft.created|updated|deleted|test_sent`, `brevo.campaign.send_requested|send_withdrawn|sent|scheduled|send_failed` (with the approval id, content hash and Brevo campaign id). `tests/test_brevo.py` walks every route through `audit_probe.AuditProbe`.

## Sending domains and DNS

Brevo authenticates each sending domain with its own DKIM record and a `brevo-code` TXT record, and the domain's SPF record must include Brevo. ANUM never changes DNS; see [Email](email.md#sending-domains-for-brevo) for the owner's current records and what to change.

## Data

Migration `0074_brevo_campaigns`: `brevo_connections` (one per workspace) and `brevo_campaign_drafts`, both with forced RLS on `anum.tenant_id` and `anum.workspace_id`; a column grant of `tenant_id, workspace_id` on `brevo_connections` to `anum_maintenance` for key rotation. Expand only. Tests: `tests/test_brevo.py` (Brevo as an `httpx.MockTransport`), `tests/test_postgres_brevo.py` (RLS isolation, the main path, one draft per campaign, maintenance discovery, key rotation), `apps/web/test/brevo.test.ts`, `apps/web/e2e/brevo.spec.ts` (English and Arabic, axe), `apps/mobile/test/brevo/brevo_test.dart` (320 to 1440 px, 200% text, Arabic).

## Not done yet

- The phone reads campaigns and reports only; drafting, tests and sending are on the web.
- The planner does not plan sends (by design: an owner approves each one). The draft preview in ANUM hides remote images, so the logo shows in the test send and in Brevo, not in the preview frame.
- Contacts, list membership, templates and Brevo's own drag-and-drop editor are not managed from ANUM; write the HTML in Brevo's editor (or elsewhere) and paste it.
- Brevo webhooks (opens, bounces in real time) are not received; numbers are read when the page or a tool asks.
