# Reuse decision register (Order 001)

For each outside project named in section 5 of the [business master roadmap](../ANUM_OS_Business_and_Product_Master_Roadmap.md): what its license really says today, what we decide, and where it would plug into ANUM. Rule from the roadmap: prefer what ANUM already has; never merge a whole codebase; check the license at the exact commit before copying anything.

Part of Order 001: [gap register](ANUM_BUSINESS_GAP_REGISTER.md), [P0 remediation](P0_CI_REMEDIATION.md), [P1 acceptance tests](P1_ACCEPTANCE_TESTS.md).

## How this was checked

On 18 October 2026, each repository's default branch was cloned without file contents (`git clone --depth 1 --filter=tree:0 --no-checkout`) and its license files read at that commit with `git show HEAD:<file>`. Harvey was cloned in full (depth 1) and its code read. Nothing was installed or run, and no code was copied into ANUM. The GitHub REST API was not usable for these repositories from this session (it is limited to the session's own repositories), so dates are commit dates from git.

This is an engineering reading, **not legal advice**. Before shipping anything that embeds third party code in a product sold to customers, have the license reviewed (roadmap section 9) and add it to a `THIRD_PARTY_NOTICES.md` and the SBOM.

## Decisions at a glance

| Candidate | License at the checked commit | Decision | In short |
|---|---|---|---|
| ANUM itself | own code | **Keep and extend** | Build Business inside ANUM. |
| Harvey | MIT | **Extract patterns** (no code import, no service) | Copy ideas: cost-first pipeline, deterministic send gate, outbox ladder, signal catalog. |
| Crawl4AI | Apache-2.0 **plus an attribution clause** | **Defer**; build a small native page fetcher first | Heavy (headless browser); attribution must be shown. |
| MarkItDown | MIT | **Adapter, later** (sandboxed worker) only if file import needs it | ANUM accepts Office files but does not extract their text yet. |
| Chatwoot | MIT core, `enterprise/` proprietary | **Defer**; maybe separate service in P2 | ANUM already has WhatsApp, email and an inbox. |
| Twenty | AGPLv3 core, some MIT packages, some Enterprise files | **Reference only** (data model ideas) | Do not embed AGPL code; build the small CRM tables natively. |
| Activepieces | MIT core, `packages/ee` commercial | **Defer** | ANUM automation already has triggers, retries, waits, approvals. |
| n8n | Sustainable Use License (not open source) | **Skip** for the product; internal evaluation only | Forbids offering it to customers as part of a paid product. |
| Langfuse | MIT core, `ee/` commercial; copyright now ClickHouse, Inc. | **Defer** | OpenTelemetry already in ANUM; Langfuse needs ClickHouse and more. |
| OpenTelemetry Collector | Apache-2.0 | **Keep existing** | `anum_api/telemetry.py` already exports OTLP. |
| Keycloak | Apache-2.0 | **Keep existing** | Already the identity provider; harden, do not replace. |
| Playwright | Apache-2.0 (+ NOTICE) | **Keep existing** | Already ANUM's browser test tool. |
| Documenso | AGPLv3 | **Defer** | Manual signed PDFs until contract volume justifies it; then separate service, never embedded. |
| Appsmith | Apache-2.0 | **Skip** | ANUM already has its own admin and module pages. |
| Graphiti | Apache-2.0 | **Defer** | Needs a graph database; wait for measured memory failures. |

## Details per candidate

### ANUM (keep and extend)

Everything the Business section needs at its base is already in ANUM (see the [gap register](ANUM_BUSINESS_GAP_REGISTER.md)): tenants and RLS, agents and skills, the model gateway with budgets, approvals bound to a payload hash, the contact suppression list, the governed delivery path, team work items, reports and files. New code goes in `services/api/anum_api/business/` as the roadmap proposes, with its own migration and RLS policies, and reuses those parts.

### Harvey (extract patterns)

