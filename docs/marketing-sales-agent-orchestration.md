# Marketing → WhatsApp → Sales agent orchestration

ANUM already has Marketing, WhatsApp, the agent tool registry, approvals and business records. The new `marketing_sales_orchestration.py` is a conservative, pure-function handoff planner for composing these modules without creating a second agent platform.

## Implemented in this change

- Tenant/workspace/message-scoped idempotency keys for inbound lead candidates
- Saudi Arabic and English qualification via existing `whatsapp_sales`
- Self-reported marketing campaign marker propagation
- Explicit distinction between inbound WhatsApp contact and verified promotional consent
- Agent role routing to WhatsApp or a sales human for urgent/sensitive conversations
- Approval-aware suggestions for quote drafting and follow-ups, with no side effects

## Engineering boundary

These are *plans*, not executed agent workflows. Callers must retrieve real authenticated workspace context, persist lead and campaign relations, dedupe message IDs in the database, run through the existing authorization and ToolPolicy approval path, and log outcomes. Treat all customer content and attribution markers as untrusted.

## Remaining before a production claim

1. Wire WhatsApp's verified inbound event handler to an idempotent, durable per-workspace sales lead record.
2. Link the Marketing campaign table to lead source by a validated internal campaign ID; record click and conversion statistics without trusting customer-submitted attribution alone.
3. Add owner-controlled autonomous **low-risk** replies with opt-in, 24h window enforcement, escalation, model budget/caps, stop switch and delivery reporting. Keep high-risk and pricing actions approval-gated.
4. Add full lead CRM editing/assignment, quotations, follow-up queues, multilingual RTL UI and analytics.
5. Test API, worker, database migrations, web/Flutter paths, live Meta coexistence onboarding, and CI security requirements.
6. Connect production WhatsApp and Marketing accounts through their actual provider consent flows. No credentials belong in Git.

The branch contains no Meta credentials and neither sends outbound messages nor bills for ads.
