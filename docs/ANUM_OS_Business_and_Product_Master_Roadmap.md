# ANUM OS: Business Model, Product Strategy & Build Roadmap

**Version:** 1.0 | **Prepared:** 17 October 2026 | **Owner:** ANUM OS | **Status:** Strategy and build specification, **not an installed implementation**

> **Central decision:** ANUM OS is one multi-tenant AI work operating system for individuals and businesses, with a built-in agency operating mode for selling custom software, automation, managed services and ANUM licenses. Do not create another unrelated CRM, agent runtime, dashboard, or backend. Productize *results*, not an unbounded list of features.

## 0. The outcome we are building toward

**Two products on one foundation:**
1. **ANUM Internal Business OS:** the owner uses ANUM to find and qualify opportunities, market existing products, prepare quotations, manage projects, build with developer tools, test, deploy, bill, support and retain customers.
2. **ANUM Commercial OS:** customers subscribe to isolated organizations/workspaces containing AI assistants, tasks, knowledge, files, workflows, reporting, customer operations, and optional industry solution packs.

**Three income streams:** (a) fixed-scope projects, (b) recurring managed services and support, (c) product/SaaS subscriptions plus onboarding. Custom projects generate early cash and reusable proof; repeat customer needs determine product modules.

**Near-term business objective:** first legally permissible paying project/pilot, then 3 paid engagements and 1 referenceable case study. **Not a forecast.** Avoid purchasing tools and advertising until real interviews produce buying evidence.

**Hard constraints:** customer-acquisition cash cap SAR 250 for first 30 days, initially SAR 0; reuse Oracle, GitHub, ANUM infrastructure; never disable approvals, tenant RLS, audit, stop/unsubscribe rules, or security scans to make demos look ready. No automatic outbound messages to new contacts without appropriate authorization and human approval. No autonomous contracts, purchases, refunds, payments, deletions, publication, production merges or client data transfers.

---

## 1. Verified starting point (repository inspection, 16 to 17 Oct 2026)

### Correct main project
- **Main OS:** https://github.com/ws123na-afk/Anum_Mine (`main`). README describes FastAPI, React/Vite, Flutter mobile, Tauri desktop, Keycloak, PostgreSQL/RLS, agent runtime, NATS events and workflow boundaries. Relevant code: `services/api/anum_api/`, `apps/web/src/`, `apps/mobile/`, `packages/contracts/`, `infra/oracle/`, `docs/`.
- **Separate voice codebase:** https://github.com/shahzebs-Library/anum-call (voice agent application; do not mistake it for the full OS).
- **Other assets:** https://github.com/shahzebs-Library/Tyre_Pulse ; https://github.com/ws123na-afk/VoiceQuote ; https://github.com/ws123na-afk/Anum360_Store ; https://github.com/ws123na-afk/opsbrain-docs ; https://github.com/ws123na-afk/Anum_Mine.

### Already represented in ANUM source: verify runtime, do not rebuild
- Agent profiles, skills, model gateway, tool execution, risk/approval policies, tasks, events and memory.
- Organization/workspace identity and permissions, PostgreSQL tenant isolation, governance, audits, privacy and marketplace foundations.
- Automations, email/Gmail and WhatsApp integration paths, notifications, files, knowledge and research (`web_search` backed by SearXNG).
- Marketing planner/analytics, commerce connectors, finance, tyre planning, reports and export, voice, web/mobile/desktop surfaces.
- CI/CD, Oracle Compose deployment files, Keycloak, database migrations, backups and optional monitoring.

**Crucial:** presence of source and documentation DOES NOT prove production reliability, complete provider integration, operational credentials, or live environment. For example, Oracle's single-VM deployment uses inline workflows rather than Temporal workers and in-process events rather than NATS, according to `docs/deploy-oracle.md`. Treat missing durable execution as a measured risk, not an excuse to deploy another framework immediately.

### Real defects seen in CI logs (15 October 2026)
Latest inspected failed run: https://github.com/ws123na-afk/Anum_Mine/actions/runs/38814995240
- API unit tests: **3 failed, 2,225 passed**, 9 skipped, 381 deselected. Failures in `services/api/tests/test_commerce.py` for sales read tool, voice answers from synced commerce data, store answers in chat cards (lines approximately 603, 648, 780).
- Security dependency audit: **4 advisory findings** in `apps/desktop/src-tauri/Cargo.lock`, including unmaintained `proc-macro-error`; inspect all advisory IDs and dependency chains before fixing.
- Authenticated journey: SSE stream returned **403 `Active workspace membership required`**; Keycloak log also mentioned missing `code_challenge_method` on a login attempt. Trace the request and test setup, do not weaken membership checks.
- Other jobs, including web/contracts, database persistence, browser e2e, Docker images, integration tests, Flutter, desktop build and Helm tests, passed in that run. Staging deployment success does not override the failing quality gate.

