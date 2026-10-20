# ANUM business gap register (Order 001)

What ANUM already has, and what is missing, for every capability in section 4 of the [business master roadmap](../ANUM_OS_Business_and_Product_Master_Roadmap.md). Checked on 18 October 2026 against `main` at `3a10bdb`, by reading the code, migrations, tests and CI results, not by file names.

Part of Order 001: [P0 remediation](P0_CI_REMEDIATION.md), [reuse decisions](REUSE_DECISION_REGISTER.md), [P1 acceptance tests](P1_ACCEPTANCE_TESTS.md).

## What the statuses mean

| Status | Meaning |
|---|---|
| `existing/working` | Code, tests and CI on `main` show it working (unit, PostgreSQL and e2e jobs green on run 39119380316). Still not proof it runs on the owner's VM. |
| `existing/unverified` | Code exists, but nothing shows it working end to end here: it needs provider credentials, a device, or the live VM. |
| `broken` | Code exists and a check shows it failing. |
| `missing` | Nothing in the code does it. Parts that could be reused are named. |
| `not-needed` | Not needed for the business plan now. |

## Headline findings

1. **There is no CRM in ANUM.** No table, route or screen holds leads, prospects, opportunities, quotes, proposals, contracts, invoices or support tickets. Searching `services/api/anum_api` for `opportunit`, `milestone`, `crm` and `ticket` finds nothing business related; `invoice` and `quotation` appear only as words the email and WhatsApp triage use to label incoming messages (`mailbox/ai.py`, `whatsapp_triage.py`).
2. **The building blocks for a safe one are already strong.** Approvals bound to a payload hash, a contact suppression (opt-out) list, a governed delivery path for outbound messages, autonomy caps and quiet hours, team work items, reports with CSV and Markdown export, department assistants (including Sales and Support), developer mode for GitHub pull requests, and model budgets all exist and are tested.
3. **"Customers" in ANUM today means three different things**, none of them a sales account: customer *organisations* are tenants the platform operator onboards (`organisations.py`); commerce *customers* are marketplace buyers, deliberately minimised to city and country (`commerce/models.py`, `Customer`); WhatsApp *contacts* are chat partners (`whatsapp_store.py`, `Contact`).
4. **Finance is personal finance**, not business invoicing: the ledger, safe-to-spend and paper trading in `finance/` never issue an invoice.
5. **One defect class found and fixed in this PR:** writes committed after the response was sent, which made the authenticated journey fail intermittently ([P0 remediation](P0_CI_REMEDIATION.md)).

## A. Foundation platform

