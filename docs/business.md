# Business: the internal sales desk

Business is roadmap P1 "Internal sales MVP" ([ANUM OS business and product roadmap](ANUM_OS_Business_and_Product_Master_Roadmap.md), sections 4B, 6, 7 P1 and 8 Sales). It is where the owner keeps what they sell, who they want to sell it to, what they know about those companies, the deals in progress, the proposals and the projects that follow. It runs inside the owner's own workspace; nothing in it is seeded, and nothing in it sends a message without an owner's approval of that exact message (sending is off by default).

- API: `services/api/anum_api/business/` under `/api/v1/business`.
- Storage: migrations `0070_business` (16 tables), `0076_business_support` (support tickets), `0080_business_change_orders` (change orders and the usage ledger) and `0082_business_pipeline` (6 tables: prospecting and the outbox), memory store for local runs and tests.
Business is roadmap P1 "Internal sales MVP" ([ANUM OS business and product roadmap](ANUM_OS_Business_and_Product_Master_Roadmap.md), sections 4B, 6, 7 P1 and 8 Sales). It is where the owner keeps what they sell, who they want to sell it to, what they know about those companies, the deals in progress, the proposals and the projects that follow. It runs inside the owner's own workspace; nothing in it is seeded and nothing in it sends a message. Invoices and the customer portal ([portal](business-portal.md)) are roadmap P2.