**P0 exit criterion:** root-caused failures fixed on branch, independent tests green, staging smoke test green, and regression evidence linked in PR. Never bypass CI by skipping assertions or changing security policy to satisfy a test.

---

## 2. The business model

### 2.1 Customer types and offers
| Segment | Pain to validate | Starter solution | Buying role | Sales motion |
|---|---|---|---|---|
| Fleets, mixers, workshops, transport | Scattered tyre/maintenance data and weak cost visibility | Tyre Pulse paid 30-day pilot/reporting | Fleet/workshop/operations manager | Direct professional introductions, demos |
| Local small businesses | No credible online presence or inquiry tracking | Business website + inquiry workflow | Owner | Portfolio demo, discovery, fixed quote |
| Retail/e-commerce teams | Slow response, repeated questions, COD/order follow-up | Human-supervised support/operations agent | Owner/ops lead | Demonstration with synthetic data |
| Service businesses | Slow quotation turnaround | VoiceQuote tailored pilot | Owner/operations manager | Problem interview and short demo |
| Spreadsheet-heavy offices | Manual preparation of repetitive reports | Excel/CSV to dashboard/report automation | Manager | Sample input and output |
| Growing teams (later) | Fragmented AI tools, approvals and company memory | ANUM OS team workspace with solution pack | Operations head/IT | Assisted pilot, then subscription |

**Do not target all segments simultaneously.** First commercial tests: (1) spreadsheet/report automation and simple websites for cash flow, (2) Tyre Pulse pilot in familiar industry. Keep ANUM OS as internal operator until safe to sell externally.

### 2.2 Productized service packages (pricing *hypotheses*, not researched market rates)
| Package | Standard deliverables | Suggested starting test price (SAR) | Limitations |
|---|---|---:|---|
| Website Starter | 4 to 6 responsive pages, contact form, basic SEO, handover | 1,000 to 2,500 | Hosting/domain/content/translation separately stated |
| Reporting Quick Win | Import one source format, 1 dashboard, 1 export, walkthrough | 1,500 to 4,000 | Extra data cleanup and integrations out of scope |
| Automation Starter | 1 defined trigger/action workflow, logging, failure notices | 750 to 2,000 | Provider charges paid by client; no critical action without approval |
| AI Support Pilot | One channel, bounded knowledge, answer review, escalation, test cases | 1,500 to 3,500 setup | Message/AI usage and operating support quoted separately |
| Tyre Pulse Pilot | Limited fleet dataset, tyre/replacement/cost report, one review | 750 to 1,500 | Only functions actually tested; no imagined savings guarantee |
| VoiceQuote Pilot | Configured catalog subset, quote approval/export, training | Quote after scoping | Catalog, accuracy, external integrations explicitly bounded |
| Custom Mobile App | Discovery + estimate, milestone-based implementation | 5,000+ only as entry discussion | Never fixed-quote without scope, complexity and delivery capacity |
| Support & Care | Updates, backups, monthly checks and agreed response time | Quote against labor + hosting + SLA | Separate from build price |

**Pricing formula:** project price = estimated labor hours × sustainable internal hourly cost + verified external expenses + contingency + profit. Define revisions, exclusions and change-order rate. Ask deposit/milestone payments where lawful. Never promise 24/7 support without staffing.

### 2.3 ANUM OS SaaS model (only after proof)
- **Solo:** tasks, personal workspace, limited agents/usage, controlled connected apps.
- **Team:** shared workspace, role permissions, approvals, reports, budgets, basic knowledge.
- **Business:** multiple departments, customer-support and operations packs, audit export, more automation, onboarding and support.
- **Enterprise:** separate contract for SSO, residency, dedicated infrastructure, policy/custom integrations and SLA.
- Price **after** 5 to 10 qualified buyer interviews and 2 to 3 paid pilots; bill usage-expensive items (LLM tokens, outbound messages, storage, telephony) transparently with caps. Avoid unlimited AI plans.
- Subscription accounting must support trials, renewals, invoices, credits, cancellations, proration, overages, refunds, and organization-level seats/entitlements; implement only when authorized payment and tax setup are ready.

