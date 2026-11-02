# Ready to sell: Gulf card payments, company and legal details, and the checklist

What ANUM needs before the platform owner takes money from customer organisations in Saudi Arabia and the UAE, and where each piece lives. Built on [Customer billing](billing.md), [Organisation export and deletion](organisation-lifecycle.md), [Privacy](privacy.md) and the [legal pages](legal-pages.md).

Implemented in `services/api/anum_api/billing/moyasar.py` (the Moyasar adapter), `billing/company.py` (company details and legal facts), `billing/selling.py` (routes), migration `0123_selling_readiness`; web `apps/web/src/billing/ConsoleReadiness.tsx`, `ConsoleCompany.tsx`, `ConsoleMoyasar.tsx`, the refund dialog in `ConsoleOrganisation.tsx`, receipts in `BillingSettings.tsx`, and the public legal pages in `PublicSite.tsx` (`lib/publicLegalApi.ts`, `lib/selling.ts`).

## Payment provider

### Why Moyasar

Two Saudi payment providers were compared from what is publicly documented about them (the official documentation sites could not be fetched from the build environment, so the choice must be confirmed by the owner when opening the account):

| Need | Moyasar | HyperPay |
|---|---|---|
| API | Plain JSON REST, HTTP Basic auth with the secret key, amounts in minor units like ANUM | OPPWA form-encoded API with an access token, separate entity ids per card brand (mada needs its own) |
| ANUM never touches card numbers | Hosted payment page (a Moyasar *invoice* with a URL) | COPYandPAY widget script on the merchant page |
| mada, Visa, Mastercard, Apple Pay, STC Pay | Yes | Yes |
| SAR and AED | SAR by default; other currencies per account (ask Moyasar to turn on AED) | Per entity |
| Refunds by API | `POST /v1/payments/{id}/refund {amount}` | Yes |
| Sandbox | Test keys `sk_test_` on the same API | Test host |
| Webhooks | A shared `secret_token` in the body | AES-256-GCM encrypted body with IV and tag headers |

Moyasar was chosen: the cleanest API, a hosted page that keeps ANUM out of card data, and one key pair for every card brand. Its one weakness is the webhook: it carries the owner's secret token inside the JSON body instead of an HMAC header. ANUM makes up for it: the token is compared in constant time (`hmac.compare_digest`), and **no amount, status, currency or invoice is ever taken from the delivered body**. The payment is fetched again from Moyasar's API with the secret key (`GET /v1/payments/{id}`, and `GET /v1/invoices/{id}` for the ANUM ids in its metadata) and only what Moyasar's API says is recorded. A forged delivery with a stolen token can therefore only make ANUM look up a real payment.

### How it works

- **Off by default.** The platform owner enters the secret key (`sk_test_` or `sk_live_`) and the webhook secret token in Platform billing > Settings > Card payments (Moyasar). Both are stored only as Fernet ciphertexts in `billing_settings` (`ANUM_SECRETS_KEY`, rotated by `python -m anum_api.rotate_secrets` with the Stripe keys) and never returned; the console shows whether each is saved and whether the key is a test key. Moyasar cannot be turned on without both.
- **Paying.** An organisation billing admin opens an open invoice and presses Pay by card: `POST /api/v1/billing/invoices/{id}/checkout` creates a Moyasar invoice for the amount due with `success_url`/`back_url` back to Settings > Billing and the ANUM tenant and invoice ids in its metadata, and answers its URL. When both Moyasar and Stripe are on, Moyasar is used. Currencies sent to Moyasar: SAR, AED, USD; another currency is refused before Moyasar is called.
- **Confirming.** Moyasar calls `POST /api/v1/billing/webhooks/moyasar/{issuer_tenant_id}` (events `payment_paid` and `payment_refunded`). Wrong or missing token: `400`, nothing changes. Unknown issuer or Moyasar off: `404`. Each event id is stored once in `billing_webhook_events` (a repeat answers `200 {"duplicate": true}`); each Moyasar payment id is applied once whatever event carries it (`payment:<id>` marker, and `provider_payment_id` is unique per issuer). If Moyasar cannot be reached the answer is `502` and the event is not marked seen, so Moyasar's retry tries again. A payment for an invoice billed by another issuer, in another currency, or not `paid` is ignored. Money above the amount due, or for an invoice paid another way meanwhile, goes to the organisation's credit balance, as on the Stripe path.
- **Recorded** as method `card`, provider `moyasar`, actor `system:moyasar`, reference `Moyasar <payment id>`.

