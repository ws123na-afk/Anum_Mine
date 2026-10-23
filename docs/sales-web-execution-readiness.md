# ANUM Sales & Marketing: governed execution release

This release builds on the existing Marketing, WhatsApp and Sales CRM modules. All customer-facing actions use the workspace account and are auditable by the owner.

## What works in code

1. **Marketing conversion counts.** `GET /api/v1/sales/campaign-conversions` aggregates every lead in the active workspace by its customer-supplied campaign marker. The source is labelled *unverified*; no invented clicks, purchases or advertising revenue are reported.
2. **Quote requests.** In Sales CRM → lead → quotation history, choose **Request approved WhatsApp quotation delivery**. The server formats the exact saved prices and terms, reserves the quote against duplicate requests and invokes the existing WhatsApp send endpoint. Freeform replies **always require an owner approval** and a valid 24-hour customer-service window. The existing WhatsApp tool handles account policy, suppression, approval and Meta transport. Quotation delivery state is read from the related WhatsApp message record; provider-accepted and confirmed-delivered states are distinct.
3. **Consent evidence.** Workspace owners can record a customer's explicit opt-in only with a saved inbound WhatsApp message ID from the same conversation and an explanation. Revocations can be recorded without such an ID when consent was withdrawn by another channel. Every change is scoped, revision-guarded and audited.
4. **Follow-up approval.** An owner selects an actual Meta-approved template and fills its parameters. The template must also be on the WhatsApp Setup allowlist. The server hashes tenant, workspace, conversation, recipient, template, parameters and due date and stores the actor and approval time.
5. **Autonomous execution.** A scheduler pass discovers due approved follow-ups through the restricted maintenance role. It checks workspace enable/pause switch, owner approval hash, current consent, tenant suppression, emergency stop, organization governance, Meta approval, template allowlist, customer reply after approval, and a per-workspace daily cap. A row is claimed before network transmission; interrupted/ambiguous transmissions become `unknown` and are **never automatically retried**, preventing duplicate messaging. Meta-accepted messages record their wamid and appear in the WhatsApp inbox.
6. **Owner controls and dashboards.** The bilingual web Sales CRM displays conversion counts, quotation approval state, follow-up queue, failures, schedule, consent evidence, and automation enable/pause/daily cap. The safe default is disabled and paused with zero sends per day.

## Activation prerequisites (an owner must provide externally)

- PostgreSQL migration `0103_sales_execution` applied to staging and production after backup.
- WhatsApp Business Cloud API number actually connected and verified, with the phone's coexistence mode preserved where Meta supports it.
- A Meta-approved message template added to the owner allowlist in WhatsApp Setup.
- Recorded customer opt-in and explicit owner follow-up approval.
- Scheduler enabled for PostgreSQL; no sends execute when its scheduler is disabled.
- Owner enables sales automation, sets a finite daily cap, and removes the pause switch.
- Organizations with tool-policy requirements beyond this exact owner approval remain **held** for the standard approvals workflow.

## Failure modes

- Message received/lead conversion does **not** imply marketing consent.
- A customer reply after scheduled approval holds the follow-up for review (avoid unwanted messages).
- Opt-out or withdrawal blocks sending, even after prior approval.
- Meta template rejection/approval changes hold the follow-up.
- An ambiguous network response remains `unknown` for manual reconciliation. Never blindly resend.
- Quote delivery is limited by WhatsApp's freeform 24-hour window; outside it, send only an approved template through the dedicated WhatsApp workflow.
- The owner may pause automation at any time. A transmission already accepted by Meta cannot be retracted.

## Required full production verification

Before advertising this as a production-complete module, verify **all** of the following:

- Backend unit and PostgreSQL integration tests, including migrations, forced RLS, cross-workspace isolation and idempotent reservation.
- CI API tests, web TypeScript/lint/build, English and Arabic responsive E2E and CodeQL.
- Against a Meta test WABA: owner approval → correct template → simulated opt-out, customer reply, quota reached, revoked consent, expired WhatsApp window, provider error, and successful webhook-delivered acknowledgements.
- Scheduler chaos testing across two replica workers and a forced process crash during the `dispatching` state.
- Deployment of the exact merge SHA to the ANUM OS VM, schema health, secrets/access control, integration credentials and end-to-end customer test.

**A GitHub merge alone is not proof that your VM was deployed or that a live customer message was delivered.** The production environment requires a connected Meta account and operator verification.