### 2.4 Unit economics and cash discipline
- Track `revenue`, `cash collected`, `direct provider costs`, `labor hours`, `hosting share`, `support hours`, `gross margin`, `customer acquisition cost`, `retention`, `late payments` by client and by offer.
- Do not count unpaid leads as revenue. Revenue forecasts must show assumptions and scenarios, not invented accuracy.
- Paid third-party apps, enrichment, SaaS CRMs, outbound bulk-email tools, lead databases and extra hosting are **not authorized** by this plan.
- Days 1 to 30: acquisition cash cap SAR 250, target SAR 0; allow SAR 100 only for necessary meetings/travel, preserve SAR 150.

---

## 3. Go-to-market: concrete proof before scaling

### 3.1 Buyer journey
`Offer catalog → relevant companies/opportunities → evidence & qualified need → consentful contact → discovery call → tailored demo → written scope → proposal → contract/payment milestone → delivery → acceptance → testimonial/reference request → maintenance offer`

### 3.2 Lead score (deterministic; no fabricated facts)
Score each 0 to 5: industry fit, verifiable problem indicators, reachable official business channel, apparent project scale, urgency confirmed in conversation, authorized buyer. Sum out of 30. Separate **verified source facts** from **agent inferences**; unknown stays unknown. Do not infer private budgets or scrape private contact information.

### 3.3 First 30 days
- **Week 1:** validate demo functions; publish three portfolio examples using your own/synthetic assets; identify 20 suitable prospects; contact 15 via authorized channels; target 3 discovery conversations and 1 demo.
- **Week 2:** qualify genuine problems; produce 2 to 3 tightly scoped proposals; ask for a purchasing decision; document objections.
- **Week 3:** deliver first paid pilot if contracted; otherwise revise ICP/offer from actual conversations, not AI guesses.
- **Week 4:** gather accepted deliverable, testimonial permission and support opportunity; evaluate conversion metrics; only increase tooling spend if paid demand justifies it.

**Targets are not forecasts.** If there are zero conversations, change channel/message; if conversations but no proposals, change pain/offer; if proposals but no payments, investigate pricing, credibility, procurement and scope.

### 3.4 Channels (order of priority)
1. Existing professional network and referrals, approached appropriately.
2. Company websites and official business inquiry forms; legitimate non-spam outreach.
3. LinkedIn networking/manual permitted prospecting, with no policy-violating scraping or bulk automated DMs: https://www.linkedin.com/.
4. Arabic/global freelance marketplaces only after legal/contracting eligibility: https://mostaql.com/ , https://khamsat.com/ , https://www.upwork.com/.
5. Organic product demonstrations/case studies and helpful content through existing ANUM Marketing.
6. Paid ads only with evidence of offer conversion and positive contribution margin.

### 3.5 Sales scripts/qualification requirements
Ask: current process, recurring pain, cost of problem, must-have outcome, who approves, timeframe, data restrictions, integration complexity, success metric. Do not send full proposals without obtaining requirements. Messages must honestly state capabilities and respect opt-outs.

---

## 4. ANUM OS functional map: everything, not just selling

### A. Foundation platform (shared by every customer)
- Tenant → organization → workspace → user → role → policy. OIDC/SSO, secure invitations, isolated RLS, safe logs, full export and deletion/retention.
- Cross-channel UI: web, Flutter mobile, desktop as thin clients to the same agent/runtime APIs.
- Conversations, agents, skills, memory with proven provenance, files/knowledge, tasks, schedules, approvals, audit.
- Provider abstraction and cost budgets; tool-scoped secrets; no tool may act beyond delegated user permission.
- Fail-safe automation: idempotency, retries, dead letters, timeouts, cancellability and checkpoints.
- Monitoring, backups/restore drills, disaster recovery documentation, release gates and rollback.