| Capability | Status | Evidence | Gap or next step |
|---|---|---|---|
| Tenant, organisation, workspace, user, role, policy | existing/working | `identity.py`, `authorization.py` (`Permission`, `Role`, `OrganizationRole`), `workspace_members.py`, `organisations.py`; migrations `0003_workspace_memberships`, `0020_tenants_rls`, `0039_team_work`; `tests/test_postgres_rls.py` | none for P1 |
| OIDC / SSO | existing/working | Keycloak realm `infra/keycloak/anum-realm.json`; `dependencies._oidc_identity`; CI "Authenticated journey" signs in with authorization code plus PKCE | Social sign-in needs owner keys ([Identity](../identity.md)) |
| Secure invitations | existing/working | single-use, email-bound invitations (`workspace_members.py`, migration `0006_workspace_invitations`, [Identity](../identity.md)) | none |
| Isolated RLS | existing/working | forced RLS on tenant tables, non-superuser app role in tests and journey; `tests/test_postgres_rls.py`, `test_postgres_control_plane.py` | Every new `business_*` table needs the same policies and tests |
| Safe logs | existing/working | `telemetry.py` never records prompts, bodies or tokens; `anum_` prefixed log fields | none |
| Export and deletion, retention | existing/working | `privacy.py` (download and delete my data), `tenant_lifecycle.py` (whole-organisation export to a signed download, organisation and workspace deletion with cooling-off, cancel, batched purge and receipt; [Organisation export and deletion](../organisation-lifecycle.md)), retention purges (`maintenance.py`, `voice_retention.py`, `conversation_retention.py`), audit export (`audit_log.py`) | Keycloak accounts of a deleted organisation are removed by the operator; no import of an export |
| Web, Flutter, desktop as thin clients | existing/working (builds) / existing/unverified (devices) | `apps/web/src/*View.tsx`, `apps/mobile/lib/features/`, `apps/desktop`; CI builds all three | Device runs are owner actions |
| Conversations, agents, skills, memory with provenance | existing/working | `conversations.py`, `agent_profiles.py`, `agent_skills.py`, `memory.py`, `retrieval.py`, `prompt_provenance.py` (untrusted text labelled as data) | none |
| Files and knowledge | existing/working | `files.py`, `knowledge_base.py`, `file_policy.py` | Office files are stored but their text is not indexed (`retrieval.is_text_file`); see MarkItDown in [reuse decisions](REUSE_DECISION_REGISTER.md) |
| Tasks, schedules, approvals, audit | existing/working | `tasks.py`, `automation.py`, `approval_integrity.py` (payload hash), `approval_routing.py` (amount bands), `audit.py` | none |
| Provider abstraction and cost budgets | existing/working | `model_gateway.py`, `anthropic_gateway.py`, `model_budget.py` (tenant and workspace monthly USD and token caps) | No per-agent cap; roadmap section 10 asks for per-agent, per-workspace and total caps |
| Tool-scoped secrets; no tool beyond the user's permission | existing/working | encrypted secrets (`secret_box.py`, Fernet), `agent_policy.py`, `tool_governance.py`, `assistants.narrow_for_assistant` | none |
| Fail-safe automation: idempotency, retries, timeouts, cancel, checkpoints | existing/working | `idempotency.py`, `automation.py` (retries, wake and recovery), `durable_runs.py`, run checkpoints (migration `0004_run_checkpoints`), `inline_runs.py` | No dead-letter queue on the VM path (no NATS there); failed runs stay visible as failed |
| Monitoring, backups, restore drills, DR docs, release gates, rollback | existing/working (code) / existing/unverified (VM) | `infra/oracle/backup.sh drill`, `monitoring.sh`, [Monitoring the Oracle VM](../monitoring-oracle.md), [Runbooks](../runbooks.md), `.claude/skills/anum-release-gate` | A real restore drill on the VM is not recorded (P0 open item) |

## B. Internal commercial operations

