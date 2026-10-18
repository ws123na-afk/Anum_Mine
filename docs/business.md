# Business: the internal sales desk

Business is roadmap P1 "Internal sales MVP" ([ANUM OS business and product roadmap](ANUM_OS_Business_and_Product_Master_Roadmap.md), sections 4B, 6, 7 P1 and 8 Sales). It is where the owner keeps what they sell, who they want to sell it to, what they know about those companies, the deals in progress, the proposals and the projects that follow. It runs inside the owner's own workspace; nothing in it is seeded, and nothing in it sends a message without an owner's approval of that exact message (sending is off by default).

- API: `services/api/anum_api/business/` under `/api/v1/business`.
- Storage: migrations `0070_business` (16 tables) and `0080_business_pipeline` (6 tables: prospecting and the outbox), memory store for local runs and tests.
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

## Permissions

Defined in `anum_api/authorization.py`.

| Role | `business:read` | `business:write` | `business:manage` | Decide acceptance |
|---|---|---|---|---|
| Owner | yes | yes | yes | yes, in Approvals (`approval:decide`) |
| Member | yes | yes | no | no |
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

Business itself never sends a draft. An approved e-mail or WhatsApp draft can be queued in the [outreach outbox](#outreach), where every message waits for an owner's approval and sending is off by default; otherwise a person sends it through an authorized channel and records it as an activity.

## Prospecting pipeline

The Harvey capabilities written natively ([Harvey parity](business/HARVEY_PARITY.md)): `anum_api/business/pipeline.py`, routes under `/api/v1/business/pipeline`, web tab Prospecting, Flutter "Prospecting and outreach".

- **Target profiles**: each product has its own target customer profile (industries, company sizes, regions with the GCC first, roles, keywords, disqualifiers, value proposition in English and Arabic). Owners edit it (`PUT /pipeline/products/{id}/target`); `GET /pipeline/targets` lists every product with its profile.
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
3. **Send**: the tool re-checks everything: sending is on (`sending_enabled`, **off by default**, owners only), outreach is not paused, today's per-channel quota (`daily_quota`) is not used up, the lead has not replied or been closed, the draft and the contact did not change, and the gate passes again. Only then is the message handed to the connected e-mail or WhatsApp tool, whose own policy decision and the contact suppression check still apply. Otherwise the row is held, cancelled or failed and nothing is sent; a failed send is never counted as a contact.
4. **Follow-ups**: `POST /outreach/items/{id}/follow-up` writes a follow-up draft; approved, it is queued with `follow_up_of` and scheduled `follow_up_min_days` later (at most `follow_up_max` per lead), then asked for approval when due.
5. **Replies** (`POST /outreach/leads/{id}/replies`, and replies noticed in the e-mail inbox or on WhatsApp from a contact ANUM reached out to): every open row of the lead is cancelled (stop-on-reply). Opt-out wording is honoured in code: the contact goes on the tenant's opt-out list for every channel and is marked do not contact, and the lead is suppressed. Legal or complaint wording is flagged for a person. The text is classified and never stored.
6. **Kill switch**: a bounce blocks that address; above `max_bounce_rate_pct` (default 5%, after at least 10 sends in 30 days) outreach pauses itself with the reason. The pause (`POST /outreach/pause`, members pause, owners resume) also stops research runs and drafting.

`GET /outreach` reports sends per channel and today's quota, waiting messages, and replies, opt-outs and bounces each with its denominator, with an alarm when opt-outs pass 2%.

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

## Storage and isolation

Migration `0070_business` creates `business_settings`, `business_products`, `business_offers`, `business_accounts`, `business_contacts`, `business_leads`, `business_evidence`, `business_inferences`, `business_activities`, `business_opportunities`, `business_proposals`, `business_proposal_versions`, `business_projects`, `business_milestones`, `business_drafts` and `business_research_runs`.

- Every table has `tenant_id` and `workspace_id`, a foreign key to `workspaces` and forced row level security with `tenant_isolation_<table>` on `anum.tenant_id` and `anum.workspace_id` (the 0040 pattern). No role is granted anything and no discovery role reads these tables.
- References between business tables are composite foreign keys on `(tenant_id, workspace_id, id)`, so a row can never point into another tenant or workspace by a guessed id, even though foreign key checks ignore row policies.
- Every store query also filters on the caller's scope; a record of another workspace reads as missing (404).
- Activities are idempotent per key (`Idempotency-Key` on `POST /leads/{id}/activities`).
- Expand only: new tables, a trigger function and indexes. Numbered 0070 and chained after `0069_mail_writers_uidvalidity`, the head of `main`.

Every state-changing route writes an audit record with ids and counts only (never contact details or message text); `tests/test_business.py` walks all of them through `audit_probe.AuditProbe`.

## Tests

- `services/api/tests/test_business.py`: rules, permissions, catalog, dedup, the state machine, the score, evidence and inferences, do not contact and the opt-out list, idempotent activities, paging and search, cross-workspace and cross-tenant reads, CSV import, research with a fake `web_search` (caps, closed leads, a model that tries to cite missing sources or claim the buyer's authority, injected text kept as data), drafts and their checks, the main path, approval binding (a changed proposal cannot be accepted with the old approval, a tampered total is refused), rejection, lost opportunities and the audit walk.
- `services/api/tests/test_business_pipeline.py`: the page fetcher (robots.txt, SSRF and private redirects, logins and captchas, size and type limits, pacing), detectors and deterministic suggestions and product fit, signals only when confirmed, target profiles per product and discovery scoped to one product, discovery dedup and junk filter, profile observations with URL, excerpt and day, estimates against caps and the model budget, the budget stop, stop and pause, the outbox gate, suppression before queue and send, the approval bound to the payload hash, sending off by default, quotas, a changed draft, rejection, stop-on-reply and opt-outs, the bounce kill switch, the inbox hooks, tenant and workspace isolation, the audit walks and the synthetic journey (offer, discover, profile, confirmed signals, score, draft, approval, sending off, a reply stops the follow-up).
- `apps/web/test/businessPipeline.test.ts`, `apps/web/e2e/businessPipeline.spec.ts`, `apps/mobile/test/business_pipeline_test.dart`: Prospecting and Outreach in the browser and on the phone.
- `services/api/tests/test_postgres_business.py`: the main path under forced RLS as the app role, tenant B and the other workspace cannot read or guess ids, row policies refuse writes into another scope, scoped foreign keys refuse cross-scope references, unique indexes back dedup, the locked-version trigger.
- `services/api/tests/test_migrations.py`: the migration's shape.
- `apps/web/test/business.test.ts` and `apps/web/e2e/business.spec.ts`: helpers, and the main path from an empty catalog to a paid milestone in the browser, member, empty, denied, failed and Arabic states.
- `apps/mobile/test/business_test.dart`: catalogs, controller paging and states, layout at 320 to 1024 wide with 200% text, a lead's moves and notes, the pipeline, proposals waiting for acceptance, milestones, Arabic.

## Not in this release

These are later roadmap phases, not missing pieces of P1:

- Autonomous sending (P4 at the earliest): every outreach message waits for an owner's approval.
- Change orders, GitHub linkage, support tickets, invoices and payment collection (P2); cash collected is what a person records on a milestone.
- E-signature (deferred in the roadmap's reuse register: manual signed PDFs first).