### B. Internal commercial operations (first new vertical)
1. **Catalog:** organizations, products, service packages, versions, prices, cost assumptions, screenshots, product maturity and supported integrations.
2. **Lead desk:** sourced organization records, contacts where lawfully obtained, source evidence, fit scores, duplicate detection, ownership, suppression/consent, task and reply history.
3. **Opportunity pipeline:** discovery → qualified → demo → quote → negotiation → won/lost; value, probabilities **entered as assumptions**, next step, last interaction, expected decision date.
4. **Proposal center:** requirements intake, scoped statement of work, milestones, deliverables, exclusions, rights/license terms, change requests, acceptance criteria, project margin projection.
5. **Sales outreach:** research/draft/manual review first; opt-out, sending quotas, identity truthfulness, do-not-contact registry, per-channel rules, local jurisdiction rules, delivery/bounce audit. No cold AI calling.
6. **Project delivery:** contract → project → milestones → GitHub repository/issue/PR → CI & test → staging review → acceptance → invoice → handover. Separate customer IP/secrets and never leak between projects.
7. **Customer portal:** approvals, files, status, issues, support requests, release notes, invoices (if payment integration authorized), knowledge base, feedback.
8. **Finance & client profitability:** deal estimates, invoices, expenses, recurring charges, usage, profitability, arrears and owner approvals.
9. **Growth reporting:** pipeline, funnel, source ROI, margin, delivery time, support time, churn, customer satisfaction; never fabricated metrics.

### C. Customer-facing ANUM commercial OS
- AI work assistant, research (source-linked), multilingual knowledge and document understanding, tasks and schedules, agent permissions, templates and reports.
- Optional verified packs: customer support, commerce/COD, maintenance/fleet, HR/workforce, finance analysis (read-only by default), internal reporting.
- Marketplace/skills with reviewed manifests, permission request and signed versions, rollback, owner authorization; no unvetted third-party code execution.
- Customer billing and self-service onboarding only after tenant isolation, support, metering and legal release gate pass.

### D. Quality, support & governance (non-negotiable)
- Support tickets/bug reports, incident severity, change log, status/health, SLA policy based on actual capacity.
- Security: RLS cross-tenant tests, OAuth state/PKCE, prompt-injection controls, SSRF, secret rotation, least privilege, upload scanning/limits, code/license dependency scans.
- Privacy: consent, regional data processing, retention, export/deletion, marketing opt-outs, audit retention and documented subprocessors.
- Operations: cost ceiling per tenant and model, runaway-agent kill switch, service health, disaster recovery exercises.

---

## 5. Repository reuse decision register

**Rule:** Compare existing ANUM feature vs lightweight library vs external service. Prefer **existing ANUM** and isolated APIs; do not merge entire incompatible codebases. All licenses must be checked at the exact commit and reviewed for commercial embedding before copying.

| Candidate (link) | Function | Proposed treatment | Legal/technical warning |
|---|---|---|---|
| **ANUM itself** https://github.com/ws123na-afk/Anum_Mine | agents, tasks, workflows, reports, marketing, integrations, permissions | **KEEP AND EXTEND, P0** | validate source against deployment |
| **Harvey** https://github.com/ethanplusai/harvey | lead discovery, profiling, scores, draft/outbox/reply workflows | **EXTRACT PATTERNS, PROTOTYPE P1** | MIT per README; verify current code, usage of Claude CLI, source ToS; no autonomous spam or assumption of free unattended AI |
| **Crawl4AI** https://github.com/unclecode/crawl4ai | crawl public sites into structured/LLM-ready data | **P1 narrow adapter only if ANUM research insufficient** | Apache-2.0; robots/ToS/rate limits, SSRF guard, resource load |
| **MarkItDown** https://github.com/microsoft/markitdown | documents to text/Markdown | **P1 lightweight import adapter if needed** | MIT; sandbox untrusted file parsing |
| **Chatwoot** https://github.com/chatwoot/chatwoot | omnichannel support inbox and ticket handling | **EVALUATE P2 standalone integration** | commercial/license terms vary; not free to white-label by assumption; operational overhead |
| **Twenty** https://github.com/twentyhq/twenty | CRM/contact/opportunity platform | **REFERENCE or separate API P2; not wholesale merge** | AGPL core + Enterprise parts; review obligations carefully |
| **Activepieces** https://github.com/activepieces/activepieces | workflow automation/connectors | **DEFER unless ANUM automation demonstrably lacks critical capabilities** | MIT core, enterprise features commercial |
| **n8n** https://github.com/n8n-io/n8n | automation | **INTERNAL-ONLY EVALUATION; not default embedded OS** | Sustainable Use License restricts many customer-facing embedding/white-label scenarios |
| **Langfuse** https://github.com/langfuse/langfuse | LLM tracing and evaluation | **DEFER: existing OpenTelemetry first** | extra DB/operations and licensing review |
| **OpenTelemetry** https://github.com/open-telemetry/opentelemetry-collector | observability | **KEEP existing instrumentation** | Apache-2.0; avoid duplicate stack |
| **Keycloak** https://github.com/keycloak/keycloak | identity and SSO | **KEEP existing** | Apache-2.0; harden and test current deployment |
| **Playwright** https://github.com/microsoft/playwright | browser E2E testing | **KEEP existing tests** | Apache-2.0 |
| **Documenso** https://github.com/documenso/documenso | e-signature | **DEFER until contract volume; use manual signed PDFs initially** | AGPL-3.0; check any commercial requirements |
| **Appsmith** https://github.com/appsmithorg/appsmith | admin dashboards/internal tooling | **SKIP for now; ANUM already has admin UI** | duplicate frontend/admin platform |
| **Graphiti** https://github.com/getzep/graphiti | temporal knowledge graph | **DEFER until measured memory failures** | added infrastructure/data governance complexity |

