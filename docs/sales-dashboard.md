# Sales CRM dashboard in WhatsApp

The Sales CRM dashboard is available as **WhatsApp → Sales CRM** in the web client. It uses the existing authenticated `/api/v1/sales` endpoints. It does not introduce a second navigation system or replace existing Business Outreach.

## Working interactions

- Status summary for the latest 100 CRM records, explicitly marked as bounded data
- Lead list with text search, stage filtering, campaign hints, source and updated timestamp
- Lead editor for contact and company, requirements, assignment, budget and status transitions with optimistic revision
- Owner-review quotation drafts with description, supplied amount and terms
- Follow-up proposals with a due date, channel and purpose
- Saudi Arabic and English copy with RTL-safe responsive layout
- Permission/error/loading/empty-state feedback from the API, with refresh

## Safety and known boundaries

The API retains role and workspace checks; the browser is never responsible for enforcement. Quotes and follow-ups are saved as drafts/proposals, **not sent or scheduled**, so no unapproved WhatsApp outbound traffic is generated. Sales totals reflect up to 100 recent records, not the entire database. Campaign hints from customers are unverified.

The integrated Sales tab is accessible even if the WhatsApp phone has not yet been connected, so already-ingested records can be managed. Live Meta onboarding, verified campaign attribution, quote history, lead-specific consent ledger, scheduled worker execution and full Flutter screen remain separate product milestones. Existing Business Outreach and Marketing views are unchanged.

## Verification required

Run the web TypeScript check, lint, unit suite and Playwright on both Arabic and English locales, in desktop and narrow layouts. Test a seeded PostgreSQL workspace with the Sales CRM migrations and role restrictions, including deliberate cross-workspace attempts, quotation approval gates and Meta webhook idempotency. Do not claim a production launch before CI and deployment verification.