- API: `services/api/anum_api/business/` under `/api/v1/business`.
- Storage: migrations `0070_business` (16 tables), `0076_business_support` (support tickets), `0080_business_change_orders` (change orders and the usage ledger) and `0089_business_invoices_portal` (invoices, payments, numbering and the customer portal), memory store for local runs and tests.
- Web: Business in the main navigation (`apps/web/src/BusinessView.tsx` and its tabs), English and Arabic.
- Flutter: Business in the menu (`apps/mobile/lib/features/business/`), English and Arabic; the phone edits the catalog, accounts, contacts, leads, opportunities and support tickets ([Mobile features](mobile-features.md#business)).
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
| Invoices | Invoices built from milestones (change order milestones included), the unbilled share of a contract, billable ledger entries, a catalog offer or lines you write; numbered and sent only after an owner approves; payments recorded by hand; void; a printable copy | See [invoices](#invoices) |
| Customer portal | A link per customer contact: their offers, change orders, invoices and requests, with approving an offer or a change order | See [the customer portal](business-portal.md) |
| KPI summary | The owner's funnel and money figures for a window of days | See [KPI summary](#kpi-summary) |
| Support | Tickets opened by hand or from an inbound email or WhatsApp conversation, with severity, status, assignee, entitlement, escalation, notes, replies and a working-hours SLA | See [support tickets](#support-tickets) |

## Permissions

Defined in `anum_api/authorization.py`.

| Role | `business:read` | `business:write` | `business:manage` | Decide acceptance |
|---|---|---|---|---|
| Owner | yes | yes | yes | yes, in Approvals (`approval:decide`): proposal acceptance and sending an invoice; change orders in the project |
| Member | yes | yes | no | no (members draft and send change orders, draft invoices and ask for approval to send them) |
| Viewer | no | no | no | no |

Viewers are refused because the desk holds contacts' personal data; the API's 403 says who can use it, and the web and Flutter screens show that sentence. `business:manage` covers the catalog, the research caps, the support policy, the billing profile, voiding an invoice, removing a recorded payment, giving a customer a portal link and turning it off, reopening a closed ticket, deletions and clearing a do-not-contact mark.

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

Business itself never sends a draft. An approved e-mail or WhatsApp draft can be queued in the [outreach outbox](#outreach), where every message waits for an owner's approval and sending is off by default; otherwise a person sends it through an authorized channel and records it as an activity.

## Prospecting pipeline

The Harvey capabilities written natively ([Harvey parity](business/HARVEY_PARITY.md)): `anum_api/business/pipeline.py`, routes under `/api/v1/business/pipeline`, web tab Prospecting, Flutter "Prospecting and outreach".

- **Target profiles**: each product has its own target customer profile (industries, company sizes, regions with the GCC first, roles, keywords, disqualifiers, trigger events, the size in the owner's words, value proposition in English and Arabic; `scripts/business/seed-products.py` fills them). Owners edit it (`PUT /pipeline/products/{id}/target`); `GET /pipeline/targets` lists every product with its profile.
- **Runs**: a discovery run is for one product (terms and places typed, or the product's industries, keywords and regions); a profile run reads up to 20 leads' own sites. `POST /pipeline/runs/estimate` creates an estimated run: searches, page reads and model calls at most, the estimated model cost, what is left of today's caps and the monthly model budget, and the problems that stop it. Nothing is called until `POST /pipeline/runs/{id}/confirm`; the run then never exceeds the confirmed numbers, re-checks the daily caps before each call, stops when the budgeted gateway refuses a model call, and reads the stop button (`/stop`) and the pause between steps. A run whose heartbeat stops (an API restart) shows as interrupted and resumes on a second confirm without duplicating leads.
- **Discovery**: results are filtered for directories, social networks, media, institutions and the product's disqualifiers, deduplicated by domain; a company whose name matches an existing account but whose domain differs is listed as a possible duplicate and never merged. New companies become an account, a `discovered` lead for that product and an evidence row (result URL, title, excerpt).
- **Product fit**: `GET /pipeline/leads/{id}/profile` scores the lead against every live product's target profile from recorded facts only (disqualifier 0; industry 2, region 1, keywords up to 2; at most 5) and shows the best fit, with the score breakdown and suggestions.
- Daily caps: lead research and pipeline runs share the research caps (runs and searches); page reads have their own cap (`fetches_per_day`, default 100) and `pages_per_lead` (default 5).

## Signals

`anum_api/business/signals.py` proposes a catalog (found in a search, website platform and tools, Google Ads tag, Meta pixel, no structured data, no online booking, agency credit, contact form, published role address, site size, stale content, AI crawlers blocked, hiring). Owners confirm or reject each (`PUT /pipeline/signals`); only confirmed signals are stored, each as an observation (`business_observations`) with the page URL, the excerpt and the day it was read, tied to the page's evidence row. A page that could not be read records nothing: unknown stays unknown. Score suggestions follow fixed rules over the newest observations (reachable channel, problem evidence, project scale only) and cite the same evidence; a person applies them through the ordinary score route.

## Page fetch

`web.fetch_page` (`anum_api/business/fetch.py`) is a read-only, low-risk tool registered with the business tools, so `ToolPolicy` and governance mediate every call and each one is audited (host, status and size only). It reads public https pages on the default port only, through the SSRF guard of `model_egress` (resolved once, every address checked, pinned, no redirects followed blindly: at most three, each re-checked), always on in every environment. It reads robots.txt first (a 4xx answer means no rules, a 5xx or no answer means nothing is read) and obeys it and its crawl delay; one request per host per second; 10 second deadline, 1.5 MB cap, text and XML only, no cookies, credentials or query strings. A 401 or 403, a password field or a challenge page is refused as behind a login or a captcha.

## Outreach

`anum_api/business/outreach.py`, routes under `/api/v1/business/outreach`, web tab Outreach.

1. **Queue** (`POST /outreach/drafts/{id}/queue`): an approved e-mail or WhatsApp draft passes the deterministic gate (recipient matches the contact, consent basis, opt-out list and do not contact, an address that bounced, subject and length limits, merge tags, spam wording, one link, plain text, an opt-out line, figures from the catalog) and then asks for an approval of `business.send_outreach` (high risk, always asks, autonomy cannot lower it) bound by the payload hash to the channel, recipient, subject and text. `GET /outreach/ready` lists approved drafts not yet queued.
2. **Approve**: an owner approves in Approvals (or the Outreach tab) with the payload hash shown.
3. **Send**: the tool re-checks everything: sending is on (`sending_enabled`, **off by default**, owners only), outreach is not paused, today's per-channel quota (`daily_quota`) is not used up, the lead has not replied or been closed, the draft and the contact did not change, and the gate passes again. In one short transaction it claims the row (the workspace's settings row and the outbox row locked FOR UPDATE), moves it to `sending` and so reserves the day's quota slot; a second run finds `sending` and sends nothing. Only then, with no transaction open, is the message handed to the connected e-mail or WhatsApp tool with the outbox id as the idempotency key; its own policy decision and the contact suppression check still apply. A failed send releases the slot. A row left in `sending` by a restart is never resent: with a provider id it is marked sent, otherwise `needs_review` for a person to check. Otherwise the row is held, cancelled or failed and nothing is sent; a failed send is never counted as a contact.
4. **Follow-ups**: `POST /outreach/items/{id}/follow-up` writes a follow-up draft; approved, it is queued with `follow_up_of` and scheduled `follow_up_min_days` later (at most `follow_up_max` live follow-ups per sequence, counted across every draft built from it, and one live row per step, backed by a unique index), then asked for approval when due.
5. **Replies** (`POST /outreach/leads/{id}/replies`, and replies noticed in the e-mail inbox or on WhatsApp from a contact ANUM reached out to): every open row of the lead is cancelled (stop-on-reply). Opt-out wording is honoured in code: the contact goes on the tenant's opt-out list for every channel and is marked do not contact, and the lead is suppressed. Legal or complaint wording is flagged for a person. The text is classified and never stored. From the e-mail inbox, the body of a matching reply is read through the inbox's own body fetch (shared mailboxes only); a private mailbox's content is never read, so its replies stop follow-ups by the sender alone.
6. **Kill switch**: a bounce blocks that address; above `max_bounce_rate_pct` (default 5%, after at least 10 sends in 30 days) outreach pauses itself with the reason. The pause (`POST /outreach/pause`, members pause, owners resume) also stops research runs and drafting.

`GET /outreach` reports sends per channel and today's quota, waiting messages, and replies, opt-outs and bounces each with its denominator, with an alarm when opt-outs pass 2%.
Replies to a support ticket are different: they answer someone who wrote in, and they go through the inbox's own approval path (see [replies and approval](#replies-and-approval)).

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

On the web these are three blocks of the project drawer (`apps/web/src/BusinessProjectMoney.tsx`); on the phone, "Change orders and costs" on a project card (`apps/mobile/lib/features/business/project_money.dart`), with the same change order form as the web (several added or removed lines with kind, quantity and price, who asked, the request, the timeline and internal cost changes).

## Invoices

Roadmap P2. `anum_api/business/api_invoices.py` (routes), `invoices.py` (rules), `invoice_service.py` (numbering, double-billing checks, milestone sync), `invoice_print.py` (the printable page) and the tool `business.send_invoice` in `tools.py`. ANUM never e-mails an invoice and never takes a payment.

### Billing profile

`GET` and `PUT /api/v1/business/billing-profile` (owners write): the legal name, address, tax label and number, e-mail and phone, bank details, a footer note, the number prefix (`INV` by default), the default days until due and the default tax rate. The profile is copied onto the invoice when sending is requested, so a later change never rewrites a sent invoice. An invoice cannot be sent for approval without a legal name, and one that charges tax needs the tax number.

### Where lines come from

`GET /invoices/billable?account_id=&project_id=` lists what could go on the next invoice and what is already billed (with the invoice number); `POST /invoices` builds a draft from any mix of:

| Source | Rule |
|---|---|
| Milestones (`milestone_ids`) | A project's milestones, including the milestone an approved change order added (listed with its change order number). The amount is the milestone's, tax included, split into net and tax so the invoice reconciles with the contract to the cent. A milestone already marked paid, with no amount, or already on a not-void invoice is refused |
| Contract share (`contract_amount`) | For a project that has no milestones: any amount from 0.01 up to the contract total (tax included) not yet invoiced |
| Usage ledger (`usage_ids`, `usage_markup_pct`) | Entries marked billable, of this client (and project), in the invoice's currency, billed once. Billed at their unit cost plus the optional markup; labour becomes a labour line |
| Catalog offer (`offer_id`, `offer_quantity`, `offer_unit_price`) | An active offer version; a fixed price is used as it is, any other price type needs the price entered |
| Manual lines (`lines`) | Description, kind, quantity, net unit price, optional tax rate |

Tax is per line (the contract's rate for contract lines, the invoice's `tax_rate_pct`, else the project's or the profile's default, for the rest). Net, tax and total of every line and of the invoice are computed in `Decimal` (two places, half up) by the server; whatever a client sends in those fields is replaced. Currency follows the project (or the request); mixing currencies is refused. The buyer is the account and its first contact with an e-mail address, editable on a draft.

### Status flow

| Status | Meaning | Moves to |
|---|---|---|
| `draft` | Being prepared; the lines, buyer, notes and terms change; no number yet | `pending_approval` (request approval), deleted |
| `pending_approval` | Frozen with its hash and waiting for an owner in Approvals | `sent` (owner approves), `draft` (rejected, lapsed, cancelled, or edited, which lapses the request) |
| `sent` | Issued: numbered, dated, visible in the customer's portal. Never changes | `paid` (balance reaches zero), `void` |
| `paid` | Fully paid | `sent` (a payment is removed) |
| `void` | Cancelled by an owner; keeps its number; its lines can be invoiced again | final |

Overdue is not a status: a sent invoice whose due date has passed and which has a balance is flagged `overdue` with `days_overdue`.

### Approval before anything is sent

`POST /invoices/{id}/request-approval` (members and owners) freezes the draft, copies the billing profile onto it, stores the SHA-256 of the buyer, seller, lines and figures, and creates a `business.send_invoice` approval (high risk, always asks) whose payload shows the customer, every line, the tax and the total. An owner approves it in Approvals with the payload hash they were shown; members cannot decide. The tool then re-checks the hash, the total and the currency: if the invoice changed in the meantime nothing is sent. Sending means: take the next number of the year, set the issue date to today and the due date from `due_in_days`, record who approved and when, and show the invoice in the portal. The approval is per invoice; there is no standing permission to send.

Numbers are `<prefix>-<year>-<4 digits>` (`INV-2026-0001`), taken from a counter row that is locked while the number is taken, one sequence per workspace and year. A voided invoice keeps its number, so numbers have gaps only where an invoice was voided, never where one was deleted (only drafts are deleted). A unique index and a database trigger back this: once an invoice is sent, paid or void, a direct UPDATE cannot change its lines, figures, number, dates, buyer or seller, and a void invoice cannot be reopened.

### Payments by hand

`POST /invoices/{id}/payments` (members and owners): amount (not above the balance), date (not in the future, not before the issue date), method (bank transfer, cash, card, cheque, other), reference and a note. When the balance reaches zero the invoice becomes `paid` and each milestone on it is marked paid with its full amount on the payment's date (cash collected in the project and the [KPI summary](#kpi-summary)); a milestone a person had already marked paid is left alone. `DELETE /invoices/{id}/payments/{payment}` (owners) takes a payment back: the invoice returns to `sent` and the milestones it marked are unmarked. Payments on invoices without milestone lines (usage, offers, contract shares) appear in the invoice summary only; the project's cash collected stays the sum of milestone payments.

### Void

`POST /invoices/{id}/void {reason}` (owners): only a sent invoice with no payments. Remove payments first if they were a mistake. There are no credit notes yet: void and issue again.

### Printing and PDF

`GET /invoices/{id}/print?lang=en|ar` returns one self-contained HTML page (A4 print stylesheet, right to left for Arabic, no script or external file, a strict content security policy, every value escaped). Print it or choose "Save as PDF" in any browser. A draft or a waiting invoice carries a "not sent" stamp, a void one a "void" stamp, a paid one a "paid" stamp, so an unsent copy cannot pass for the real one. A sent invoice prints the seller details it was sent with.

### Routes

| Route | Who | Does |
|---|---|---|
| `GET /invoices?status=&account_id=&project_id=&overdue=&q=` | read | Pages the invoices (search matches the number, the buyer and the customer reference) |
| `GET /invoices/summary` | read | Outstanding, overdue, invoiced this month, collected this month and drafts, per currency, never converted |
| `GET /invoices/billable` | read | What can be invoiced, and what is already billed |
| `POST /invoices`, `GET`, `PATCH /invoices/{id}`, `DELETE` (draft) | write | Draft, read, edit, delete |
| `POST /invoices/{id}/request-approval`, `/cancel-request` | write | Ask for approval to send; withdraw the request |
| `POST /invoices/{id}/void` | owner | Void |
| `POST /invoices/{id}/payments`, `DELETE .../payments/{id}` | write, owner | Record, take back |
| `GET /invoices/{id}/print` | read | The printable copy |

Every invoice response carries `can` (what the caller may do now) and `issues` (why a draft cannot be sent yet), so web and phone show the same buttons and reasons. On the web the invoices are the Invoices tab of Business (`apps/web/src/BusinessInvoices.tsx`); on the phone, Invoices in the Business screen (`apps/mobile/lib/features/business/invoices.dart`). Deleting an account that has invoices is refused: its records are kept.

## KPI summary

`GET /kpi?days=` (7 to 366 days, ending now or at `to`):

- **Funnel**: of the leads created in the window, how many reached qualified, contacted, meeting, offered and won by now, each with its denominator (`reached of created`) and the rate from the previous step.
- **Pipeline**: open opportunities' assumed values and the weighted values, per currency, labelled as assumptions.
- **Contracted**: totals of proposals accepted in the window, plus the total change (with tax, negative for a reduction) of change orders approved in the window. **Cash collected**: milestone payments recorded with a date in the window. Neither is called revenue.
- Won and lost opportunities, proposals sent and accepted, research runs and searches in the window. When there are more than 5000 records the summary says it covers the newest.

## Support tickets

Roadmap 4.D and P2 ("support ticket"), section 6 `Support`: requester identity, entitlement, severity, escalations and resolution audit. Routes are under `/api/v1/business/support` (`anum_api/business/api_support.py`, rules in `support.py`, inbox links in `support_channels.py`). Web: Business, Support tab (`#business-support`, policy under Business, Settings). Flutter: Business, Support (list, create, detail, replies; the support plan entitlement, and the policy editor are on the web only). Tickets from a message are created with **Create ticket** on an email in the Inbox and on a WhatsApp conversation, on the web and on the phone (title and severity, then a link to the new ticket); a second request for a message that already has an open ticket answers 409 and the screen says which ticket. Web: `apps/web/src/TicketFromMessage.tsx`; Flutter: the Inbox message and WhatsApp conversation screens.

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

Migration `0080_business_change_orders` adds `business_change_orders` and `business_usage_ledger` under the same rules, and one nullable column, `business_proposal_versions.change_order_id`. Check constraints keep a sent or decided change order bound to a version and hash, and an approved one naming who approved it and the version it produced. Numbered 0080 after `0083_skill_signing`.

Migration `0082_business_pipeline` adds `business_outreach_settings`, `business_target_profiles`, `business_observations`, `business_pipeline_runs`, `business_outbox` and `business_replies` under the same rules, with scoped foreign keys to leads, accounts, contacts, drafts and products and unique indexes (one target profile per product, one live outbox row per draft and per sequence step). Expand only; chained after `0088_approvals_paging`, the head of `main`. All six are listed in `anum_api/tenant_lifecycle_tables.py` for export and purge, and carry the `anum_tenant_purger` grants (scope columns and delete) and the `lifecycle_purge_<table>` policy, as the change-order tables do.
Migration `0089_business_invoices_portal` adds `business_invoices`, `business_invoice_payments`, `business_invoice_counters`, `business_portal_access` and `business_portal_decisions` under the same rules, and one nullable column, `business_settings.billing_profile`. Check constraints keep totals equal to subtotal plus tax, a paid invoice fully paid and a sent invoice numbered, dated and attributed; a trigger freezes a sent invoice ([invoices](#invoices)); unique indexes back the invoice number, the per-year counter and one customer decision per exact version. Expand only; chained after `0088_approvals_paging`, the head of `main`.

- Every table has `tenant_id` and `workspace_id`, a foreign key to `workspaces` and forced row level security with `tenant_isolation_<table>` on `anum.tenant_id` and `anum.workspace_id` (the 0040 pattern). No role is granted anything and no discovery role reads these tables.
- References between business tables are composite foreign keys on `(tenant_id, workspace_id, id)`, so a row can never point into another tenant or workspace by a guessed id, even though foreign key checks ignore row policies.
- Every store query also filters on the caller's scope; a record of another workspace reads as missing (404).
- Activities are idempotent per key (`Idempotency-Key` on `POST /leads/{id}/activities`).
- Expand only: new tables, a trigger function and indexes. Numbered 0070 and chained after `0069_mail_writers_uidvalidity`, the head of `main`.
- Migration `0076_business_support` adds `business_support_tickets` and `business_ticket_replies` (same forced RLS and scoped foreign keys to accounts, contacts, projects and offers), a nullable `business_activities.ticket_id` with a scoped foreign key, and a nullable `business_settings.support_policy` (null means the defaults). Expand only; chained after `0075_file_scan_status`, the head of `main`. Deleting an account with tickets is refused; deleting a contact unlinks it from its tickets; a product whose version is a ticket's support plan cannot be deleted.

Every state-changing route writes an audit record with ids and counts only (never contact details or message text); `tests/test_business.py` walks all of them through `audit_probe.AuditProbe`.

## Tests

- `services/api/tests/test_business.py`: rules, permissions, catalog, dedup, the state machine, the score, evidence and inferences, do not contact and the opt-out list, idempotent activities, paging and search, cross-workspace and cross-tenant reads, CSV import, research with a fake `web_search` (caps, closed leads, a model that tries to cite missing sources or claim the buyer's authority, injected text kept as data), drafts and their checks, the main path, approval binding (a changed proposal cannot be accepted with the old approval, a tampered total is refused), rejection, lost opportunities and the audit walk.
- `services/api/tests/test_business_pipeline.py`: the page fetcher (robots.txt, SSRF and private redirects, logins and captchas, size and type limits, pacing), detectors and deterministic suggestions and product fit, signals only when confirmed, target profiles per product and discovery scoped to one product, discovery dedup and junk filter, profile observations with URL, excerpt and day, estimates against caps and the model budget, the budget stop, stop and pause, the outbox gate, suppression before queue and send, the approval bound to the payload hash, sending off by default, quotas, a changed draft, rejection, stop-on-reply and opt-outs, the bounce kill switch, the inbox hooks, tenant and workspace isolation, the audit walks and the synthetic journey (offer, discover, profile, confirmed signals, score, draft, approval, sending off, a reply stops the follow-up).
- `apps/web/test/businessPipeline.test.ts`, `apps/web/e2e/businessPipeline.spec.ts`, `apps/mobile/test/business_pipeline_test.dart`: Prospecting and Outreach in the browser and on the phone.
- `services/api/tests/test_postgres_business.py`: the main path under forced RLS as the app role, tenant B and the other workspace cannot read or guess ids, row policies refuse writes into another scope, scoped foreign keys refuse cross-scope references, unique indexes back dedup, the locked-version trigger.
- `services/api/tests/test_business_change_orders.py`: change orders (owner-only approval bound to the content hash, immutable accepted and amended versions, totals and rounding reconciliation with tax, milestone increases and reductions that skip settled milestones, stale change orders, reject and withdraw), the ledger, the AI usage import, profitability with revenue and cash apart, permissions and cross-workspace and cross-tenant isolation.
- `services/api/tests/test_business_invoices.py`: the line pricing and the hash, the billing profile, a milestone invoice that reconciles with tax and waits for an owner, the approval bound to the hash (edit, reject, cancel), numbering that never reuses a number, double billing, change order milestones, billable ledger entries, a contract without milestones, offers, payments and their effect on milestones, void, overdue and the summary, the printable copy (escaping, language, stamps), permissions, isolation and the audit walk.
- `services/api/tests/test_business_portal.py` and `docs/business-portal.md#tests`: the customer portal.
- `services/api/tests/test_business_support.py`: the working-hours SLA arithmetic (weekends, nights, hours to midnight, pauses), policy checks, permissions, the status flow and resolution audit, escalation and notes, links and entitlements, tickets from an email or WhatsApp conversation (fake inboxes), reply checks and the hand-off to the approval path, the agent's draft tool, paging, filters and search, cross-workspace and cross-tenant isolation and the audit walk of every support route.
- `services/api/tests/test_postgres_business.py::test_support_tickets_under_rls`: tickets under forced RLS, scoped foreign keys, the resolution check and the one-open-ticket-per-message index.
- `services/api/tests/test_migrations.py`: the migrations' shape.
- `apps/web/test/businessSupport.test.ts` and `apps/web/e2e/businessSupport.spec.ts` (mocks in `e2e/support/businessSupportApi.ts`): the SLA badges, filters and query building, and in the browser an empty queue, a ticket opened by hand, assigned, worked and noted, an email ticket whose reply is handed to Approvals with its payload hash, resolving with the required resolution, a member refused when reopening a closed ticket, the owner saving the policy, and the denied and failed states.
- `apps/mobile/test/business_support_test.dart`: the support controller's paging, filters and states (loading, empty, error, denied), creating a ticket, status changes and the resolve dialog's checks, reply drafts and the approval hand-off, layout at 320 and 1024 wide with 200% text, Arabic.
- `apps/web/test/business.test.ts` and `apps/web/e2e/business.spec.ts`: helpers, and the main path from an empty catalog to a paid milestone in the browser, member, empty, denied, failed and Arabic states.
- `apps/web/test/businessChanges.test.ts` and `apps/web/e2e/business-change-orders.spec.ts`: change order and ledger forms, money and empty values, and in the browser a change order drafted, sent and approved with its hash, a labour cost turning the margin into a figure, a member who cannot approve and a failed list.
- `apps/mobile/test/business_change_orders_test.dart`: the project money screen: owner path, member, reject with a reason, failed and denied loads, layout at 320 and 768 wide with 200% text and in Arabic.
- `apps/mobile/test/business_test.dart`: catalogs, controller paging and states, layout at 320 to 1024 wide with 200% text, a lead's moves and notes, the pipeline, proposals waiting for acceptance, milestones, Arabic.

## Not in this release

These are later roadmap phases, not missing pieces of P1:

- Autonomous sending (P4 at the earliest): every outreach message waits for an owner's approval.
- GitHub linkage, invoices and payment collection (P2); cash collected is what a person records on a milestone.
- Sending outbound messages (P3: human-approved sends) and any automation of them.
- GitHub linkage and payment collection: ANUM takes no payments; cash collected is what a person records on a milestone or on an invoice.
- Invoices: credit notes (void and issue again), recurring invoices, payment reminders and e-mailing an invoice from ANUM (the owner sends the printed copy or the portal link through the inbox's approved path), and a PDF file produced on the server (the printable page saves as PDF in the browser).
- AI usage from another workspace (for example a client's own ANUM workspace) is not linked yet: the import reads the workspace the owner works in.
- Support: customers replying inside a request, a public status page and change log, automatic escalation or notifications on a breach, and attachments on tickets. Tickets link the inbox, which keeps any files.
- E-signature (deferred in the roadmap's reuse register: manual signed PDFs first).