**Do not claim a whole repository is legally reusable because GitHub lists it as public.** Check repo `LICENSE`, file-level headers, nested package notices, transitive dependency licenses and third-party service terms. Maintain `THIRD_PARTY_NOTICES.md` and a software bill of materials (SBOM).

---

## 6. Technical architecture and data model

### Preserve ANUM boundaries
`Web / Flutter / Tauri → ANUM API (OIDC + tenant/workspace authorization) → domain services + existing agent runtime + approvals → PostgreSQL RLS / files / events → scoped external adapters`

- New commercial feature under **ANUM internal modules**, not a replacement app. Propose `services/api/anum_api/business/` with focused submodules `catalog`, `leads`, `opportunities`, `proposals`, `projects`, `support`, `billing_contracts`; reuse current `marketing/`, `reports.py`, `automation.py`, `delivery.py`, integrations and model budgets.
- Frontend under a coherent **Business** navigation with consistent web and Flutter contracts. Do not duplicate entire admin or finance UI.
- Database schema proposal (validate against existing migrations and naming first): `business_products`, `business_offers`, `business_accounts`, `business_contacts`, `business_leads`, `business_evidence`, `business_opportunities`, `business_activities`, `business_consents`, `business_proposals`, `business_proposal_versions`, `business_projects`, `business_milestones`, `business_change_orders`, `business_support_tickets`, `business_contracts`, `business_entitlements`, `business_usage_ledger`.
- Every business row keyed and policy-scoped by `tenant_id`, `workspace_id` where relevant. For internal sales, all records belong to the owner's business tenant only. Provider IDs and external URLs are untrusted and cannot cross tenant boundaries.
- `Lead` state: discovered / researched / qualified / contacted / replied / meeting / offered / won / lost / suppressed. Preserve activity event trail, source URL, timestamp, consent basis, reason for score and last verified date.
- `Proposal`: immutable accepted version, currency, taxes marked tentative pending verified jurisdiction, external costs, payment milestones, accepted scope, no invisible assumptions. Signed/approved status must require authenticated manual action or trusted signature provider.
- `Project`: linked accepted proposal, owner, milestone acceptance, change requests, release artifacts and contract limits. Customer data stays isolated from agent scratchpads and demos.
- `Support`: requester identity, entitlement, ticket severity, data visibility, escalations and resolution audit.
- Use transactional outbox/idempotency for external side effects; approval tokens bound to exact content, recipient, amount and actor. Retry sends only with idempotency keys.

### Lead research execution loop
`search query → candidate URL/source → safely fetch public evidence → deduplicate organization → qualify with factual citations → draft fit explanation → human review → enqueue approved next step → log activity → track opt-outs and responses`

- Evidence source (URL/title/date/excerpt) and confidence stored separately from prospect record. Unsupported claims become `unknown`.
- No hidden enrichment of personal phone numbers; no circumventing captchas, login, rate limits or robots rules. Respect platform API terms.
- P1 **read-only research**, P2 **drafts**, P3 **human-approved sends**, P4 limited automation on opt-in conversations only.

### Developer agency execution loop
`Lead → scoped estimate → signed agreement → deposit/milestone → project board → repository branch → code/test → PR approval → staging → acceptance → approved deploy → documentation → support`

- Never let an AI push/merge/deploy directly to production, spend provider credits, or expose third-party credentials without human approval.
- GitHub integration limited to named repositories and least-privilege scopes; record commit SHA, PR, CI run, reviewer and rollback revision.

