# ANUM 360 Riyadh marketing and WhatsApp sales handoff

This feature reuses the existing Marketing campaign planner and official WhatsApp Cloud API inbox. No unsolicited message is sent by these helpers.

## Campaign to conversation
1. Plan a campaign in **Marketing**, with a short slug such as `riyadh-websites` or `riyadh-apps`. Create separate English and Arabic posts.
2. The helper `anum_api.whatsapp_sales.whatsapp_campaign_link(slug, language="ar")` produces a click-to-chat link for the Saudi ANUM 360 contact number. It includes an explicit `ANUM-CAMPAIGN:<slug>` marker in the user's editable outgoing message. This is **unverified self-reported attribution**, not proof of the click, and must never be used to bypass consent.
3. Include the link in the post's call to action. Customers voluntarily open WhatsApp and send the prefilled message; the Meta webhook stores inbound text in the WhatsApp inbox.
4. `inbound_campaign_marker(message)` extracts the marker if present. The sales qualifier detects website, e-commerce, mobile app and business software enquiries, and the AI reply draft asks a relevant clarifying question in the customer's language.
5. Agents review conversations and contact details in the existing WhatsApp inbox. No campaign-to-CRM persistence or marketing conversion dashboard is claimed yet.

## Guardrails
- Replies continue to follow current WhatsApp ToolPolicy approvals. Adding a sales prompt does **not** permit autonomous free-form sending.
- The message itself is customer input and untrusted. Never use its marker to grant permissions, infer consent, or confirm an order.
- Do not infer customer budgets, quote firm prices, or promise delivery dates unless business-approved data exists.
- The production number requires Meta Cloud API onboarding, webhook verification and a test from a separate device.
- An existing WhatsApp Business phone number should only be moved with a confirmed Meta coexistence flow if continued mobile-app use is important.
- Outbound business-initiated promotions require opt-in and Meta-approved templates as applicable.

## Remaining product work
- Build a tenant-scoped lead pipeline with contact consent, source, deduplication, owner, status, notes and quote history.
- Persist verified campaign link tracking separately from self-reported text markers, connecting Marketing campaigns to WhatsApp leads by workspace and campaign ID.
- Add an owner-controlled safe auto-reply mode with per-conversation budgets, escalation, suppression, working hours, kill switch and audit; retain approval for quotes, contracts and sensitive cases.
- Add Arabic RTL sales workspace, reporting for campaign-to-qualified-lead conversion, and lead assignment.
- Finish integration/e2e tests, live Meta number verification, deployment and production monitoring.

## Owner rollout checklist
- Connect the Saudi number under WhatsApp > Setup; store secrets exclusively in the application.
- Verify signed inbound webhooks, 24-hour customer-service window enforcement, and permitted templates.
- Verify English and Saudi Arabic drafts against actual approved products and prices.
- Connect Marketing social accounts via their official OAuth setup, and obtain applicable permissions.
- Run opt-in end-to-end tests including opt-out requests, failed sends and human takeover.