## Refunds

`POST /api/v1/platform/billing/organizations/{tenant_id}/invoices/{invoice_id}/payments/{payment_id}/refunds {amount_minor, reason, mode, idempotency_key}` (platform owner only, for organisations it bills):

- `mode: "provider"` refunds a Moyasar card payment through Moyasar; `mode: "manual"` records money returned outside ANUM (a bank transfer back, or a refund done in the Stripe dashboard). A non-Moyasar payment cannot use `provider` (`409` says to record it by hand).
- The amount is at most what is left of the payment (its amount less refunds that did not fail), the invoice's paid amount and its uncredited total (`422` otherwise).
- **Idempotent.** `idempotency_key` (8 to 120 characters) is unique per issuer (`uq_billing_refunds_key`). The same key with the same payment and amount returns the first refund (`200`) and never refunds twice; with anything else `409`. The web dialog makes one key per dialog, so a retry after a network error is the same refund.
- **No transaction across the provider call.** The refund is claimed as `pending` in one short transaction, Moyasar is called with none open, then the result is booked in a second one. A replay of a claim still `pending` (the API died between the two) first reads the payment's `refunded` total from Moyasar and only calls the refund again when Moyasar has not refunded it yet. A provider refusal marks the refund `failed` with the reason and books nothing; the amount is free for a new attempt.
- **Booking.** A succeeded refund issues a credit note for the same amount (next gapless `CN-` number, reason `Refund: ...`), lowers the invoice's paid amount and raises its credited amount, so `total = paid + credited + due` still holds: nothing becomes due again and nothing lands on the credit balance.
- **Refunds made in the Moyasar dashboard.** A `payment_refunded` webhook compares Moyasar's refunded total with ANUM's refunds of that payment: pending ones it covers succeed; any extra is recorded once as a refund "Refunded in the Moyasar dashboard" with its credit note (keyed by the payment id and Moyasar's total).
- Audited as `billing.refund.create` with ids, amount, mode and status only.

Table `billing_refunds` (customer tenant, forced RLS, organisation purge policy): payment, invoice, amount, currency, provider and provider refund id (unique per issuer and provider), status (`pending`, `succeeded`, `failed`), reason, idempotency key, credit note, failure, who asked, when. Invoice details now include `refunds`, and each payment `refunded_minor` and `refundable_minor`.

## Receipts

`GET /api/v1/billing/invoices/{id}/payments/{payment_id}/receipt` (the organisation's billing admins) and `GET /api/v1/platform/billing/organizations/{tenant}/invoices/{id}/payments/{payment_id}/receipt` (the platform owner) answer a PDF receipt, labelled in English and Arabic with Arabic names printed as Arabic: who received the money (the company details below), who paid, date, amount, method and provider, reference, the invoice and what is still due, and any refunds of the payment. The invoice drawer has a Receipt button per payment.

## Company and legal

The platform owner's company details live in `billing_company_profiles` (one row in the issuer's tenant, forced RLS): legal name, trading name, registration number, country and issuing authority, registered address, contact, support and security emails, phone, VAT number or TRN, data protection officer (name and email), server location (provider and region), hosting provider and governing law, plus `legal_terms` (the facts only the owner and a lawyer decide, from a fixed list of keys) and `published_docs`.

| Route | Who | What |
|---|---|---|
| `GET`, `PUT /api/v1/platform/legal` | platform owner | The company fields, the legal facts with labels and help, every placeholder's value or `null`, what each document still misses, the facts generated by the server. Emails, country code, VAT and phone are validated; unknown fact keys and texts over 2000 characters are refused. Marking a document published needs `lawyer_reviewed: true` and no missing fact in it. Audited with the names of the fields set, never their values. |
| `GET /api/v1/public/legal` | anyone | The configured issuer's (`ANUM_BILLING_ISSUER_TENANT_ID`) company fields, values, published documents and missing facts. Read through `anum_platform_reader` with a column grant that leaves out `updated_by` and a policy that admits only that issuer's row, plus the issuer's two provider switches (`stripe_enabled`, `moyasar_enabled`) and nothing else of its billing settings. Cached 60 seconds; no tenant id in the answer. |

**Filling the documents.** Every `{{PLACEHOLDER}}` in `docs/legal` is filled from, in this order: a fact the server knows (the service address, conversation, learning and call retention, the deletion cooling-off, the local backup days from the last backup record, file storage region from Backblaze settings, and whether each subprocessor is in use: Anthropic or an OpenAI-compatible model provider, speech, SMTP, push, Backblaze, Let's Encrypt, Moyasar, Stripe; Twilio, WhatsApp and Brevo are "Only when a workspace connects its own account"); the company fields; the typed legal facts. A fact the server knows always wins, so a page cannot claim a provider that is turned off. The public page shows **Not set yet** with the fact's name for anything missing, and a company card (who provides ANUM) once the legal name is set. `tests/test_selling_readiness.py` fails when a document gains a placeholder the API does not know.

**Invoices** issued after the company details are saved print the legal name, address, registration and VAT number from them (they win over the billing settings' issuer fields); earlier invoices keep their snapshot.

**Arabic pages.** Each document has an Arabic text in `docs/legal/ar/` with exactly the same placeholders (a unit test compares them), the same DRAFT notice and the same links. The page language follows the site's language switch: Arabic visitors get the Arabic text right to left, with a note that the English text applies until a lawyer approves the translation; the company card, the rights panel, the draft banner and "Not set yet" are in the visitor's language. The same company details and legal facts fill both languages; the fixed words the server generates in English ("Yes", "No", "Not used", "Only when a workspace connects its own account", country names, the plan page sentence) are shown in Arabic on the Arabic page, and what the owner typed is shown as typed. The lawyer's review and the published mark cover both texts: a lawyer must approve the Arabic text too before it is relied on.

**Templates, not legal advice.** Every document still opens with the template notice, and the console says in English and Arabic that a lawyer in Saudi Arabia or the UAE must review them. A document shows as published only when the owner marked it so after that review (or the build's `VITE_ANUM_LEGAL_PUBLISHED` lists it) and none of its facts is missing.

### What the documents now cover (Saudi PDPL and UAE PDPL basics)

- **Lawful basis and purposes**: the Privacy Policy's purpose table, now with billing; each basis is a fact counsel confirms.
- **Retention**: the retention table, with meeting transcripts and visitor records from Settings > Workspace > How long records are kept ([Record retention](record-retention.md)), billing records (a fact), backups.
- **Data subject rights**: access, correction, deletion and export; the privacy, DPA and terms pages end with a panel whose buttons open the app's own flows: Download my data and Delete my data (Settings > Privacy, [Privacy](privacy.md)), Export or delete the organisation (Settings > Workspace > Data export and deletion, [Organisation export and deletion](organisation-lifecycle.md)) and the retention settings. The DPA no longer says organisation deletion is an operator task: it is self-service with a cooling-off period.
- **Cross-border transfers**: the server location from the company details and the subprocessor locations, with the transfer statement and mechanism as facts counsel writes, naming the Saudi transfer regulation and the UAE PDPL rules.
- **Breach notification**: a new Privacy Policy section (the competent authority, SDAIA or the UAE Data Office, and affected people, within the period the applicable law sets; customers within the DPA's hours), and the DPA's breach section adds help with notifying authorities.
- **Subprocessors**: a new Payments table (Moyasar, Stripe) and every "In use" column generated from what this deployment has turned on.

### Cookies

Checked again for this change: the public site and the app set no cookies and load no analytics or advertising scripts (no `document.cookie` or `Set-Cookie` in the web app or the API; Moyasar's page is on Moyasar's own domain). So there is no cookie banner, as [Public pages](legal-pages.md#cookies) says.

## Checklist

Platform billing > **Ready to sell** (`GET /api/v1/platform/readiness`, platform owner only) reads real state and lists what blocks selling first. English and Arabic.

| Item | Done when | Otherwise it says | Fix |
|---|---|---|---|
| Card payment provider | Moyasar or Stripe is on with a key and a webhook secret | Each provider's state; a **Check** state while only test keys are saved; whether bank details are on invoices | Settings tab |
| Company details | Legal name, registration number and country, address, contact email, privacy email and server location are set | Which fields are empty | Company and legal tab |
| Mail sending | A mailbox is connected in the owner's workspace (invoice emails go through it, after approval) | Whether a mailbox and server SMTP are set; SPF cannot be known from code | Email, plus the DNS check |
| Backups on the server, Off-site backups | The last run recorded by `backup.sh` succeeded | Not recorded, never succeeded, or the last run's time and the last success; the Backblaze key hint for off-site | Run `backup.sh backup` on the server |
| Legal pages | No fact is missing and all five documents are marked published after a lawyer's review | Facts missing per document; how many are published | Company and legal tab |
| Plans | At least one plan is published on the pricing page | Active and published counts | Plans tab |
| This organisation sells ANUM | `ANUM_BILLING_ISSUER_TENANT_ID` is this organisation | Not set, another organisation, or `ANUM_WEB_APP_URL` missing | `infra/oracle/.env` |

**DNS check.** `POST /api/v1/platform/readiness/dns {domain}` resolves the domain's TXT records and `_dmarc.<domain>` on the server (dnspython, 5 second limit, no cache) and reports the SPF record with its status (`ok`, `missing`, `multiple`, `no_senders` such as `v=spf1 ~all`, which authorises nobody, or `too_open` for `+all`) and the DMARC policy (`ok` for quarantine or reject, `monitor` for `p=none`, `missing`). The domain defaults to the company contact email's domain. Only well-formed domain names are accepted (no IP addresses).

### Backups

`infra/oracle/backup.sh backup` now writes `<backup dir>/status/last-local.json` and `last-offsite.json` after each run: `{"ok", "finished_at", "last_success_at", "keep_days"}` (times and the local keep days only, world-readable). The Oracle compose file mounts that folder read-only into the API at `/var/lib/anum/backup-status` (`ANUM_BACKUP_STATUS_DIR`). With no file the checklist says "Not recorded" rather than guessing.

## Tests

- `services/api/tests/test_selling_readiness.py` (fake Moyasar over `httpx.MockTransport`, never the real service): write-only keys and both secrets required, operator-only settings, the hosted page request (amount, currency, metadata, no card data), SAR and AED accepted and another currency refused before any call, the token compared through `hmac.compare_digest`, the amount read back from Moyasar instead of the body, duplicate events and payments, another issuer's invoice, a wrong currency and an unpaid payment ignored, Moyasar unreachable answered `502` and retried, refunds through Moyasar with a credit note and no credit balance, the idempotency key replay and conflict, limits, a failed provider refund booking nothing, manual refunds, a dashboard refund recorded once, refund permissions and isolation, receipts for both sides and refused across tenants, company details filling the public facts and the invoice PDF, Moyasar listed as a subprocessor when on, publishing refused without the lawyer confirmation or with facts missing, validation, each operator seeing only their own details, the public read without an issuer, the checklist from real state with backup files, the SPF analysis and the DNS route; every placeholder in `docs/legal` known to the API.
- `services/api/tests/test_postgres_selling_readiness.py`: the main path in PostgreSQL (company details, Moyasar on, webhook, refund, receipt, public facts, checklist) as the application role; forced RLS on both new tables across tenants; the reader sees only the configured issuer's company row and the two provider switches, never `updated_by`, a key ciphertext, bank details or refunds; keys stored only as ciphertexts.
- `apps/web/test/selling.test.ts`, `apps/web/e2e/selling.spec.ts` (checklist, DNS check, company and legal form, Moyasar settings, refund, the console tabs and the filled privacy notice at 1440x900, 1366x768, 1024x768, 768x1024 and 390x844 in English and Arabic with axe), and the updated `e2e/public-site.spec.ts` (the Arabic legal pages right to left at the five sizes with axe); `apps/web/test/legalArabic.test.ts` (same placeholders, DRAFT notice and links in every Arabic text, the generated values in Arabic).

## Not done yet

- No real Moyasar payment has been made: the adapter was tested only against a fake. The first test-mode payment, refund and webhook must be checked on staging with the owner's `sk_test_` key, and Moyasar must confirm AED on the account.
- Moyasar's official API documentation could not be read from the build environment; the request and response fields follow Moyasar's published API as known, and the owner's first sandbox payment is the check.
- Stripe refunds are recorded by hand (mode `manual`); there is no Stripe refund API call.
- The console's "collected this month" counts payments received and does not subtract refunds.
- The Arabic legal texts are translations written for this change and not yet reviewed by a lawyer; until a lawyer approves them the page says the English text applies. Legal facts the owner types in English are shown as typed on the Arabic page.