---

## 7. Implementation roadmap with objective gates

### P0. Stabilize ANUM (week 0 to 2, before selling ANUM subscriptions)
- Reproduce/repair the 3 commerce test failures; inspect expected tool invocation and responses. Repair auth journey's workspace SSE 403 while preserving authorization; inspect PKCE login issue. Resolve all 4 Rust advisories after identifying exact dependencies. Run required CI and regression tests.
- Run staging health, database migration dry-run, restore drill, login/invite/workspace isolation smoke test; verify production staging distinction and no secrets leaked.
- **Exit:** all relevant CI green, no unresolved critical/high advisories without reviewed exception, staging works; rollback documented.

### P1. Internal sales MVP (weeks 1 to 4, feature flags; can develop alongside P0 but never release externally first)
- Offer/service catalog with three packages plus Tyre Pulse.
- Lead/opportunity pipeline, manual CSV import, verified-source research via existing `web_search`; dedup, evidence and do-not-contact.
- Agent-generated qualification and approved message/proposal drafts with deterministic validation and budget cap.
- Basic proposal PDF/Markdown or existing report export, client milestone tracker, sales KPI summary.
- Pilot Harvey prospecting **in sandbox and with no outbound sending**; port only small components shown better than ANUM alternatives.
- **Exit:** using synthetic data, a user can create product → lead → qualification → draft → approved proposal → won opportunity → project without errors; all writes tenant-protected; sends disabled by default.

### P2. Agency operations (weeks 4 to 8 after first qualified pipeline)
- Detailed quoting/cost estimator, customer file intake, change orders, GitHub project linkage, QA templates, delivery/handover, support ticket and monthly maintenance ledger.
- Existing Gmail/WhatsApp integrations may be used for inbound replies/approved communication, after configuration and provider authorization; do not force a new inbox tool.
- **Exit:** one client engagement can be administered with transparent costs and acceptance; no missing approval for external action.

### P3. Paid pilots and evidence (weeks 6 to 12)
- Run 2 to 3 real, contractually authorized small pilots with correct consent and data controls; gather actual support time, margin, ROI/effort baseline, failure rate and customer feedback.
- Fix product gaps that recur across customers. Avoid unrelated module expansion.
- **Exit:** demonstrable business value and reliable delivery, documented customer permissions for any testimonials.

### P4. Commercial ANUM SaaS beta (months 3 to 6, conditional)
- Organization signup/trial, tenant provisioning, actual usage metering, seats/entitlements, terms, privacy controls, billing integration with manual fallback, subscription cancellation, invoices, support and audit export.
- Product tours, onboarding, usage caps and incident response; verified multi-tenant penetration tests and backup/restore. One region before multi-region marketing.
- **Exit:** first paying ANUM tenant completes onboarding safely and continues paying; no cross-tenant leakage and metering is correct.

### P5. Scale only after retention (months 6 to 12+, conditional)
- Industry solution packs, partner/referral program, marketplace, more connectors, enterprise policies, service-level commitments, dedicated deployments where justified.
- Scale orchestration from inline to Temporal/NATS only when measured reliability/load requirements exceed simple infrastructure; avoid doubling services prematurely.

---

## 8. Tests and acceptance criteria Claude must implement

### Core security
- Create tenant A and B; every read/list/insert/update/export/agent/tool/attachment/realtime access from A to B must fail, including guessed IDs and long-running jobs.
- Owner/member/viewer roles enforced server side; detached/expired memberships rejected in SSE and REST; OAuth PKCE exercised through actual login.
- Prompt injection in scraped webpages, customer files, messages must not grant tool access or change system instructions; external text is data only.
- Secret masking, token rotation, approval binding and SSRF/rate limit tests; do not trust agent output as authorization.

### Sales
- Lead dedup, evidence trace, confidence/unknown labels; consent/opt-out and bounce suppression honored before queue/send.
- Approval required for first outbound, all pricing promises, contracts, external spending; reject changed message/recipient after approval.
- Idempotent activity and delivery records; failed sends cannot be counted as successful contacts.
- Proposal subtotals, currencies, service charges, taxes, internal cost and revisions reconciled; no unapproved scope changes.

### Project delivery
- One lead converted to signed scope, project milestones, GitHub PR and accepted artifact with real audit chain; no production merge or deployment before authorization.
- Multi-client isolation in documents, builds, logs and support; API and UI tests.

