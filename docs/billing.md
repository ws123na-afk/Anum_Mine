# Customer Billing

ANUM bills its own customers: each customer company is an organisation (tenant, [Customer organisations](customer-organisations.md)), and the platform owner's own organisation is the **issuer** that sells plans, issues invoices and collects payments. Roadmap: P4 (Commercial ANUM SaaS beta) in [the master roadmap](ANUM_OS_Business_and_Product_Master_Roadmap.md).

Implemented in `services/api/anum_api/billing/` (models, store, service, enforcement, jobs, provider adapters, routes), migration `0085_customer_billing`, web `apps/web/src/billing/` (Billing settings page, platform console, public pricing page, sign-up and onboarding checklist), Flutter `apps/mobile/lib/features/billing/`.

Money is always an integer number of **minor units** (`price_minor`, `total_minor`: halalas for SAR, cents for USD) plus an ISO 4217 `currency`. SAR is the default currency. Times are ISO 8601 UTC.

## Who can do what

| Who | Comes from | May |
|---|---|---|
| Visitor | nobody signed in | Read the published plans (`GET /api/v1/public/billing/plans`). |
| Signed-up account without an organisation | a verified Keycloak account (no `tenant_id` yet) | Create one organisation with a trial (`POST /api/v1/signup/organization`). |
| Organisation billing admin | workspace **owner** in the organisation, or `org_admin` with an active membership (`billing:read`, `billing:manage`) | See the plan, usage, invoices and payments; change or cancel the plan; edit the billing contact and address; download invoice PDFs; pay by card when the issuer turned Stripe on. Members and viewers get `403` naming who can. |
| Platform owner | realm role `platform_operator` (`platform:operate`), in their home organisation, which is the issuer | Define plans, set the issuer's bank details, tax rules, dunning schedule and Stripe keys; see every subscription of their customers with the MRR summary; start, change, pause, resume and cancel subscriptions; record payments with a proof file; issue credit notes; void invoices; ask to email an invoice (approval required). |

The platform owner acts on a customer's billing only when the customer's billing account names the owner's organisation as its issuer, or when the owner onboarded that organisation (`platform_organizations.operator_user_id`). Every other organisation answers `404`.

## Data model (migration `0085_customer_billing`)

Every table has `tenant_id` and **forced RLS** with the policy `tenant_isolation_<table>` on `anum.tenant_id` (billing is organisation-wide, so no workspace predicate). Rows of the issuer (plans, settings, number sequences, webhook events) live in the issuer's tenant; rows about a customer live in the customer's tenant and carry `issuer_tenant_id`.

| Table | Tenant | What |
|---|---|---|
| `billing_plans` | issuer | `code`, `name`, `description`, `price_minor`, `currency` (default `SAR`), `interval` (`month`, `year`), `trial_days`, `per_seat`, `limits` (`seats`, `workspaces`, `agent_runs_per_month`, `storage_gb`, `messages_per_month`; `null` is unlimited), `features` (list of short texts), `published` (on the pricing page), `active` (archived plans stay on existing subscriptions), `sort_order`. |
| `billing_settings` | issuer | Issuer name, address, VAT number, bank details shown on invoices (bank name, account name, IBAN, SWIFT), invoice and credit note prefixes, payment terms, `tax_rules` (country to rate in basis points, default `{"SA": 1500}`), default tax rate, dunning schedule (`reminder_days`, `grace_days`), the workspace whose email integration sends invoices, and Stripe: `stripe_enabled` (off by default) with the secret key and webhook secret as Fernet ciphertexts. |
| `billing_accounts` | customer | One per organisation: issuer, billing contact (name, email), company name, address, country, VAT number, `credit_balance_minor`, `state` (`active` or `read_only`), onboarding checklist dismissal. |
| `billing_subscriptions` | customer | Plan id plus a snapshot of the plan (name, price, currency, interval, limits, features), `seats`, `status` (`trialing`, `active`, `past_due`, `paused`, `cancelled`), trial end, current period, `cancel_at_period_end`, `pending_plan_id` (a downgrade at period end), `past_due_since`, `grace_until`. A partial unique index allows one subscription that is not cancelled per organisation. |
| `billing_seats` | customer | Seat quantity changes: from, to, when, prorated amount, who. |
| `billing_usage` | customer | Meter readings per month (`agent_runs`, `storage_bytes`, `messages`, `seats`, `workspaces`), refreshed from ANUM's own counters. |
| `billing_invoices` | customer | `number` (set when issued), `status` (`draft`, `open`, `paid`, `void`, `uncollectible`), `kind` (`subscription`, `proration`, `manual`), period, issue and due dates, subtotal, tax rate and country, tax, total, paid, credited, due, bill-to and issuer snapshots, reminders sent, the last email request. Unique `(issuer_tenant_id, number)`. |
| `billing_invoice_lines` | customer | Description, quantity, unit and line amounts, `kind` (`plan`, `proration`, `credit`, `adjustment`), period. |
| `billing_credit_notes` | customer | Number, invoice, reason, subtotal, tax, total, who issued it. |
| `billing_payments` | customer | Invoice, amount, method (`bank_transfer`, `cash`, `card`, `stripe`, `other`), reference, received date, proof file (PDF, PNG or JPEG, at most 5 MB, checked by magic bytes), provider payment id (unique per issuer and provider), who recorded it. |
| `billing_number_sequences` | issuer | The next invoice and credit note number. |
| `billing_webhook_events` | issuer | Provider event ids already processed (unique per issuer and provider). |
| `billing_signups` | the new organisation | Who signed up, when, from which hashed IP, which plan. |

