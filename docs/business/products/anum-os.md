# ANUM OS: sales kit

Slug `anum-os`. Catalog entry: [`scripts/business/products.json`](../../../scripts/business/products.json), loaded by `scripts/business/seed-products.py` ([Product sales kits](index.md#loading-the-catalog)).

Sources: [README](../../../README.md), [product vision](../../product-vision.md), [business and product master roadmap](../../ANUM_OS_Business_and_Product_Master_Roadmap.md) (sections 0, 2.1, 2.3), [production plan](../../production-plan.md) status table, and the feature pages linked below. Anything not in those sources is `-`.

**Selling rule from the roadmap (section 2.1):** keep ANUM OS as the owner's internal operator until it is safe to sell externally. The first external offer is an assisted team pilot, not a self-serve subscription.

## What it does (verified in this repository)

ANUM OS is a multi-tenant AI work operating system: people delegate work to agents while authority, memory, data, integrations and audit stay under their control ([product vision](../../product-vision.md)).

| Area | What is built | Page |
|---|---|---|
| Agents and approvals | Agent runtime with tool mediation, risk levels and approval gates bound to the exact payload; workspace agents and department assistants with their own instructions, tools and autonomy | [Agent runtime](../../agent-runtime.md), [Agents](../../agents.md), [Approvals and risk](../../approvals-and-risk.md) |
| Tasks, memory, knowledge, files | Tasks and agent runs, inspectable memory, knowledge base, workspace files | [Tasks](../../tasks.md), [Memory](../../memory.md), [Knowledge base](../../knowledge-base.md), [Files](../../files.md) |
| Automation and reports | Event, schedule and webhook automations; reports with export | [Automation](../../automation.md), [Reports](../../reports.md) |
| Sales desk | Catalog with priced versions, leads with a six-criterion score, research, drafts (sending off), pipeline, proposals, projects | [Business](../../business.md) |
| Communication | Unified inbox (Gmail, Outlook), own mail server, WhatsApp Business inbox, Brevo campaigns with approval-only sending, phone calls (Twilio, off until connected), in-app calls, calls and meetings summaries | [Email](../../email.md), [WhatsApp](../../whatsapp.md), [Brevo](../../brevo.md), [Phone calls](../../calls.md), [In-app calls](../../in-app-calls.md), [Meetings](../../meetings.md) |
| Commerce and marketing | Amazon, noon and the owner's own store synced into one place, price and stock changes only after approval; social media marketing | [Commerce](../../commerce.md), [Marketing](../../marketing.md) |
| Surfaces | Web (React), Flutter Android and iOS app, Tauri desktop, English and Arabic | [Flutter mobile](../../mobile.md), [Desktop](../../desktop.md), [Web localization](../../web-localization.md) |
| Security and tenancy | Keycloak OIDC sign-in with two-factor, PostgreSQL row-level security per tenant, audit log, privacy export and delete, per-tenant model budgets | [Identity](../../identity.md), [Multi-tenancy](../../multi-tenancy.md), [Security](../../security.md), [Privacy](../../privacy.md) |
| Operations | One-VM Oracle deployment with nightly backups and a restore drill; Helm chart; Render blueprint | [Deploy on Oracle](../../deploy-oracle.md), [Deployment](../../deployment.md) |

Not claimed: production reliability at customer scale, durable Temporal workflows on the Oracle VM (it runs inline workflows, roadmap section 1), public SaaS billing (roadmap 2.3: only after proof).

## Ideal customer profile

| Field | Value |
|---|---|
| Industry | Professional services; retail and e-commerce operators already selling on Amazon or noon; operations teams with many repeated tasks (roadmap 2.1 "Growing teams") |
| Size | 5 to 50 staff (recommendation, owner to confirm) |
| Region | GCC first: UAE and Saudi Arabia, then the rest of the GCC. Arabic and English UI. |
| Buyer roles | Owner or founder, operations head, IT lead |
| Trigger events | Team starts using AI tools with no approvals, audit or shared memory; customer messages spread over WhatsApp and email; a new department or branch; Amazon or noon sales tracked by hand |
| Disqualifiers | Wants unattended autonomous actions (payments, sending, deletions) without approvals; needs on-premises hosting, data residency or an SLA the owner cannot staff yet; wants a fixed price for unlimited AI usage (roadmap 2.3: avoid unlimited AI plans) |

## Value proposition

One AI workspace for the whole team where agents do the repeated work and a person approves anything that matters, with every action audited and every tenant's data isolated.

| Pain | Outcome |
|---|---|
| Staff paste company data into separate AI tools with no record of what was sent or done | Agents work inside the company's own workspace; every tool call is mediated, risky ones wait for approval, and the audit log shows who did what |
| Customer questions arrive on WhatsApp, email and store channels and get missed | One inbox and commerce view; assistants classify and draft replies, a person approves before anything is sent |
| Knowledge lives in people's heads and old chats | A workspace knowledge base and inspectable memory the assistants answer from, which the owner can review and delete |

## Packaging and pricing

| Package | Contents | Price |
|---|---|---|
| ANUM OS assisted pilot | One isolated organization and team workspace, approvals and audit on, up to two department assistants, one automation or report, onboarding and handover | `-` |
| Solo, Team, Business, Enterprise plans (roadmap 2.3) | As described in the roadmap | `-` |

**Recommendation (owner to confirm):** quote the pilot with the roadmap's pricing formula (labor hours x internal hourly cost + verified external costs + contingency + profit). Bill model, messaging, storage and telephony usage separately with caps. Set subscription prices only after 5 to 10 qualified buyer interviews and 2 to 3 paid pilots (roadmap 2.3). The seed loads the pilot as a draft `quote` offer.

## Short pitch

ANUM OS is an AI workspace for your team that does the repeated work and asks before it acts. Assistants read your inbox, WhatsApp and store orders, draft the replies and reports, and wait for a person to approve anything that leaves the company. Every action is logged and your data stays in your own isolated workspace, in Arabic or English, on web, phone and desktop.

## Email opener template

> Subject: {{company}}: AI help that asks before it acts
>
> Hello {{first_name}}, I saw that {{company}} {{verified_observation, for example "answers customers on WhatsApp and sells on noon"}}. Many teams in {{city}} now use AI tools with no record of what was sent or decided. I build ANUM OS, a workspace where AI assistants draft replies, reports and follow-ups from your own data, and a person approves anything before it goes out. Would a 20-minute call next week to see whether a small pilot on {{one_workflow}} makes sense be useful? If not, reply "no" and I will not write again.

Fill `{{verified_observation}}` only from a recorded evidence source on the lead ([Business: evidence](../../business.md#evidence-and-inferences)). Drafts are approved and sent by a person; ANUM does not send outreach ([Business: drafts](../../business.md#drafts-sending-is-off)).

## Objection handling

| Objection | Answer |
|---|---|
| "We already use ChatGPT." | Keep it for personal use. ANUM adds what a company needs around it: approvals before actions, an audit log, shared knowledge and tenant isolation. The pilot tests one workflow side by side. |
| "Is our data safe?" | Each tenant's data is isolated by PostgreSQL row-level security; sign-in is Keycloak with two-factor; the audit log records actions; people can export and delete their data ([Security](../../security.md)). Name the hosting location honestly: today one Oracle Cloud VM in Riyadh. |
| "What if the AI sends something wrong?" | Sending, payments, price changes and deletions need a person's approval of the exact content; a changed payload is refused ([Approvals and risk](../../approvals-and-risk.md)). |
| "How much does it cost?" | A fixed-scope pilot quoted on your workflow; AI and messaging usage at cost with a cap you set. No unlimited plan. |
| "Do you have customers?" | Answer truthfully: not yet as an external product. The owner runs their own businesses on it. Offer the pilot with written acceptance criteria. |

## Proof points (verified only)

- The owner's own businesses run on it: the setup plan creates the Fleet Consulting, Tyre Pulse, ANUM 360 Gallery, OpsBrain and VoiceQuote workspaces with their agents and automations ([Setup plan](../../setup-plan.md)).
- Running on a staging VM at `app.anum360os.com` ([Deploy on Oracle](../../deploy-oracle.md)).
- CI gates every change: 17 checks including API unit, PostgreSQL row-level security, browser end-to-end, Flutter and image scans ([Production plan](../../production-plan.md)).
- External customers, testimonials, uptime figures: `-` (none recorded).

## Owner questions

1. Confirm the pilot price, or the hourly cost and hours to compute it.
2. Confirm the ideal company size and the first industry to approach.
3. May the owner's own businesses be named as references in outreach?
4. Confirm maturity `pilot` for the catalog.