### Operational
- AI tool call limits and cost ceilings tested; emergency kill switch tested. Scheduled jobs do not duplicate on process restart. Restore drills show actual RPO/RTO limits. CI and staging must gate production.

### Measurable business analytics
- Lead→qualified→meeting→proposal→won; conversion rates use denominator and time window, not vanity numbers. Revenue vs cash collected clearly separated. Margin includes direct variable cost and estimated labor. Monthly SaaS metrics only when actual subscriptions exist.

---

## 9. Legal and commercial prerequisites (confirm with qualified local adviser)

- **Saudi employment/business authorization:** the owner's nationality, residence and employment status make business establishment, invoicing and independent contracting rules a real gating item. Do not assume a Saudi freelance permit covers non-Saudi residents. Verify the permitted legal structure, employer restrictions, any required foreign-investment and commercial registration, profession/activity authorization before taking paid Saudi projects.
- **Tax/invoices:** validate ZATCA VAT registration threshold, invoicing requirements and any applicable e-invoicing phase before issuing invoices. Tax status and rates are jurisdiction-specific.
- **Privacy and marketing:** PDPL and SDAIA implementing rules, opt-out, lawful basis and restrictions on direct marketing. Keep evidence and an actionable suppression list. For international customers, review GDPR/other local requirements.
- **Customer contracts:** signed scope, IP ownership and license rights, hosting and provider responsibility, data controller/processor split, confidentiality, change requests, warranties, acceptance, refunds, support, liability and termination.
- **Third-party terms:** verify LinkedIn, Google, search providers, messaging, email, marketplace and model-provider terms; do not assume a Claude Code subscription allows unattended server automation.
- **Commercial open source:** Apache/MIT notices, AGPL network obligations, mixed licenses, embedded product restrictions, source availability and attribution.

Primary navigation for verification:
- Saudi business / investment: https://misa.gov.sa/ and https://mc.gov.sa/
- Saudi ZATCA: https://zatca.gov.sa/
- Saudi data governance: https://sdaia.gov.sa/
- Saudi freelance portal: https://freelance.sa/
- Saudi PDPL resources: https://dgp.sdaia.gov.sa/

This section is a launch checklist, **not legal advice**. Do not block internal prototypes while checking, but do not invoice or conduct disallowed marketing before eligibility is resolved.

---

## 10. Costs and infrastructure

- Reuse `infra/oracle/` and current GitHub Actions, PostgreSQL, Keycloak, SearXNG, Caddy and existing monitoring. Verify actual free-tier resources and live container capacity, not just README claims.
- Before adding any service, require a resource estimate (CPU, RAM, persistent storage, network, backups), monthly provider cost, operations burden and exit/rollback strategy.
- No new instance just to host Harvey, Twenty, Chatwoot, Langfuse, n8n or Activepieces. Test libraries and small adapters locally first.
- Add per-agent, per-workspace and total hard monthly spend caps; throttle/stop research and models when caps hit. Low-confidence research should consume less AI, not more.
- Customer contracts must state who pays hosting, paid AI, third-party communications, domain, storage and taxes.

---

## 11. Detailed Claude execution contract (copy/paste)

