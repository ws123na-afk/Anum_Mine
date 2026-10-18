# Business: the internal sales desk

Business is roadmap P1 "Internal sales MVP" ([ANUM OS business and product roadmap](ANUM_OS_Business_and_Product_Master_Roadmap.md), sections 4B, 6, 7 P1 and 8 Sales). It is where the owner keeps what they sell, who they want to sell it to, what they know about those companies, the deals in progress, the proposals and the projects that follow. It runs inside the owner's own workspace; nothing in it is seeded and nothing in it sends a message.

- API: `services/api/anum_api/business/` under `/api/v1/business`.
- Storage: migrations `0070_business` (16 tables) and `0077_business_change_orders` (change orders and the usage ledger), memory store for local runs and tests.
- Web: Business in the main navigation (`apps/web/src/BusinessView.tsx` and its tabs), English and Arabic.
- Flutter: Business in the menu (`apps/mobile/lib/features/business/`), English and Arabic.
- Contracts: `packages/contracts/src/business.ts`.

## What it does

| Area | What a person can do | Rules the server enforces |
|---|---|---|
| Catalog | Add products and service packages, their maturity (idea to retired) and priced versions with deliverables, limitations and cost assumptions (labor hours, hourly cost, external costs, contingency) | Only a draft version changes; activating a version retires the previous active one (one active version per product); the estimated cost and the margin at the price are computed, never guessed |
| Accounts and contacts | Companies with website, industry and place; contacts with a consent basis | One account per website domain and one contact per e-mail address in a workspace; consent `unknown` stays unknown and blocks recording a contact |
| Leads | The lead state machine, the six-criterion score, evidence, inferences, notes and the activity trail | See [lead states](#lead-states), [the score](#lead-score) and [evidence and inferences](#evidence-and-inferences) |
| Research | Run the research skill on one lead | See [research](#research) |
| Drafts | Write e-mail, WhatsApp, LinkedIn or call-script drafts in English or Arabic, edit them and approve the text | See [drafts](#drafts-sending-is-off) |
| CSV import | Import companies with a dry run first | Each row is checked for duplicates, the opt-out list and invalid values before anything is added |
| Pipeline | Opportunities from qualified leads, with value and probability as the owner's assumptions | A won stage only comes from an accepted proposal; lost needs a reason and withdraws its open proposals |
| Proposals | Versioned proposals with scope, deliverables, exclusions, assumptions, acceptance criteria, priced lines, tax, internal cost, milestones, payment terms and a Markdown export | See [proposal acceptance](#proposal-acceptance) |
| Projects | A project from an accepted proposal, its milestones with status, acceptance and payment received | One project per proposal; milestone titles and amounts come from the accepted version (or a version an approved change order produced) |
| Change orders | Request a change to an accepted contract: the scope delta as line items, the price and timeline impact, the internal cost change; send it to an owner | See [change orders](#change-orders) |
| Usage and costs | Labour hours and provider costs per client account and project (AI usage from the model budget, hosting share, messaging, other), billable or not; the project's profitability | See [usage ledger and profitability](#usage-ledger-and-profitability) |
| KPI summary | The owner's funnel and money figures for a window of days | See [KPI summary](#kpi-summary) |

## Permissions

Defined in `anum_api/authorization.py`.

| Role | `business:read` | `business:write` | `business:manage` | Decide acceptance |
|---|---|---|---|---|
| Owner | yes | yes | yes | yes, in Approvals (`approval:decide`); change orders in the project |
| Member | yes | yes | no | no (members draft and send change orders) |
| Viewer | no | no | no | no |

Viewers are refused because the desk holds contacts' personal data; the API's 403 says who can use it, and the web and Flutter screens show that sentence. `business:manage` covers the catalog, the research caps, deletions and clearing a do-not-contact mark.

## Lead states

`discovered`, `researched`, `qualified`, `contacted`, `replied`, `meeting`, `offered`, `won`, `lost`, `suppressed`. The allowed moves are `LEAD_TRANSITIONS` in `anum_api/business/models.py`. The first time a lead reaches a state, the time is stamped (`qualified_at`, `contacted_at` and so on); the funnel reads those stamps.

- **Qualified** needs an industry fit score, a problem evidence score (linked to evidence when above 0) and at least one evidence record.
- **Contacted, replied, meeting** are records of what a person did outside ANUM. They are refused while the account or contact is marked do not contact or the contact is on the tenant's opt-out list; contacted also needs a consent basis other than unknown.
- **Offered** is set when a proposal is sent for acceptance; **won** only when that proposal is accepted.
- **Lost** and **suppressed** need a reason. Leaving suppressed or won needs an owner, and leaving suppressed is refused while the contact is still blocked.
- At most one open lead per account and offer (a partial unique index backs the check).

## Lead score

Six criteria, 0 to 5 each, out of 30 (roadmap 3.2): industry fit, problem evidence, official channel, project scale, urgency confirmed and authorized buyer. A criterion with no score is unknown: it adds nothing and the lead shows "n of 6 known". Every score needs a reason and may link only the lead's own evidence. Urgency and the buyer's authority can only be confirmed in a conversation: research never suggests them.

## Evidence and inferences

Evidence is a public source fact: URL, title, published date (unknown unless shown), excerpt, confidence and where it came from. Inferences are conclusions drawn from evidence, kept in their own table with the evidence they rest on, who or what wrote them (person, template or model) and a review status (proposed, accepted, rejected). An inference without a source is not stored. Removing a source removes it from the scores and inferences that cited it.

## Research

`POST /leads/{id}/research` runs the research skill (`anum_api/business/research.py`), the roadmap's find, profile, score, draft loop:

1. **Find**: up to three searches built only from the lead's own record (company name, domain, industry, place, offer), each a `web_search` tool call under the same tool policy as an agent (governance rules can block it or ask for approval) and audited by the runtime.
2. **Profile**: new results become evidence with a confidence from whether the page is on the company's own domain. Result text is untrusted data: stored and shown as data, and given to a model only inside labelled untrusted blocks (`prompt_provenance`).
3. **Score**: proposed inferences that must cite the results (from the model through the workspace's budgeted gateway, or a template without a model). Research never sets a score.
4. **Draft**: a fit explanation for review that lists what is still unknown.

Caps per workspace and UTC day (Business, Settings; owners): research runs (default 10) and web searches (default 30). Model calls also count against the workspace's monthly model budget. Without `ANUM_SEARXNG_URL` (or with web search switched off under Integrations) research answers 503 and says to add sources by hand.

## Drafts: sending is off

Drafts are written from the catalog's own words and the recorded sources, by the model or a template, and checked deterministically before a person can approve the text: the channel's length limit, a subject for e-mail, an opt-out line (reply STOP or إيقاف), no guarantees, no placeholders, no figure that is not in the catalog or the sources, and no long dashes. Approving records the exact text's hash and the person; editing returns it to draft. Drafts are refused for blocked contacts and closed leads.

There is no send route, queue or delivery tool in Business; `GET /setup` reports `sending_enabled: false` and a test fails if a route under `/api/v1/business` names a send or an outbox. A person sends an approved draft themselves through an authorized channel and records it as an activity.

## Do not contact and the opt-out list

Marking a contact do not contact (`POST /contacts/{id}/do-not-contact`) adds their e-mail and phone to the tenant's [contact suppression list](privacy.md#contact-suppression) for every channel and purpose, and suppresses their open leads. Contacts already on that list show the masked entry and block contact records and drafts. Clearing the mark is for owners and does not remove the list entry; an owner removes that under Contact opt-outs. The CSV import adds contacts found on the list as suppressed leads.

## Proposal acceptance

A proposal has versions; each version has a SHA-256 content hash over its canonical JSON. Totals are reconciled in `Decimal`: subtotal, tax (tentative until the jurisdiction is verified), total, external costs, margin against the internal cost estimate, and milestones that must add up to the total.

1. `POST /proposals/{id}/request-acceptance` checks the version (line items, deliverables, acceptance criteria, milestone sum, total above zero), locks it and asks for an approval of the `business.accept_proposal` tool (high risk, always asks). The approval shows the client, version, totals, milestones, deliverables and exclusions and is bound to them by the payload hash.
2. An owner approves in Approvals (or in the proposal on the web) with the payload hash they were shown.
3. The tool runs only if the proposal still has that version, that content hash and that total; otherwise nothing is accepted. It marks the version accepted with who and when, wins the opportunity and the lead, and records the activity once.

A locked version is never edited: a change creates a new version and lapses the waiting approval (an old approval then answers 410). A database trigger refuses any update of a locked version. A rejected or lapsed approval returns the proposal to draft with the reason. An accepted proposal never changes; its accepted version is the contract record and the Markdown export uses it.

## Change orders

Roadmap 4.B.4 and P2. A change order amends the contract of a project, which only exists for an accepted proposal (`anum_api/business/changes.py`, routes in `anum_api/business/api_changes.py`).

| Route | Who | What it does |
|---|---|---|
| `GET /projects/{id}/change-orders`, `GET /change-orders/{id}` | readers | The project's change orders, newest first; an open one shows its computed price impact against the current contract, or why it cannot apply |
| `POST /projects/{id}/change-orders` | members, owners | A draft: title, the request, who asked, lines (`add`, or `remove` matching a line of the current version exactly on description, kind, quantity and price), the timeline change in days (minus for sooner), the internal cost change |
| `PATCH /change-orders/{id}` | members, owners | Only a draft changes |
| `POST /change-orders/{id}/submit` | members, owners | Binds the change order to the current contract version and its hash, computes the deltas and its own content hash, and waits for an owner. A stale one (the contract changed) is sent again the same way |
| `POST /change-orders/{id}/approve` `{content_hash}` | owners (`approval:decide`) | Approves the exact change that was shown |
| `POST /change-orders/{id}/reject` `{reason}` | owners | Rejected, with the reason |
| `POST /change-orders/{id}/withdraw` `{reason}` | members, owners | A draft or a waiting change order is withdrawn |

Statuses: `draft`, `pending_approval` (sent for approval), `approved`, `rejected`, `withdrawn`. A closed project takes no new change orders.

Approving is an authenticated owner action and checks, in one transaction, that the change order still names the project's current version and hash, that the content hash is the one the owner sent, and that the recomputed deltas equal the ones that were shown; otherwise nothing changes (409) and the change order is sent again. Then:

1. A new proposal version is written, locked, with `change_order_id` set: the current contract with the lines added or removed, the internal cost changed and the milestones adjusted. The version it amends and the accepted version are never edited (the locked-version trigger refuses any update; a new version is a new row).
2. Milestones: an increase adds a milestone "Change order n: title" for the total delta, due the timeline change after the last milestone; a decrease comes out of the latest milestones not yet accepted, submitted or paid, and is refused when it is larger than those.
3. The proposal's `current_version`, the project's `proposal_version` and `contract_total` move to the new version; the proposal stays accepted with its original `accepted_version` and `accepted_hash` (the client's acceptance record). The Markdown export shows the current contract and says which accepted version it amends.
4. The activity trail and the audit log record it (ids, numbers and hashes only).

Money uses `models.money` (two places, half up) like proposals. The deltas are the difference between the totals of the two versions (`proposal_totals`), so the accepted price plus every approved change order always equals the current contract; `tests/test_business_change_orders.py` checks it with tax and odd cents.

## Usage ledger and profitability

`business_usage_ledger` holds one row per cost: labour (hours times hourly cost), AI usage, hosting share, messaging or other (quantity times unit cost), for a client account and optionally one of its projects, billable or not, in SAR, AED or USD. The amount is computed by the server. Nothing in it is an invoice or a payment.

- `GET /usage?project_id=&account_id=&kind=&billable=&q=` pages the entries; `POST /usage` adds one (members, owners); `PATCH /usage/{id}` corrects one; `DELETE /usage/{id}` is for owners.
- `POST /usage/import-ai {project_id, month, share_pct, billable}` reads one finished month of this workspace's estimated model spend from the model budget (`anum_api/model_budget.py`, USD) and records the share the owner attributes to the project. Each month is imported once per project (a partial unique index backs it); the current month is refused while it is still growing, and a month without usage is refused rather than recorded as zero. An imported row keeps the budget's figures; only its description and billable flag change.
- `GET /projects/{id}/profitability`: revenue is the accepted proposal plus approved change orders, before tax (with the accepted part and the change orders part shown apart, and `reconciled` true when they add up to the current contract); tax and the contract total; cash collected is only the milestone payments a person recorded, with what is not yet collected; labour hours and cost, direct costs by kind, billable and non-billable costs; gross margin and its percentage. Costs in another currency are listed apart and never converted. A figure with nothing behind it is null and shows "-" (no payments recorded, no costs recorded, no margin without costs).

On the web these are three blocks of the project drawer (`apps/web/src/BusinessProjectMoney.tsx`); on the phone, "Change orders and costs" on a project card (`apps/mobile/lib/features/business/project_money.dart`), where a change order has one added line and a timeline change (removing a line is on the web).

## KPI summary

`GET /kpi?days=` (7 to 366 days, ending now or at `to`):

- **Funnel**: of the leads created in the window, how many reached qualified, contacted, meeting, offered and won by now, each with its denominator (`reached of created`) and the rate from the previous step.
- **Pipeline**: open opportunities' assumed values and the weighted values, per currency, labelled as assumptions.
- **Contracted**: totals of proposals accepted in the window. **Cash collected**: milestone payments recorded with a date in the window. Neither is called revenue.
- Won and lost opportunities, proposals sent and accepted, research runs and searches in the window. When there are more than 5000 records the summary says it covers the newest.

## Storage and isolation

Migration `0070_business` creates `business_settings`, `business_products`, `business_offers`, `business_accounts`, `business_contacts`, `business_leads`, `business_evidence`, `business_inferences`, `business_activities`, `business_opportunities`, `business_proposals`, `business_proposal_versions`, `business_projects`, `business_milestones`, `business_drafts` and `business_research_runs`.

Migration `0077_business_change_orders` adds `business_change_orders` and `business_usage_ledger` under the same rules, and one nullable column, `business_proposal_versions.change_order_id`. Check constraints keep a sent or decided change order bound to a version and hash, and an approved one naming who approved it and the version it produced. Numbered 0077 after `0074_brevo_campaigns`; parallel branches use 0075 and 0076, and the chain is renumbered at merge.

- Every table has `tenant_id` and `workspace_id`, a foreign key to `workspaces` and forced row level security with `tenant_isolation_<table>` on `anum.tenant_id` and `anum.workspace_id` (the 0040 pattern). No role is granted anything and no discovery role reads these tables.
- References between business tables are composite foreign keys on `(tenant_id, workspace_id, id)`, so a row can never point into another tenant or workspace by a guessed id, even though foreign key checks ignore row policies.
- Every store query also filters on the caller's scope; a record of another workspace reads as missing (404).
- Activities are idempotent per key (`Idempotency-Key` on `POST /leads/{id}/activities`).
- Expand only: new tables, a trigger function and indexes. Numbered 0070 and chained after `0069_mail_writers_uidvalidity`, the head of `main`.

Every state-changing route writes an audit record with ids and counts only (never contact details or message text); `tests/test_business.py` walks all of them through `audit_probe.AuditProbe`.

## Tests

- `services/api/tests/test_business.py`: rules, permissions, catalog, dedup, the state machine, the score, evidence and inferences, do not contact and the opt-out list, idempotent activities, paging and search, cross-workspace and cross-tenant reads, CSV import, research with a fake `web_search` (caps, closed leads, a model that tries to cite missing sources or claim the buyer's authority, injected text kept as data), drafts and their checks, the main path, approval binding (a changed proposal cannot be accepted with the old approval, a tampered total is refused), rejection, lost opportunities and the audit walk.
- `services/api/tests/test_postgres_business.py`: the main path under forced RLS as the app role, tenant B and the other workspace cannot read or guess ids, row policies refuse writes into another scope, scoped foreign keys refuse cross-scope references, unique indexes back dedup, the locked-version trigger.
- `services/api/tests/test_business_change_orders.py`: change orders (owner-only approval bound to the content hash, immutable accepted and amended versions, totals and rounding reconciliation with tax, milestone increases and reductions that skip settled milestones, stale change orders, reject and withdraw), the ledger, the AI usage import, profitability with revenue and cash apart, permissions and cross-workspace and cross-tenant isolation.
- `services/api/tests/test_migrations.py`: the migrations' shape.
- `apps/web/test/business.test.ts` and `apps/web/e2e/business.spec.ts`: helpers, and the main path from an empty catalog to a paid milestone in the browser, member, empty, denied, failed and Arabic states.
- `apps/web/test/businessChanges.test.ts` and `apps/web/e2e/business-change-orders.spec.ts`: change order and ledger forms, money and empty values, and in the browser a change order drafted, sent and approved with its hash, a labour cost turning the margin into a figure, a member who cannot approve and a failed list.
- `apps/mobile/test/business_change_orders_test.dart`: the project money screen: owner path, member, reject with a reason, failed and denied loads, layout at 320 and 768 wide with 200% text and in Arabic.
- `apps/mobile/test/business_test.dart`: catalogs, controller paging and states, layout at 320 to 1024 wide with 200% text, a lead's moves and notes, the pipeline, proposals waiting for acceptance, milestones, Arabic.

## Not in this release

These are later roadmap phases, not missing pieces of P1:

- Sending outbound messages (P3: human-approved sends) and any automation of them.
- GitHub linkage, support tickets, invoices and payment collection (P2); cash collected is what a person records on a milestone.
- AI usage from another workspace (for example a client's own ANUM workspace) is not linked yet: the import reads the workspace the owner works in.
- E-signature (deferred in the roadmap's reuse register: manual signed PDFs first).
