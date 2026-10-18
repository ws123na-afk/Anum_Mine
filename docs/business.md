# Business: the internal sales desk

Business is roadmap P1 "Internal sales MVP" ([ANUM OS business and product roadmap](ANUM_OS_Business_and_Product_Master_Roadmap.md), sections 4B, 6, 7 P1 and 8 Sales). It is where the owner keeps what they sell, who they want to sell it to, what they know about those companies, the deals in progress, the proposals and the projects that follow. It runs inside the owner's own workspace; nothing in it is seeded and nothing in it sends a message.

- API: `services/api/anum_api/business/` under `/api/v1/business`.
- Storage: migration `0070_business` (16 tables) and `0076_business_support` (support tickets), memory store for local runs and tests.
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
| Projects | A project from an accepted proposal, its milestones with status, acceptance and payment received | One project per proposal; milestone titles and amounts come from the accepted version |
| KPI summary | The owner's funnel and money figures for a window of days | See [KPI summary](#kpi-summary) |
| Support | Tickets opened by hand or from an inbound email or WhatsApp conversation, with severity, status, assignee, entitlement, escalation, notes, replies and a working-hours SLA | See [support tickets](#support-tickets) |

## Permissions

Defined in `anum_api/authorization.py`.

| Role | `business:read` | `business:write` | `business:manage` | Decide acceptance |
|---|---|---|---|---|
| Owner | yes | yes | yes | yes, in Approvals (`approval:decide`) |
| Member | yes | yes | no | no |
| Viewer | no | no | no | no |

Viewers are refused because the desk holds contacts' personal data; the API's 403 says who can use it, and the web and Flutter screens show that sentence. `business:manage` covers the catalog, the research caps, the support policy, reopening a closed ticket, deletions and clearing a do-not-contact mark.

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

There is no send route, queue or delivery tool in Business; `GET /setup` reports `sending_enabled: false` (outreach) and a test fails if a route under `/api/v1/business` names a send or an outbox. A person sends an approved draft themselves through an authorized channel and records it as an activity. Replies to a support ticket are different: they answer someone who wrote in, and they go through the inbox's own approval path (see [replies and approval](#replies-and-approval)); Business itself still sends nothing.

## Do not contact and the opt-out list

Marking a contact do not contact (`POST /contacts/{id}/do-not-contact`) adds their e-mail and phone to the tenant's [contact suppression list](privacy.md#contact-suppression) for every channel and purpose, and suppresses their open leads. Contacts already on that list show the masked entry and block contact records and drafts. Clearing the mark is for owners and does not remove the list entry; an owner removes that under Contact opt-outs. The CSV import adds contacts found on the list as suppressed leads.

## Proposal acceptance

A proposal has versions; each version has a SHA-256 content hash over its canonical JSON. Totals are reconciled in `Decimal`: subtotal, tax (tentative until the jurisdiction is verified), total, external costs, margin against the internal cost estimate, and milestones that must add up to the total.

1. `POST /proposals/{id}/request-acceptance` checks the version (line items, deliverables, acceptance criteria, milestone sum, total above zero), locks it and asks for an approval of the `business.accept_proposal` tool (high risk, always asks). The approval shows the client, version, totals, milestones, deliverables and exclusions and is bound to them by the payload hash.
2. An owner approves in Approvals (or in the proposal on the web) with the payload hash they were shown.
3. The tool runs only if the proposal still has that version, that content hash and that total; otherwise nothing is accepted. It marks the version accepted with who and when, wins the opportunity and the lead, and records the activity once.

A locked version is never edited: a change creates a new version and lapses the waiting approval (an old approval then answers 410). A database trigger refuses any update of a locked version. A rejected or lapsed approval returns the proposal to draft with the reason. An accepted proposal never changes; its accepted version is the contract record and the Markdown export uses it.

## KPI summary

`GET /kpi?days=` (7 to 366 days, ending now or at `to`):

- **Funnel**: of the leads created in the window, how many reached qualified, contacted, meeting, offered and won by now, each with its denominator (`reached of created`) and the rate from the previous step.
- **Pipeline**: open opportunities' assumed values and the weighted values, per currency, labelled as assumptions.
- **Contracted**: totals of proposals accepted in the window. **Cash collected**: milestone payments recorded with a date in the window. Neither is called revenue.
- Won and lost opportunities, proposals sent and accepted, research runs and searches in the window. When there are more than 5000 records the summary says it covers the newest.

## Support tickets

Roadmap 4.D and P2 ("support ticket"), section 6 `Support`: requester identity, entitlement, severity, escalations and resolution audit. Routes are under `/api/v1/business/support` (`anum_api/business/api_support.py`, rules in `support.py`, inbox links in `support_channels.py`). Web: Business, Support tab (`#business-support`, policy under Business, Settings). Flutter: Business, Support.

A ticket holds:

- **What and who**: title, description, the requester's name, e-mail and phone. When the e-mail or phone matches a Business contact, the requester is shown as verified and the contact and its account are linked.
- **Links**: the Business account, contact and project it concerns where known. Every id must be this workspace's (another workspace's id reads as missing), and they must agree: a contact or project of another account is refused.
- **Entitlement**: what covers the work. `project` (a project that is not closed), `support_plan` (an active catalog version, for example a "Support & Care" package) or `none` (not covered yet; the owner decides whether to quote).
- **Severity** (`critical`, `high`, `normal`, `low`), **assignee** (an active owner or member of the workspace), **escalation** (a flag with a reason, who and when; only open tickets).
- **Source**: `manual`, `email` or `whatsapp`, with the message or conversation id. See [from an inbox](#tickets-from-an-inbox).
- **Resolution audit**: a resolution code (`fixed`, `workaround`, `answered`, `no_fault_found`, `duplicate`, `out_of_scope`), a summary of at least a sentence, who resolved it and when, who closed it and when, and how often it was reopened. The database refuses a resolved or closed ticket without them.
- **Activity trail**: every change (opened, assigned, updated, status, escalation, notes, replies) is a row in `business_activities` with the ticket's id.

### Ticket states

`new`, `triaged`, `in_progress`, `waiting_on_customer`, `resolved`, `closed`; the allowed moves are `TICKET_TRANSITIONS` in `models.py` and each ticket lists them in `allowed`.

- Resolving needs the resolution code and summary. Closing needs a resolved ticket.
- Moving to waiting on customer counts as the first response (asking the customer something is a response) and pauses the resolution clock; leaving it resumes the clock.
- A resolved or closed ticket can be reopened (to in progress). Reopening a closed ticket is for owners. The time it sat resolved does not count against the team.
- A closed ticket cannot be edited until it is reopened. Owners may delete a ticket opened by mistake; one with a sent or waiting reply is kept as a record.

### SLA policy and due times

The owner sets the policy (`PUT /support/policy`, Business, Settings): one time zone, the working days, the working hours and, per severity, a response target and a resolution target in **working hours**. Defaults: Asia/Riyadh, Sunday to Thursday, 09:00 to 17:00; critical 2 h / 16 h, high 4 h / 40 h, normal 8 h / 80 h, low 16 h / 160 h. There is no 24/7 cover: a ticket that arrives at night or on a weekend starts counting when the next working day starts, and the screens say so. Set the targets from what you can actually deliver (roadmap 4.D: "SLA policy based on actual capacity").

- The clocks start at `reported_at`: the inbound message's time for a ticket from an inbox, otherwise when the ticket was opened.
- Due times are computed when the ticket is opened and again when its severity changes. A policy change applies to new tickets and to severity changes; existing due times stay.
- Paused working minutes (waiting on customer, resolved before a reopen) are added to the resolution target.
- `sla.response` and `sla.resolution` are `on_track`, `due_soon` (within an hour), `breached`, `paused`, `met` or `missed`; `sla.breached` is true for an open ticket with an overdue clock. The first response is the first reply sent through ANUM, a note marked as a response (the requester was answered outside ANUM, for example by phone) or moving to waiting on customer.
- `GET /support/summary` counts open, new, unassigned, escalated, breached and waiting tickets (the newest 5000 open tickets; it says when it covers fewer).

### Tickets from an inbox

`POST /support/tickets/from-message` with `source` `email` (an email inbox message id) or `whatsapp` (a WhatsApp conversation id). The caller must be able to read that inbox (`email:read` or `whatsapp:read`) and the inbox's own visibility applies.

- The ticket stores the id only: a link, never a copy of the text. It copies who wrote in (name, address or number) and when, and uses the subject as the default title. The inbox stays the record of what was said; the ticket screen links to it.
- A message in a private mailbox is refused: others on the ticket could not open it.
- One open ticket per message or conversation (a partial unique index backs it); a second request answers 409 with the open ticket's id.

### Replies and approval

A reply is written on the ticket by a person or by the agent's `business.draft_support_reply` tool (low risk; owners and members; it only drafts). It stays a draft until a person asks for approval (`POST /support/tickets/{id}/replies/{reply_id}/request-approval`):

- For an email ticket, ANUM creates an email inbox reply draft with exactly the reply's text and asks for the inbox's `send_email` approval (high risk, always approved by an owner with the payload hash). Needs `email:act`.
- For a WhatsApp ticket, the reply goes through WhatsApp's send: free-form text always waits for approval and needs the 24-hour window. Needs `whatsapp:reply`.
- Before that, the reply is checked: no unfilled placeholders and no guarantees or 24/7 promises.
- The reply then shows `pending_approval`; when the inbox reports it sent (or failed or discarded), the ticket shows it on the next read and the first send stops the response clock. Nothing in Business sends: it hands the text to the inbox's existing approval path and the runtime sends only what the owner approved.
- A ticket opened by hand has no conversation to reply in: reply yourself, then add a note marked as a response.

## Storage and isolation

Migration `0070_business` creates `business_settings`, `business_products`, `business_offers`, `business_accounts`, `business_contacts`, `business_leads`, `business_evidence`, `business_inferences`, `business_activities`, `business_opportunities`, `business_proposals`, `business_proposal_versions`, `business_projects`, `business_milestones`, `business_drafts` and `business_research_runs`.

- Every table has `tenant_id` and `workspace_id`, a foreign key to `workspaces` and forced row level security with `tenant_isolation_<table>` on `anum.tenant_id` and `anum.workspace_id` (the 0040 pattern). No role is granted anything and no discovery role reads these tables.
- References between business tables are composite foreign keys on `(tenant_id, workspace_id, id)`, so a row can never point into another tenant or workspace by a guessed id, even though foreign key checks ignore row policies.
- Every store query also filters on the caller's scope; a record of another workspace reads as missing (404).
- Activities are idempotent per key (`Idempotency-Key` on `POST /leads/{id}/activities`).
- Expand only: new tables, a trigger function and indexes. Numbered 0070 and chained after `0069_mail_writers_uidvalidity`, the head of `main`.
- Migration `0076_business_support` adds `business_support_tickets` and `business_ticket_replies` (same forced RLS and scoped foreign keys to accounts, contacts, projects and offers), a nullable `business_activities.ticket_id` with a scoped foreign key, and a nullable `business_settings.support_policy` (null means the defaults). Expand only; chained after `0074_brevo_campaigns` (a sibling branch takes 0075; renumber at merge). Deleting an account with tickets is refused; deleting a contact unlinks it from its tickets; a product whose version is a ticket's support plan cannot be deleted.

Every state-changing route writes an audit record with ids and counts only (never contact details or message text); `tests/test_business.py` walks all of them through `audit_probe.AuditProbe`.

## Tests

- `services/api/tests/test_business.py`: rules, permissions, catalog, dedup, the state machine, the score, evidence and inferences, do not contact and the opt-out list, idempotent activities, paging and search, cross-workspace and cross-tenant reads, CSV import, research with a fake `web_search` (caps, closed leads, a model that tries to cite missing sources or claim the buyer's authority, injected text kept as data), drafts and their checks, the main path, approval binding (a changed proposal cannot be accepted with the old approval, a tampered total is refused), rejection, lost opportunities and the audit walk.
- `services/api/tests/test_postgres_business.py`: the main path under forced RLS as the app role, tenant B and the other workspace cannot read or guess ids, row policies refuse writes into another scope, scoped foreign keys refuse cross-scope references, unique indexes back dedup, the locked-version trigger.
- `services/api/tests/test_business_support.py`: the working-hours SLA arithmetic (weekends, nights, hours to midnight, pauses), policy checks, permissions, the status flow and resolution audit, escalation and notes, links and entitlements, tickets from an email or WhatsApp conversation (fake inboxes), reply checks and the hand-off to the approval path, the agent's draft tool, paging, filters and search, cross-workspace and cross-tenant isolation and the audit walk of every support route.
- `services/api/tests/test_postgres_business.py::test_support_tickets_under_rls`: tickets under forced RLS, scoped foreign keys, the resolution check and the one-open-ticket-per-message index.
- `services/api/tests/test_migrations.py`: the migrations' shape.
- `apps/web/test/business.test.ts` and `apps/web/e2e/business.spec.ts`: helpers, and the main path from an empty catalog to a paid milestone in the browser, member, empty, denied, failed and Arabic states.
- `apps/mobile/test/business_test.dart`: catalogs, controller paging and states, layout at 320 to 1024 wide with 200% text, a lead's moves and notes, the pipeline, proposals waiting for acceptance, milestones, Arabic.

## Not in this release

These are later roadmap phases, not missing pieces of P1:

- Sending outbound messages (P3: human-approved sends) and any automation of them.
- Change orders, GitHub linkage, invoices and payment collection (P2); cash collected is what a person records on a milestone.
- Support: customers opening tickets themselves (a portal), a public status page and change log, automatic escalation or notifications on a breach, and attachments on tickets. Tickets link the inbox, which keeps any files.
- E-signature (deferred in the roadmap's reuse register: manual signed PDFs first).