- **Repository:** https://github.com/ethanplusai/harvey, branch `main`, commit `e114e95471b1` (15 Oct 2026). `LICENSE`: MIT, "Copyright (c) 2026 Ethan Rogers". About 17,800 lines of Python plus a small web dashboard.
- **What it is:** a single-user sales agent that runs on the operator's own machine, stores everything in SQLite (`harvey/state.py`) and calls the model by running the `claude -p` command line (`harvey/brain.py`) or Codex, so model use comes out of a personal Claude or ChatGPT subscription.
- **Why we do not run it or import it:** it is single-tenant (one SQLite file, no tenant or workspace), it relies on a personal subscription CLI for unattended server work (the roadmap warns this may not be allowed by the provider's terms, and ANUM already has a budgeted, metered model gateway), and it includes LinkedIn browser automation (`harvey/integrations/linkedin.py`, Playwright with "humanlike randomized delays") that the roadmap rules out. Its profile step reads `robots.txt` only as a *signal* about AI crawlers (`harvey/collectors/profile.py`); it does not use `robots.txt` to decide whether to fetch a page. ANUM's fetcher must.

How Harvey's pieces map onto ANUM:

| Harvey piece (file) | What it does | ANUM part to reuse | Treatment |
|---|---|---|---|
| Discover (`collectors/discover.py`) | Finds businesses: OpenStreetMap (Nominatim plus listings, free), DataForSEO and Serper (paid). Cost estimate before a run and a budget check inside the loop. | `web_search` tool (SearXNG, `anum_api/web_search.py`) for queries; model budgets (`model_budget.py`) for the cost cap. | Pattern: "estimate, then confirm, then run with a budget". OpenStreetMap listings could be a later read-only tool with its own usage policy check. No paid providers in P1. |
| Profile (`collectors/profile.py`, `integrations/tech_detect.py`) | Fetches homepage, `robots.txt`, `sitemap.xml`, team and careers pages; regex for signals (incumbent agency, ads, tech stack). No model call. | Missing in ANUM: a page fetch tool. Reuse the SSRF guard of `anum_api/commerce/http.py` (`owner_url_policy`, `check_owner_url`, built on `model_egress.check_endpoint`) and register it as a read-only tool so `ToolPolicy` mediates it. | Pattern: cheap, model-free evidence first. Each signal is stored with its URL and excerpt (`business_evidence`). Must honour `robots.txt` and rate limits. |
| Signals (`signals.py`) | Harvey proposes a catalog of signals; the person confirms which are collected. Every signal is an observation with its source. | Agent skills (`agent_skills.py`, `SkillManifest`) for the instructions; approvals for confirming. | Pattern: only confirmed signals are collected; facts and guesses kept apart. Matches the roadmap's "verified source facts vs agent inferences". |
| Scout (`agents/scout.py`) | Python does all searching; the model only scores and summarises what Python found. | Agent runtime (`runtime.py`, `agent_planning.py`) with a new `anum.business-research` skill. | Pattern: the model never browses; it reads labelled evidence (`prompt_provenance.label_untrusted`). Scoring in ANUM is the deterministic 0 to 30 score of roadmap 3.2, with the model only drafting the explanation. |
| Writer (`agents/writer.py`, `prompts/writer.md`) | Writes the first email per prospect from grounded facts, follow-ups per campaign. | Model gateway with budgets; marketing assist (`marketing/assist.py`) as the drafting precedent. | Pattern only. P1 drafts are saved, never sent. |
| Gate (`gate.py`) | Deterministic pre-send checks in code: valid recipient matching the record, verified address, subject and body limits, no leftover merge tags, banned phrases, link budget, plain text only. | Approvals (`approval_integrity.py`), suppression (`suppression.py`), delivery (`delivery.py`). | **Port the idea as ANUM code** (MIT allows it; we will write our own): a `business.outreach_gate` that runs before an approval can be requested and again at send time. |
| Outbox ladder (`agents/sender.py`) | Each email is a row; `pending_review` (copilot) or `approved` (autopilot); a heartbeat sends due approved rows after a kill switch check, a stop-on-reply check and the gate, with pacing and a daily cap. | `delivery.request_delivery` (sending is a high-risk tool call that waits for an approval bound to the payload hash), autonomy daily caps and quiet hours (`autonomy.py`), suppression list. | Pattern: ANUM already does most of this. Add: stop-on-reply for a lead, per-channel daily quota, and keep autopilot off (the roadmap allows only human-approved sends until P3). |
| Handler (`agents/handler.py`) | Reads replies, classifies intent, honours opt-out phrases in code whatever the model says, handles bounces, trips a global kill switch above a bounce rate. | WhatsApp inbox and triage (`whatsapp.py`, `whatsapp_triage.py`), Email Center and unified inbox (`mailbox/`, `email_inbox.py`), suppression list (STOP already adds an entry). | Pattern: hard opt-out rules in code, and a bounce-rate kill switch for outreach. |
| Email finder (`integrations/email_finder.py`) | Guesses address patterns, verifies with MX lookups and Hunter. | none | **Skip.** The roadmap forbids hidden enrichment of personal contacts; ANUM stores only contacts that were published by the business or given to us. |
| Compliance check (`integrations/precleared.py`) | Optional paid per-recipient check for UK, US, Canada, Germany, France rules. | none | Skip for now; Saudi PDPL and UAE rules need a local adviser (roadmap section 9). |
| Quota (`integrations/quota.py`) | Reads the personal Claude subscription's usage through an undocumented endpoint. | `model_budget.py` | Skip; ANUM meters its own calls. |
| Analyst (`agents/analyst.py`) | Funnel numbers, opt-out rate alarm above 2 percent. | Reports (`reports.py`) | Pattern for the sales KPI report. |

### Crawl4AI (defer; native fetcher first)

- **Repository:** https://github.com/unclecode/crawl4ai, `main`, commit `94b67888ca8b` (15 Oct 2026). `LICENSE`: Apache License 2.0 **with an added section** requiring every distribution or public use, including web applications, to show: "This product includes software developed by UncleCode ... as part of the Crawl4AI project" in an About or Credits section. So it is not plain Apache-2.0.
- **Decision:** defer. P1 needs a few pages per prospect, not a crawler. Write a small read-only `web.fetch_page` tool (httpx, `robots.txt` check, size and time limits, the SSRF guard pattern from `commerce/http.py`, text extraction) and register it like `web_search`. Revisit Crawl4AI only if JavaScript-heavy sites block real work; then run it as a separate container with its own CPU and memory limit (it drives a headless Chromium) and add the attribution to ANUM's About page.

### MarkItDown (adapter later, sandboxed)

- **Repository:** https://github.com/microsoft/markitdown, `main`, commit `4cc9fa17653d` (3 Oct 2026). `LICENSE`: MIT, Copyright (c) Microsoft Corporation.
- **Decision:** adapter when needed. ANUM's file policy accepts `.docx`, `.xlsx`, `.pptx` (`anum_api/file_policy.py`), but the knowledge base only indexes text files. A customer requirements document in Word is exactly what proposal intake needs. Plug point: the retrieval indexing step (`retrieval.py`, `knowledge_base.py`), running MarkItDown in a separate worker process with no network and memory and time limits, after the existing malware scan hook (`FileScanner` in `file_policy.py`). Check MarkItDown's own dependencies' licenses before adding it.

### Chatwoot (defer; separate service if ever)

- **Repository:** https://github.com/chatwoot/chatwoot, default branch `develop`, commit `972567d7442b` (17 Oct 2026). `LICENSE`: MIT ("MIT Expat") for everything outside `enterprise/`; `enterprise/LICENSE` allows production use only under Chatwoot's subscription terms.
- **Decision:** defer. ANUM already has a WhatsApp Business inbox, an email center and unified inbox with AI triage and reply drafts, and department assistants for Support. What ANUM lacks is a **ticket** record (see the gap register). Build a small `business_support_tickets` table on top of the existing inboxes in P2. If a full omnichannel desk is ever needed, run unmodified Chatwoot (MIT parts only) as a separate service on its own database and connect through its API; never copy `enterprise/` code. It is a Rails app with PostgreSQL, Redis and Sidekiq: too heavy for the shared Oracle VM.

### Twenty (reference only)

- **Repository:** https://github.com/twentyhq/twenty, `main`, commit `289976eaeeb7` (17 Oct 2026). `LICENSE`: mostly AGPLv3, with an "additional permission under AGPLv3 section 7" for applications built against its published APIs; files marked `/* @license Enterprise */` are under a commercial license; some packages (CLI, SDKs, `twenty-ui`, apps) are MIT.
- **Decision:** reference only. ANUM's CRM needs are small (accounts, contacts, leads, opportunities, activities) and must live under ANUM's RLS and approvals. Copying AGPL code into ANUM would put network source obligations on the whole product. Read Twenty's data model for ideas; write ANUM's own tables. A separate Twenty instance connected over its API stays possible later (the section 7 permission is aimed at that), after legal review.

### Activepieces (defer)

- **Repository:** https://github.com/activepieces/activepieces, `main`, commit `58ac3b0a8188` (18 Oct 2026). `LICENSE`: MIT for everything outside `packages/ee/` and `packages/server/api/src/app/ee`, which have their own commercial license.
- **Decision:** defer. ANUM's automation already has workflows, schedules, event and webhook triggers, waiting steps, retries, idempotency keys, cancel and resume, and approval-gated steps (`automation.py`, `automation_triggers.py`, [Automation](../automation.md)). Revisit only if a customer needs a connector ANUM lacks; then use an individual MIT "piece" as a reference for one ANUM integration, not the platform.

### n8n (skip for the product)

- **Repository:** https://github.com/n8n-io/n8n, `master`, commit `d2dc2f9ce502` (18 Oct 2026). `LICENSE.md`: the **Sustainable Use License** 1.0 ("You may use or modify the software only for your own internal business purposes or for non-commercial or personal use. You may distribute the software or provide it to others only if you do so free of charge for non-commercial purposes."), plus `LICENSE_EE.md` for `.ee.` files.
- **Decision:** skip as part of ANUM. It cannot be embedded in, or offered with, the paid Commercial OS. Internal use by the owner for the owner's own business is allowed by the text, but it would duplicate ANUM automation, so there is no reason to run it.

### Langfuse (defer)

- **Repository:** https://github.com/langfuse/langfuse, `main`, commit `564f800fb530` (18 Oct 2026). `LICENSE`: MIT for everything outside `ee/`, `web/src/ee/`, `worker/src/ee/`. Copyright line is now "ClickHouse, Inc." (the project changed hands; recheck terms at adoption).
- **Decision:** defer. ANUM already records model usage and cost per tenant and workspace (`model_budget.py`) and exports traces over OpenTelemetry (`telemetry.py`) without prompt content. Self-hosting Langfuse needs PostgreSQL, ClickHouse, Redis and object storage: far more than the Oracle VM's spare capacity. Revisit when answer-quality evaluation becomes a measured need; prompts would then leave ANUM's privacy boundary, which needs a privacy review.

### OpenTelemetry Collector (keep existing)

- **Repository:** https://github.com/open-telemetry/opentelemetry-collector, `main`, commit `ebfdf90d82ab` (16 Oct 2026). `LICENSE`: Apache-2.0.
- **Decision:** keep. ANUM instruments with the OpenTelemetry SDK (`services/api/pyproject.toml`: `opentelemetry-api` 1.45.1 and instrumentations; `anum_api/telemetry.py`) and the development compose runs the contrib collector. On the Oracle VM no collector runs ([Deploying on Oracle](../deploy-oracle.md)); set `ANUM_OTEL_EXPORTER_OTLP_ENDPOINT` to a hosted endpoint when needed.

### Keycloak (keep existing)

- **Repository:** https://github.com/keycloak/keycloak, `main`, commit `2827b3fa10f9` (17 Oct 2026). `LICENSE.txt`: Apache-2.0.
- **Decision:** keep. Realm as code in `infra/keycloak/anum-realm.json`; Keycloak 25 in compose and on the VM. The journey proves PKCE (S256) is required for the web client. Hardening items stay in [Identity](../identity.md) and [Deploying on Oracle](../deploy-oracle.md#keycloak).

### Playwright (keep existing)

- **Repository:** https://github.com/microsoft/playwright, `main`, commit `b335271cdc2b` (17 Oct 2026). `LICENSE`: Apache-2.0, with a `NOTICE` file.
- **Decision:** keep for testing. `@playwright/test` ^1.63.0 runs the web e2e specs (`apps/web/e2e/`). Never use it to automate LinkedIn or any site's sign-in (Harvey does; ANUM must not).

### Documenso (defer)

- **Repository:** https://github.com/documenso/documenso, `main`, commit `87245005651a` (16 Oct 2026). `LICENSE`: GNU AGPLv3. The repository also has a `CLA.md`.
- **Decision:** defer. Early contracts are few: use a manually signed PDF stored in Files and record the signed date on the proposal (an authenticated manual action, as roadmap section 6 requires). If volume grows, run Documenso unmodified as a separate service or use a hosted signing provider, and connect by API; never embed AGPL code in ANUM.

### Appsmith (skip)

- **Repository:** https://github.com/appsmithorg/appsmith, default branch `release`, commit `fd78f12c57ca` (16 Oct 2026). `LICENSE`: Apache-2.0 (only the root file was read; file-level headers were not checked).
- **Decision:** skip. ANUM has its own admin and module pages on web and Flutter with a shared design and contracts; a second admin builder would split the UI and its permission model.

### Graphiti (defer)

- **Repository:** https://github.com/getzep/graphiti, `main`, commit `417c3a125ae3` (17 Oct 2026). `LICENSE`: Apache-2.0.
- **Decision:** defer. ANUM memory is pgvector retrieval with provenance and retention (`retrieval.py`, [Memory](../memory.md)). Graphiti needs a graph database (Neo4j or FalkorDB) and extra model calls per write. Revisit only with measured memory failures (for example, wrong answers about how an account's facts changed over time).

## Estimates (estimates only, not quotes)

Hours are for one experienced developer using ANUM's own tooling; costs in USD per month. "Model cost" assumes the configured provider's pay-as-you-go prices and is capped by ANUM's model budgets.

| Work package | Hours | Infrastructure | Model API cost | Recurring cost |
|---|---|---|---|---|
| Harvey patterns ported as ANUM code (gate, outbox rules, signals, stop-on-reply, bounce kill switch) | 40 to 60 | none new | none for the gate; drafting counted under P1 | none |
| Native `web.fetch_page` tool (robots, SSRF guard, limits, tests) | 16 to 24 | none new (runs in the API) | none (no model call) | none |
| MarkItDown adapter in a sandboxed worker | 16 to 24 | a small worker container, about 0.25 CPU and 256 to 512 MB RAM on the VM | none | none |
| Crawl4AI as a separate container (only if needed) | 16 to 24 | 1 CPU and 1 to 2 GB RAM while crawling | none | VM capacity only |
| Chatwoot, Twenty, Documenso, Langfuse as separate services | not planned | each needs its own database and 1 to 4 GB RAM | none | at least one more VM or a paid plan; out of scope |

Sources (license originals at the commits above): [Harvey](https://github.com/ethanplusai/harvey/blob/main/LICENSE), [Crawl4AI](https://github.com/unclecode/crawl4ai/blob/main/LICENSE), [MarkItDown](https://github.com/microsoft/markitdown/blob/main/LICENSE), [Chatwoot](https://github.com/chatwoot/chatwoot/blob/develop/LICENSE), [Twenty](https://github.com/twentyhq/twenty/blob/main/LICENSE), [Activepieces](https://github.com/activepieces/activepieces/blob/main/LICENSE), [n8n](https://github.com/n8n-io/n8n/blob/master/LICENSE.md), [Langfuse](https://github.com/langfuse/langfuse/blob/main/LICENSE), [OpenTelemetry Collector](https://github.com/open-telemetry/opentelemetry-collector/blob/main/LICENSE), [Keycloak](https://github.com/keycloak/keycloak/blob/main/LICENSE.txt), [Playwright](https://github.com/microsoft/playwright/blob/main/LICENSE), [Documenso](https://github.com/documenso/documenso/blob/main/LICENSE), [Appsmith](https://github.com/appsmithorg/appsmith/blob/release/LICENSE), [Graphiti](https://github.com/getzep/graphiti/blob/main/LICENSE).