### Gapless numbering

Invoice numbers (`INV-000001`) and credit note numbers (`CN-000001`) are sequential **per issuer** with no gaps. A number is taken only when an invoice is issued, in the same transaction that writes the invoice: the sequence row (issuer tenant) is incremented with an upsert that holds its row lock until commit, then the invoice is written in the customer's tenant. A rollback returns the number. Issued invoices are never deleted: mistakes are corrected with a credit note or by voiding an unpaid invoice, which keeps its number.

### Cross-tenant reads without bypassing RLS

- **Platform console** (all subscriptions, MRR, invoices due): read through `anum_platform_reader` (NOLOGIN, granted `WITH INHERIT FALSE, SET TRUE`) in a separate rolled-back transaction that sets `anum.billing_issuer` to the caller's own home tenant after the `platform:operate` check. Column grants on amounts, statuses and dates of `billing_accounts`, `billing_subscriptions` and `billing_invoices` only; its policies admit rows whose `issuer_tenant_id` is `anum.billing_issuer`.
- **Plan catalog** (the public pricing page and a customer's upgrade options): the same role reads `billing_plans` rows that are `published` and `active` of the issuer named by `anum.billing_issuer`, public columns only. The public endpoint names the issuer from `ANUM_BILLING_ISSUER_TENANT_ID`; a customer's upgrade list uses their own account's issuer.
- **Usage meters** (seats, workspaces, agent runs, storage, messages across all of an organisation's workspaces): read as `anum_maintenance` with `anum.billing_tenant` set to the caller's own tenant; column grants on ids, `active`, sizes and timestamps only.
- **Recurring jobs**: discovered as `anum_maintenance` (ids, statuses and dates of subscriptions and invoices that need work), then each organisation is handled as the application role inside its own tenant context.

## Plans and limits

Plan limits are enforced when something new would be created, never by removing anything:

| Limit | Checked when |
|---|---|
| `seats` (or the subscription's `seats` on a per-seat plan) | Inviting someone or accepting an invitation: active members plus pending invitations across the organisation. |
| `workspaces` | Creating a workspace. |
| `agent_runs_per_month` | Creating a task (an agent run), counted per UTC month across the organisation. |
| `storage_gb` | Uploading a file. |

A refusal is `402` with code `plan_limit_reached` and a sentence naming the limit, the current use and what to do ("Your plan allows 5 seats and 5 are in use. Upgrade in Settings > Billing, or remove a pending invitation."). An organisation without a subscription has no limits.

## Lifecycle

- **Trial**: a new subscription starts `trialing` for the plan's `trial_days` (14 by default for sign-ups). Changing plan during a trial costs nothing.
- **Upgrade** (a dearer plan, or more seats): immediate. The unused part of the period on the old price is credited and the new price is charged for the rest of the period: `(new - old) x remaining seconds / period seconds`, rounded half up to a minor unit. A proration invoice is issued at once.
- **Downgrade**: at period end by default (`pending_plan_id`), or `when: "now"` with the prorated difference added to the account's credit balance, which the next invoice uses up. A downgrade the current usage does not fit is refused with `409` listing every limit that is over (`blocked_by`), so nothing is ever deleted to fit a plan.
- **Cancel**: `cancel_at_period_end`; the subscription ends at the period end and no further invoice is issued. **Reactivate** clears it, or starts a new period (and invoice) for a cancelled one.
- **Pause / resume** (platform owner): a paused subscription is not invoiced; limits still apply.
- **Renewal**: the billing job issues the next period's invoice when a period ends (applying a pending downgrade first).
- **Trial expiry**: when the trial ends the first invoice is issued, due at once; unpaid, the subscription becomes `past_due` and dunning starts.
- **Dunning**: an invoice past its due date makes the subscription `past_due` (`past_due_since`). Reminders are prepared on the days in `reminder_days` after the due date (default 1, 7 and 14); each is an email that waits for approval (below). After the last reminder plus `grace_days` (default 7) the grace period ends and the organisation becomes **read-only**: every write is refused with `402` `billing_read_only` except billing, sign-in and approvals, and all data stays readable and exportable. Recording a payment that settles the overdue invoices makes the subscription `active` and lifts read-only at once.

## Tax

The tax rate comes from the billing account's country in `tax_rules` (basis points; `SA` 15% by default) or the default rate. Tax is computed on the invoice subtotal after credits, rounded half up, and shown with the rate and country on the invoice. Credit notes split their total into subtotal and tax at the invoice's rate.

## Payment collection

The default is **manual / bank transfer** (no paid tool is needed): the invoice and the Billing page show the issuer's bank details and the invoice number as the transfer reference. The platform owner records each payment with its reference and an optional proof file; a payment that covers the amount due marks the invoice `paid`.

**Stripe** is an optional adapter behind `PaymentProvider` (`billing/providers.py`), **off by default** and with no key in the repository. The platform owner turns it on in the console and enters the secret key and webhook signing secret, which are stored only as Fernet ciphertexts (`ANUM_SECRETS_KEY`, rotated by `python -m anum_api.rotate_secrets`) and never returned. With it on, an organisation admin can pay an open invoice by card (`POST /api/v1/billing/invoices/{id}/checkout` returns a Stripe Checkout URL). Stripe calls `POST /api/v1/billing/webhooks/stripe/{issuer_tenant_id}`:

- the `Stripe-Signature` header is verified (HMAC-SHA256 over `timestamp.body` with the webhook secret, 5 minute tolerance, constant-time compare); anything else is `400` and changes nothing;
- each event id is stored once in `billing_webhook_events`; a repeated delivery answers `200` and does nothing (idempotent);
- `checkout.session.completed` records a `stripe` payment for the invoice named in the session metadata, after checking that the invoice's organisation is billed by this issuer; the payment's provider id is unique, so a payment is never recorded twice.

Test mode keys (`sk_test_...`) work the same way; the console shows whether the key is a test key.

## Documents

- **Invoice PDF**: `GET .../invoices/{id}/pdf`, written by ANUM's dependency-free PDF writer (`tyre/pdf.py`): issuer and bill-to blocks, number, dates, lines, subtotal, tax with rate, total, paid, due, and the bank details. Built-in PDF fonts cover Latin text; Arabic names print as `?` (the web and app pages show them in full).
- **Invoice email**: `POST /api/v1/platform/billing/organizations/{tenant_id}/invoices/{id}/email {to?}` plans a send through the issuer workspace's email integration with the PDF attached ([delivery](tools-and-integrations.md#delivery)): it waits in **Approvals** bound to the payload hash, and nothing is sent until someone approves it. Dunning reminders take the same path. Without an email integration the request answers `409` saying what to connect; the reminder is recorded as not sent.

## API

All routes are under `/api/v1`. Errors use the standard envelope (`{"error": {"code", "message", ...}}`).

### Public and sign-up

| Route | Who | What |
|---|---|---|
| `GET /public/billing/plans` | anyone | `{signup_enabled, currency, tax: {country, rate_bp}, plans: [PublicPlan]}`. Only published, active plans of the configured issuer; no tenant id or any other organisation data. `PublicPlan`: `{id, code, name, description, price_minor, currency, interval, trial_days, per_seat, limits, features}`. |
| `GET /signup/status` | a signed-in account, with or without organisation | `{email, email_verified, state}`: `verify_email`, `ready` (may create an organisation), `provisioned` (with `tenant_id`, `workspace_id`). |
| `POST /signup/organization {organization_name, plan_id, country?, accept_terms}` | a signed-in account without organisation, verified email | Creates the organisation, its default workspace `<tenant_id>_main`, the caller's owner membership, the billing account and a trial subscription, all inside the new tenant's RLS context. `201 {tenant_id, workspace_id, trial_end, identity_linked}`. Refusals: `403 email_not_verified`, `409` (this account already created an organisation), `422` (disposable email domain, bad name, unknown plan), `429` (more than 3 sign-ups per IP per hour, or the daily cap `ANUM_SIGNUP_DAILY_LIMIT`). |

### Organisation billing (owners and org admins)

| Route | What |
|---|---|
| `GET /billing/overview` | `{account, subscription, usage: {period, meters: [{key, used, limit, unit}]}, state, open_balance_minor, currency, payment: {bank_transfer: {...}, card_enabled}, issuer: {name, ...}}`. `subscription` is `null` when the organisation is not billed. |
| `GET /billing/plans` | Plans the organisation may move to (`PublicPlan`), with `current: true` on its own. |
| `GET /billing/subscription/preview?plan_id=&seats=` | `{effective: "now" or "period_end", amount_minor, tax_minor, total_minor, credit_minor, blocked_by: [{key, used, limit}]}`. |
| `POST /billing/subscription/change {plan_id, seats?, when?}` | Upgrade or downgrade; `{subscription, invoice}`. |
| `POST /billing/subscription/cancel` | Cancel at period end. |
| `POST /billing/subscription/reactivate` | Undo a cancellation or restart a cancelled subscription. |
| `PUT /billing/account {contact_name, contact_email, company_name, address_line1, address_line2, city, postal_code, country, vat_number}` | The billing contact and address. |
| `GET /billing/invoices?q=&status=&limit=&offset=` | `{items: [InvoiceSummary], total}`; `q` matches the number. |
| `GET /billing/invoices/{id}` | `Invoice`: summary plus `lines`, `payments` (without proof bytes), `credit_notes`, `bank_details`. |
| `GET /billing/invoices/{id}/pdf` | The PDF (`Content-Disposition: attachment`). |
| `POST /billing/invoices/{id}/checkout` | A Stripe Checkout URL when Stripe is on (`409` otherwise). |
| `GET /billing/onboarding` | The first-run checklist: `{items: [{key, done}], complete, dismissed}`; keys `invite_team`, `connect_email`, `choose_plan`, `add_payment_details`. |
| `POST /billing/onboarding/dismiss` | Hide the checklist. |

### Platform console (`platform_operator`)

| Route | What |
|---|---|
| `GET`, `PUT /platform/billing/settings` | Issuer details, bank details, tax rules, dunning schedule, sending workspace, `stripe_enabled`; `stripe_secret_key` and `stripe_webhook_secret` are write-only (`has_stripe_secret_key`, `has_stripe_webhook_secret`, `stripe_test_mode` come back). |
| `GET`, `POST /platform/billing/plans`, `PATCH /platform/billing/plans/{id}` | The plan catalog (archive with `active: false`). |
| `GET /platform/billing/summary` | `{currency, mrr_minor, arr_minor, counts: {trialing, active, past_due, paused, cancelled}, open_invoices, overdue_minor, collected_this_month_minor}`. |
| `GET /platform/billing/subscriptions?q=&status=&limit=&offset=` | `{items: [{tenant_id, display_name, plan_name, status, price_minor, currency, interval, mrr_minor, current_period_end, open_balance_minor, state}], total}`. |
| `POST /platform/billing/organizations/{tenant_id}/subscription {plan_id, trial_days?, seats?}` | Start billing an organisation the caller onboarded. |
| `POST /platform/billing/organizations/{tenant_id}/subscription/{action}` | `change {plan_id, seats?, when?}`, `pause`, `resume`, `cancel`, `reactivate`. |
| `GET /platform/billing/organizations/{tenant_id}/invoices`, `.../invoices/{id}`, `.../invoices/{id}/pdf` | As the organisation sees them. |
| `POST .../invoices/{id}/payments {amount_minor, method, reference, received_at?, proof?: {name, content_type, data_base64}}` | Record a payment. |
| `GET .../invoices/{id}/payments/{payment_id}/proof` | The proof file. |
| `POST .../invoices/{id}/credit-notes {total_minor, reason}` | Issue a credit note (at most the amount not yet credited). |
| `POST .../invoices/{id}/void` | Void an open invoice without payments. |
| `POST .../invoices/{id}/email {to?}` | Ask to email the invoice; `{status, approval_id, payload_hash, message}`. |
| `POST /platform/billing/run` | Run invoice generation and dunning now for the caller's customers. |

`Invoice` and `InvoiceSummary` fields: `id, number, status, kind, currency, period_start, period_end, issued_at, due_at, subtotal_minor, tax_rate_bp, tax_country, tax_minor, total_minor, amount_paid_minor, amount_credited_minor, amount_due_minor, overdue`.

## Self-serve sign-up

1. The public pricing page (`#pricing`, no sign-in) lists the published plans.
2. **Start free trial** opens the sign-up page (`#signup?plan=<id>`), which sends the visitor to Keycloak's registration page (`prompt=create`) with **Verify email** on. ANUM never stores a password.
3. Back in ANUM with a verified account that has no organisation, the page asks for the organisation name and country and calls `POST /signup/organization`.
4. The API provisions the organisation in the new tenant's RLS context (the same path as the platform console's onboarding, no `BYPASSRLS`) and, when the Keycloak provisioning client is configured, sets the account's `tenant_id` and `default_workspace_id` attributes through Keycloak's admin API. The client refreshes its token and lands in the new workspace.
5. The first-run **onboarding checklist** on Home (invite team, connect email, choose plan, add payment details) links to Members, Email, Billing and the billing contact form; the Setup plans page offers ready-made setups.

Abuse limits, without a captcha: a verified email is required, one organisation per account, disposable email domains refused, at most 3 sign-ups per IP address per hour (the IP is stored only as a salted hash) and `ANUM_SIGNUP_DAILY_LIMIT` per day overall (default 50).

Owner settings (see [Deploy on Oracle](deploy-oracle.md#self-serve-sign-up) and [Deploy on Render](deploy-render.md)): `ANUM_BILLING_ISSUER_TENANT_ID`, `ANUM_SIGNUP_ENABLED=true`, Keycloak registration with Verify email (`keycloak-2fa.sh sign-up`, needs the realm's email server), and the `anum-provisioner` client for automatic tenant linking.

## Tests

- `services/api/tests/test_billing.py`: proration, tax, gapless numbering, dunning transitions, trial expiry, limit enforcement, read-only mode, Stripe webhook signatures and idempotency, permissions, the public endpoint leaking nothing, sign-up provisioning and its limits, the audit walk.
- `services/api/tests/test_postgres_billing.py`: forced RLS on every table across tenants, the reader roles' column grants and policies, numbering under PostgreSQL, sign-up provisioning isolated per tenant.
- Web: `apps/web/test/billing.test.ts`, `apps/web/e2e/billing.spec.ts` (view plan, upgrade, invoice, record payment) and `apps/web/e2e/signup.spec.ts` (pricing, sign-up, onboarding).
- Flutter: `apps/mobile/test/billing/billing_test.dart`.

## Not done yet

- The billing contact and address are edited on the web only; the phone shows them.
- Stripe was exercised only against a stand-in (signatures, idempotency, the Checkout request); the first real test-mode payment should be checked on staging. No Playwright spec drives the Stripe redirect, the console's plan editor, issuer settings form or start-billing dialog.
- The `anum-provisioner` Keycloak client is created by hand (steps in [Deploy on Oracle](deploy-oracle.md#self-serve-sign-up)); it is not in the realm file and the linking call was tested against a stand-in of Keycloak's admin API.
- `messages_per_month` is metered and shown but not enforced.
- The console's MRR and totals assume one currency (SAR); plans in another currency are listed but summed as is.
- The console's subscription list is assembled in the API process (fine for hundreds of customers, not paged in SQL).