> **You are the lead architect, senior engineer, product manager and release reviewer for ANUM OS.** Read this entire roadmap and the existing repo `ws123na-afk/Anum_Mine`. The objective is to grow ANUM into an internally useful agency/business operating system and, later, a safe multi-tenant SaaS. **Do not create a new app, restart the project, redesign finished screens, or install everything in this document.**
>
> **First: audit** current branch, recent CI, real architecture, database models/migrations, RLS, agent runtime, tools, approval paths, automation, marketing, reports, email, support, frontend/mobile and Oracle deployment. Return a gap register with `existing/working`, `existing/unverified`, `broken`, `missing` and `not-needed`, with file paths and evidence. Explicitly compare source vs actual deployed configuration.
>
> **Second: repair P0**. Reproduce three commerce assertions, the authenticated SSE 403/PKCE path, and all four Rust security findings. Repair underlying defects without weakening security; rerun tests. State any failures honestly.
>
> **Third: reuse-before-build.** For every missing capability, search reputable maintained repositories, read exact licenses, audit dependencies and test a minimal integration; choose between existing ANUM, small permissively licensed library, isolated external service or bounded native feature. Prefer existing ANUM. Do NOT embed AGPL, source-available or Enterprise code into the commercial OS without explicit legal/license approval. Track license notices and operating costs. Do not claim zero API cost based only on a project's README.
>
> **Fourth: implement P1 in a NEW FEATURE BRANCH**. Add unified internal Business workspace: verified product/service catalog, evidence-backed prospect/account list, opportunity pipeline, source-aware qualification, opt-outs/consent, approved outbound drafts (sending OFF), proposal/estimate versioning, project milestone tracking, owner analytics and cost caps. Reuse existing ANUM agent runtime, model gateway, PostgreSQL/RLS, approvals, marketing, reports, tasks, and frontend design. Share contracts with Flutter where relevant. Implement migrations with backward compatibility and rollback/downgrade procedure.
>
> **Fifth: validate** comprehensive unit/integration/e2e/RLS/security tests; synthetic first-customer journey; accessibility/mobile responsive checks; cost ceilings; backups; CI; staging. No unapproved external mail, scraping that violates provider terms, automated purchases, production deployment or main branch merge.
>
> **Deliverables on each phase:** a) exact files changed, b) before/after feature map, c) licenses and source links, d) commands to install/run/test, e) screenshots or test traces where available, f) cost estimates and assumptions, g) failed tests and risks, h) PR link, i) rollback steps, j) acceptance checklist. Flag unknowns and do not fabricate claims or demos.
>
> **Stop after P0 diagnosis + proposed P1 file/migration plan for explicit owner approval before substantial edits.** Preserve everything currently working.

---

## 12. First actionable work order

**Order 001: Technical baseline and business-enablement audit**
- Inspect `main` HEAD, failing workflow run 38814995240 (or newer current failure), deployed environment configuration and existing modules.
- Deliver `docs/business/ANUM_BUSINESS_GAP_REGISTER.md`, `docs/business/REUSE_DECISION_REGISTER.md`, `docs/business/P0_CI_REMEDIATION.md`, `docs/business/P1_ACCEPTANCE_TESTS.md` in a **draft PR** on a new branch, without production changes.
- Identify which source modules already implement CRM-like records, customer support, invoicing, sales and lead workflow, instead of assuming absent from filenames.
- Provide clear estimate by work package (hours, infrastructure, model API costs, recurring cost).
- Request approval for repair and implementation scope only after evidence review.

**Order 002: P0 repair**, after review.
**Order 003: P1 internal Business MVP**, after P0 and a reviewed implementation plan.

### Definition of success
The owner can demonstrate, from one ANUM workspace with no paid add-on, the full documented flow: catalog offer → sourced potential customer → qualification → owner-reviewed draft → scoped price and proposal → accepted project → developer task → QA/handover → support follow-up, with no unapproved communication, no cross-tenant data exposure and an auditable cost ledger. **Payment collection and public SaaS subscriptions remain separately gated.**

---

## 13. Sources / source-of-truth links

**Existing ANUM project docs:**
- https://github.com/ws123na-afk/Anum_Mine/blob/main/README.md
- https://github.com/ws123na-afk/Anum_Mine/blob/main/docs/product-vision.md
- https://github.com/ws123na-afk/Anum_Mine/blob/main/docs/roadmap.md
- https://github.com/ws123na-afk/Anum_Mine/blob/main/docs/production-readiness.md
- https://github.com/ws123na-afk/Anum_Mine/blob/main/docs/deploy-oracle.md
- https://github.com/ws123na-afk/Anum_Mine/blob/main/docs/agents.md
- https://github.com/ws123na-afk/Anum_Mine/blob/main/docs/marketing.md
- https://github.com/ws123na-afk/Anum_Mine/blob/main/docs/reports.md
- https://github.com/ws123na-afk/Anum_Mine/blob/main/docs/automation.md
- https://github.com/ws123na-afk/Anum_Mine/blob/main/docs/governance-and-scale.md
- https://github.com/ws123na-afk/Anum_Mine/actions/runs/38814995240

**Third-party license originals:**
- https://github.com/ethanplusai/harvey/blob/main/LICENSE
- https://github.com/activepieces/activepieces/blob/main/docs/about/license.mdx
- https://github.com/twentyhq/twenty/blob/main/LICENSE
- https://github.com/n8n-io/n8n/blob/master/LICENSE.md
- https://github.com/documenso/documenso/blob/main/LICENSE

**Research caution:** some README details change quickly; the exact upstream version/commit must be pinned and checked at actual integration time. This plan is not proof that any of these optional systems has been installed or works with ANUM.