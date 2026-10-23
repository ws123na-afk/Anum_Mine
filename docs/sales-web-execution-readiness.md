# Sales web completion: conversion analytics and guarded operations

This release adds a full-workspace campaign-source count and a consolidated review queue for Sales CRM quotes and follow-up proposals. Unlike the original 100-row overview, the campaign aggregation queries all leads in the active tenant/workspace. The campaign markers remain customer-supplied and **must not** be presented as verified advertising conversions.

The execution policy module checks explicit owner approval, verified marketing consent, customer suppression, daily limits, the WhatsApp 24-hour service window and an owner pause switch. Defaults are **disabled** and **paused**. It does not transmit messages. The existing Business Outreach subsystem already has approved sending, suppression, stop-on-reply and follow-up capabilities, but this Sales CRM requires a deliberate mapping of CRM records to Business contacts and consent records before it can reuse those facilities.

## Production verification gates

- Run Alembic migration 0102 against staging and test rollback using backups.
- Run Python tests including `test_sales_execution_reporting.py`, sales CRM API routes and scoped SQL repositories.
- Run web TypeScript checks, lint, unit tests, and English/Arabic E2E UI.
- Confirm an actual owner approval through the existing approval dashboard, then test refusal on cancellation, opt-out, quota, expired WhatsApp window and unapproved template.
- Verify existing connected WhatsApp Business coexistence, phone ID routing and webhook signatures through an authorized account.
- Verify worker durability, retries and delivery acknowledgments before enabling outbound automation.
- Confirm all CI and CodeQL checks pass; deploy the exact merge SHA and perform a real production smoke test.

**Not yet complete:** actual CRM-to-Business Outreach mapping, quotation approval requests and delivery, durable scheduled follow-up execution, owner-editable automation configuration, and the live production deployment. These features remain disabled rather than allowing misleading or unapproved customer communications.
