# ANUM Sales Lead Lifecycle Domain

This change introduces a cohesive lead lifecycle domain spanning qualified inbound WhatsApp enquiries, human ownership, status progression, budget validation, approved quotation drafting, follow-up proposals, and workspace-scoped reporting.

## Domain interface

- `create_lead`: create an inbound lead from `SalesHandoff`, preserving campaign hint and explicit marketing-consent state.
- `edit_lead`: assigned salesperson, company/contact details and bounded commercial requirements, protected by optimistic revision control.
- `transition_lead`: explicit state machine, including qualification, quoted, negotiation, won and lost paths, with immutable audit event proposals.
- `draft_quote`: calculate only supplied line items (never model-invented prices), with owner approval required.
- `propose_follow_up`: validate future due dates and supported channels without sending any communication.
- `sales_overview`: per-workspace funnel counts and revenue from explicit won quote amounts, not estimated sales.

These are domain operations, not a live API or database-backed user interface. The caller must apply authenticated tenant context, store the resulting record and event in one transaction, enforce expected revisions and unique message IDs, and run existing tool permissions/approvals before any external action.

## Product integration still required

1. Persist leads, quote revisions, events, consent and follow-ups in PostgreSQL under forced RLS and unique message constraints.
2. Call the lead creation transaction from the verified WhatsApp webhook ingestion pipeline or a durable worker, and connect it to existing Business Outreach records.
3. Supply REST routes, role-based authorization and searchable Sales Pipeline interfaces for web and Flutter (English/Arabic RTL).
4. Execute scheduled follow-ups through the existing jobs/approvals infrastructure with explicit opt-in, approved templates where necessary, and an owner-controlled stop switch.
5. Join trusted Marketing campaign IDs to persisted sales results for conversion analytics; customer text markers remain unverified attribution.
6. Run unit/integration/E2E coverage, security checks and live Meta tests before production deployment.

The present changes do **not** enable autonomous customer messaging, collect payment, publish advertisements, or modify the user's Meta accounts.
