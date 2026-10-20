---
id: billing-console
title: Billing console
summary: Sell plans to customer organisations, record their payments and follow revenue, for platform operators.
keywords: billing, console, plans, subscriptions, invoices, payments, credit note, MRR, revenue, platform operator, Stripe
docs: ../billing.md
---

# Billing console

The billing console is where the platform operator bills customer organisations. It shows plans, subscriptions, invoices and payments; it never shows an organisation's own work.

## Set up your plans and bank details

1. Open **Billing console** in the sidebar under **Admin**, then **Settings**.
2. Fill your company name, address, VAT number and bank details. They print on every invoice. Set the VAT rates per country, the payment terms and the reminder days.
3. Open **Plans**, create a plan with its price, interval, trial days and limits, and tick **Published** to show it on the pricing page.

## Start billing and record a payment

1. In **Subscriptions**, click **Start billing**, choose an organisation you onboarded and a plan, then **Start subscription**.
2. Open the organisation, then an invoice. Click **Record payment**, enter the amount, method and transfer reference, attach the proof if you have one, and save. A payment that covers the amount due marks the invoice paid.
3. To correct an invoice, click **Credit note**, or **Void** an open invoice that has no payment yet.

## Send an invoice and follow revenue

1. On an invoice, click **Email invoice**. The email waits in **Approvals** and is sent only when someone approves it.
2. The band at the top shows monthly recurring revenue, open and overdue amounts and what was collected this month.
3. Click **Run billing now** to issue due invoices and reminders at once instead of waiting for the hourly run.

## Who can do this

- Platform operators only (realm role platform_operator), in their own organisation. The entry appears in the sidebar only for them.
- Organisation owners and administrators see their own plan and invoices in **Settings**, **Billing**.

## Learn more

[Customer billing](../billing.md)