| # | Capability | Status | Evidence (what exists today) | Gap or next step |
|---|---|---|---|---|
| 1 | Catalog: products, service packages, versions, prices, cost assumptions, maturity | missing | `commerce/models.py` `Product` and `Listing` are marketplace stock items (tyres, SKUs, channel prices), not service offers; `blueprints/shahzeb_business_group.json` describes a workspace setup, not offers | New `business_products`, `business_offers` (versioned, currency, cost assumptions, maturity). Seed: Website Starter, Reporting Quick Win, Automation Starter, Tyre Pulse pilot. |
| 2 | Lead desk: organisations, lawful contacts, evidence, fit score, dedup, owner, suppression, history | missing (suppression existing/working) | `suppression.py` (opt-out list, hashed, per channel and purpose, STOP keywords, export); `web_search.py` (SearXNG, read-only tool); no lead or evidence record anywhere | New `business_accounts`, `business_contacts`, `business_leads`, `business_evidence`, `business_activities`. Reuse suppression for do-not-contact. Add a page fetch tool ([reuse decisions](REUSE_DECISION_REGISTER.md#crawl4ai-defer-native-fetcher-first)). |
| 3 | Opportunity pipeline | missing | `team_work.py` work items have status, due date, assignee, but no value, stage or probability | New `business_opportunities` with stages discovery to won or lost; value and probability stored as assumptions entered by a person. |
| 4 | Proposal center: requirements, scope, milestones, exclusions, rights, change requests, acceptance, margin | missing | Reports can render Markdown and CSV and print to PDF (`reports.py`, [Reports](../reports.md)); files hold attachments | New `business_proposals`, `business_proposal_versions` (accepted version immutable), `business_change_orders`. Export through the report renderer. |
| 5 | Sales outreach: draft first, opt-out, quotas, truthful identity, channel and local rules, bounce audit | existing/working (send path) / missing (sales layer) | `delivery.py` (every outbound send is a high-risk tool call that waits for an approval bound to the payload hash); `suppression.py`; `autonomy.py` (first contact always asks, daily caps, quiet hours, blocked recipients); Email Center SMTP (`mailbox/outgoing.py`); WhatsApp templates (`whatsapp.py`); marketing publish approvals (`marketing/publisher.py`) | Missing: drafts tied to a lead, a deterministic pre-send gate, per-channel sales quota, stop-on-reply, bounce-rate kill switch. Sending stays off in P1. |
| 6 | Project delivery: contract to project, milestones, GitHub repo, issue, PR, CI, staging, acceptance, invoice, handover | existing/working (GitHub part) / missing (project record) | `developer.py`, `developer_projects.py`, `developer_github.py`: per-project repository and token, coding agent on a branch, draft PR, CI read back, merge only after owner approval and green CI; `team_work.py` work items with checklists | New `business_projects`, `business_milestones` linking an accepted proposal to developer projects and work items. Invoice step is missing (B8). |
| 7 | Customer portal: approvals, files, status, issues, support requests, release notes, invoices | existing/working (offers, change orders, invoices, requests, approvals; [portal](../business-portal.md)) / missing (files, release notes) | Customer organisations (`organisations.py`) give a customer company its own tenant, but that is for customers *using* ANUM, not a portal onto a project the owner delivers | P2 at the earliest. A portal must not open the owner's tenant to customers; likely a viewer membership in a per-client workspace. |
| 8 | Finance and client profitability: estimates, invoices, expenses, recurring charges, usage, arrears | existing/working (invoices with manual payments, usage ledger, profitability, overdue summary; [invoices](../business.md#invoices)) / missing (recurring charges, credit notes, payment collection) | `finance/` is the owner's personal ledger (no invoices); model usage and cost per workspace in `model_budget.py`; commerce fees per order (`commerce/models.py` `FeeRecord`) | New usage ledger per client (`business_usage_ledger`) and invoice records later. No payment collection (roadmap: separately gated). |
| 9 | Growth reporting: pipeline, funnel, source ROI, margin, delivery and support time, churn, satisfaction | missing (sales) / existing/working (marketing) | Marketing analytics and weekly report (`marketing/analytics.py`); generic reports with snapshots (`reports.py`) | A `business.pipeline` report generator with denominators and time windows (roadmap section 8). |

## C. Customer-facing ANUM commercial OS

| Capability | Status | Evidence | Gap or next step |
|---|---|---|---|
| AI work assistant, source-linked research, multilingual knowledge, tasks, schedules, agent permissions, templates, reports | existing/working | Assistant chat (`conversations.py`, `chat_tools.py`), `web_search` with sources, English and Arabic UI ([Web localization](../web-localization.md)), knowledge base, agents, setup plans (`setup_plans.py`) | Office document text extraction (see A) |
| Packs: customer support, commerce and COD, fleet, HR, finance (read-only), internal reporting | existing/working (commerce, tyre, finance read-only, support assistant) / missing (HR) / existing/unverified (providers) | `commerce/` (Amazon, noon, own store; live sync needs owner credentials), `tyre/`, `finance/` (read-only connectors, no money moves), Support department assistant (`assistants.py`), one business blueprint | HR pack missing; not needed for P1 |
| Marketplace and skills: reviewed manifests, permissions, signed versions, rollback | existing/working (catalog, install, versions) / missing (signing of skill packages) | `governance.py`, `skills_api.py`, `phase5.py` (marketplace packages and installs, tenant-level), [Governance and scale](../governance-and-scale.md) | Container images are signed; skill packages are not. Not needed before P5. |
| Customer billing and self-service onboarding | missing | Plans are a placeholder (`trial`, `standard`, `enterprise`, "billing is not connected", [Customer organisations](../customer-organisations.md)); `self_sign_up` setting defaults off | P4, after the legal release gate |

## D. Quality, support and governance

| Capability | Status | Evidence | Gap or next step |
|---|---|---|---|
| Support tickets, bug reports, incident severity, change log, status, SLA | existing/working (tickets, severity, working-hours SLA) / existing (runbooks, health) / missing (change log, status page) | Business support tickets ([Business: support tickets](../business.md#support-tickets), migration `0076_business_support`): severity, status flow, assignee, entitlement, escalation, resolution audit, SLA from the owner's working-hours policy, tickets linked to inbound email and WhatsApp, replies through the inboxes' approval paths; `/health`; [Runbooks](../runbooks.md); error reporting (`error_reporting.py`) | A customer-facing status page and change log; tickets opened by customers themselves (portal, P2 at the earliest) |
| RLS cross-tenant tests | existing/working | `tests/test_postgres_rls.py`, CI "API PostgreSQL persistence tests" | Extend to every `business_*` table |
| OAuth state and PKCE | existing/working | journey's `check_pkce_is_required` and S256 sign-in | none |
| Prompt-injection controls | existing/working | `prompt_provenance.py`, [Threat model](../threat-model.md) | Add scraped-page cases when the fetch tool lands |
| SSRF | existing/working | `model_egress.py` endpoint policy; `commerce/http.py` `owner_url_policy`; `web_search` address fixed by the operator | Reuse for the page fetch tool |
| Secret rotation, least privilege | existing/working | `rotate_secrets.py`; narrow NOLOGIN roles (`anum_maintenance`, `anum_outbox_relay`) | none |
| Upload scanning and limits | existing/working (limits, type sniffing) / missing (scanner) | `file_policy.py` size and type checks; `FileScanner` hook "No scanner ships with ANUM" | Install a scanner before customers upload files (P2) |
| Code and license dependency scans | existing/working (vulnerabilities) / missing (licenses, SBOM in repo) | CI "Security scans": pip-audit, pnpm audit, OSV-Scanner, cargo audit, bandit, gitleaks; CycloneDX SBOM in deploy workflows | No license scan and no `THIRD_PARTY_NOTICES.md` yet |
| Privacy: consent, retention, export and deletion, marketing opt-outs, audit retention, subprocessors | existing/working | `privacy.py`, `suppression.py` (purpose `marketing`), [Privacy](../privacy.md), [Privacy policy template](../privacy-policy.md) | Consent basis per lead (P1) |
| Cost ceiling per tenant and model; runaway-agent kill switch | existing/working | `model_budget.py` refuses calls over budget; per-agent monthly cost caps and the organization's emergency stop for all agents and sends in `agent_controls.py` ([Agent controls](../agent-controls.md), migration `0095_agent_controls`); autonomy daily caps; finance keeps its own switches (`finance/execution.py`, `finance/live.py`) | A push kind with its own text for the stop (it uses the team-work kind) |
| Service health, DR exercises | existing/unverified | `infra/oracle/smoke.sh`, `backup.sh drill`, [Deploying on Oracle](../deploy-oracle.md#backups) | Record a drill on the VM |

## Source compared with the Oracle deployment

The live VM could not be inspected from this sandbox (no SSH, and production checks must not be run from here). This compares the code with the deployment definition the owner runs (`infra/oracle/compose.yaml`, `.env.example`, [Deploying on Oracle](../deploy-oracle.md)).

| Area | In the source | On the Oracle path | Effect on Business P1 |
|---|---|---|---|
| Runtime | Temporal worker or inline (`ANUM_RUNTIME_BACKEND`) | inline; a run stops if the API restarts and is resumed by hand | Lead research runs must be short and resumable; no long crawls |
| Events | NATS JetStream with outbox relay, or in-process | in-process (`ANUM_EVENT_BUS=memory`), events stored in PostgreSQL, SSE reads them | Fine for one owner; no dead-letter queue |
| Database | PostgreSQL with RLS, app role `anum_app` | same (PostgreSQL with pgvector, `anum_app`, migrations as `anum_migrator`) | New migrations run there unchanged |
| Identity | Keycloak | Keycloak 25 behind Caddy, admin console closed by default | none |
| Files | S3-compatible | S3-compatible bucket (Backblaze B2 placeholders) | Proposal PDFs and evidence snapshots go there |
| Web search | SearXNG | SearXNG container (`ANUM_SEARXNG_URL=http://searxng:8080`) | Lead research can use it at no API cost |
| Model | any provider through the gateway | `ANUM_MODEL_PROVIDER` from `.env`; optional Ollama profile | Drafting cost depends on the owner's provider; budgets apply |
| Observability | OpenTelemetry export | no collector; GlitchTip and Beszel optional | Use model budget usage and reports for cost tracking |
| Backups | `anum_backup.py`, drill | nightly logical dump, RPO 24 hours, no point-in-time recovery | Accept for internal sales data; not for customer production data |
| Feature switches | commerce, finance, email, marketing, WhatsApp each need provider keys | wired as environment variables in compose; which are set on the VM is unknown here | Assume none set; P1 must work with no paid add-on |

## Estimates per work package (estimates only)

For one experienced developer using ANUM's own tooling. Model costs assume pay-as-you-go prices of a mid-size model and are capped by ANUM's model budgets; they are not measured. Infrastructure reuses the Oracle VM, so the recurring cost of P1 is the model provider only.

| Work package | Hours | Infrastructure | Model API cost | Recurring cost |
|---|---|---|---|---|
| P0 close-out: VM smoke test, migration dry run, restore drill, branch protection (owner with Claude) | 4 to 8 | existing VM | none | none |
| Business schema and RLS: catalog, accounts, contacts, leads, evidence, activities, consents, opportunities, proposals and versions, projects, milestones (one expand-only migration, policies, PostgreSQL tests) | 32 to 48 | existing PostgreSQL; a few MB of data | none | none |
| Business API and permissions (`anum_api/business/`), audit and events | 40 to 60 | none new | none | none |
| Catalog with the three packages plus Tyre Pulse | 8 to 12 | none | none | none |
| CSV import, dedup, do-not-contact through `suppression.py` | 16 to 24 | none | none | none |
| Page fetch tool plus a research skill using `web_search` (evidence with URL, date and excerpt; unknown stays unknown) | 24 to 32 | none new | about 1 to 3 USD per 100 researched leads (summaries only) | as used, capped |
| Deterministic score (0 to 30) plus model-written explanation; drafts with a pre-send gate; sending off | 24 to 32 | none | about 1 to 4 USD per 100 drafts | as used, capped |
| Proposal versions and export (Markdown, print to PDF through reports) | 16 to 24 | files bucket | about 0.5 to 2 USD per 10 proposals | as used, capped |
| Project and milestone link to developer projects and work items | 12 to 20 | none | none | none |
| Sales KPI report and owner kill switch | 12 to 16 | none | none | none |
| Web Business section (catalog, leads, pipeline, proposal, project) with all states | 60 to 90 | none | none | none |
| Flutter Business screens (read and approve first) | 40 to 60 | none | none | none |
| Tests: unit, RLS, API, e2e journey with synthetic data ([P1 acceptance tests](P1_ACCEPTANCE_TESTS.md)) | 52 to 78 | CI minutes | none (mock model in CI) | none |
| **Total, P0 close-out plus P1** | **about 340 to 500** | **existing VM** | **under 10 USD for a month of internal use at these volumes** | **model provider only** |
