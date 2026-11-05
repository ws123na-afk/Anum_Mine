# Business books: expenses, income and profit and loss

Module 5 of the five-module expansion (owner spec, 4 November 2026). The books **extend Business accounting**; they are not a second finance system. Each business is its own workspace (ANUM and Tyre Pulse are separate businesses) and keeps its own books.

**Where every figure comes from:**

| Figure | Source | Never |
|---|---|---|
| Revenue, receivables, output tax | The business's own [invoices, payments and credit notes](business.md#invoices) | A second invoice or a typed revenue figure |
| Costs paid from a member's own money | The allocation rows of [`business_ledger_allocations`](finance-links.md#business-allocations): a member allocates their canonical Finance transaction to the business | The member's bank, account, card, merchant text or messages; a second posting |
| Costs billed by suppliers, payables, input tax | Vendor bills recorded here, with their payments | A payment counted as a cost a second time |
| What the above cannot express | Journal adjustments: accruals, prepayments, capital items and their depreciation, loans and interest, owner money in and out, reimbursing members | An edit after the fact (a mistake is voided with a reason) |

**Nothing posts twice.** The books read only the `business` dimension of Finance links (the allocation rows). A payment that is also a vehicle cost and a purchase is three views of one canonical transaction, and it reaches the P&L once, through its allocation. An allocation used to pay a bill becomes **that bill's payment**: the bill is the cost (accrual) or the payment is (cash), never both. One allocation pays one bill (a partial unique index; two requests racing for it get one 201 and one 409). Nothing here writes `finance_journal_entries`, `finance_postings`, `finance_transactions` or `finance_links`; the tests compare the whole journal before and after.

Code: `services/api/anum_api/business/books_models.py`, `books_store.py`, `books_service.py`, `books_report.py` (the pure figures), `books_group.py` (consolidation, pure), `books_schedule.py` (recurring dates, pure), `books_recurring.py`, `books_rates_auto.py`, `books_jobs.py` (the daily job), `api_books.py` and `api_books_more.py` (routes); `db/business_books_repository.py`; migrations `0137_business_books` and `0138_books_v2`; web `apps/web/src/BusinessBooks.tsx`, `BusinessBooksForms.tsx`, `BusinessBooksMore.tsx`, `lib/businessBooks.ts`, `lib/businessBooksApi.ts`, `lib/messages/businessBooks.{en,ar}.ts`, `businessBooks.css`; contracts `packages/contracts/src/businessBooks.ts`.

## Who can do what

| | Owner (`business:manage`) | Member (`business:write`) | Viewer |
|---|---|---|---|
| Read the P&L, dashboard, lists and exports (`business:read`) | yes | yes | no (403 with who can give access) |
| The combined books of all the businesses they own | yes, only businesses where they hold `business:manage` | no | no |
| Vendors, bills, recording bill payments, recurring bills (create, edit, pause, resume, end), approving a draft bill, cost centres, budgets, classifying allocation rows | yes | yes | no |
| Book settings, exchange rates and automatic rates, journal adjustments, voiding a bill or a bill payment | yes | no (403) | no |
| Intercompany entries | yes, and `business:manage` in the other business too | no | no |

Members of the business see allocation rows and their figures only; the personal ledger, links and messages keep their restrictive owner policies.

## How the figures are made

`books_report.report(inputs, from, to, basis)` is a pure function over the records `books_service.gather` reads (three short reads, no transaction held across them).

**Accrual basis** (the default): revenue is each issued invoice's line nets on its issue date, by line kind (services, labour, passed-on costs, other), less the net of credit notes on their date. Bills count on their date in their section (cost of sales or operating expenses); allocation rows on the date the member paid. Accruals count on their date and reverse on their reverse date; a prepayment takes its amount out on its date and spreads it evenly over its months. Realised exchange differences: a payment in another currency than the base, valued at the payment date less its value at the invoice or bill date.

**Cash basis:** revenue is each invoice payment's net share (payment times net over total) on its date, split over the line kinds; bills count by their payments' net shares; allocation rows as above; accruals and prepayments are left out.

**On both bases:** depreciation of capital bills with a useful life and of capital-item adjustments (straight line, the first month from the bill or the start month, monthly parts that add up exactly); loan interest as a finance cost; intercompany recharges on their date (other income for the side that charges, an operating expense for the other). A capital bill or a `capital` allocation is never an expense when bought. An allocation row waiting for review in Finance (`needs_review`) is not counted and the page says how many.

**Sections.** A bill has a section (vendor default, else operating expenses). An allocation row's section comes from its classification in the books (`business_book_allocation_tags`: cost of sales, operating expense, capital, or `excluded`), else from Settings' category map (`category_sections`), else operating expenses.

**Statement:** revenue, cost of sales, gross profit and margin, operating expenses, operating profit, other income, finance costs, exchange differences, net profit and margin. Then:

- **Tax** per currency: output tax from invoices less credit notes (cash basis: the tax share of payments), input tax from bills, the difference, and the count of allocation rows that carry no tax split. Labelled with the settings' tax label and country. **It is not a tax return and makes no claim of legal compliance.**
- **Cash flow** per currency, never converted: customers paid, suppliers paid, capital items bought, loans received and repaid, interest, owner contributions and drawings, members reimbursed, intercompany settlements and loans, and the net movement. Money members paid from their own pocket (allocation rows) is shown apart as "paid by members", because no business cash moved. **A cash surplus is not profit**: a loan is cash in and debt, never revenue.
- **Owed on the last day**: receivables and payables per currency, aged (not due, 1 to 30, 31 to 60, 61 to 90, over 90 days, aged on today for a period that has not ended), outstanding invoices and unpaid bills, what the business owes members (reimbursable allocations less reimbursements), loans outstanding, intercompany balances per related business, and owner contributions and drawings in the period.
- **Dashboard**: the 12-month trend, customers and projects (revenue and costs per account and project, costs reaching a customer through its projects), cost centres, budget against actual (each budget's own period, in the base currency), recurring costs per month (the newest bill of each recurring vendor and category), and every exchange rate used.

## Currencies and exchange rates

Each business has a base currency (Settings). **Different currencies are never added.** A figure in another currency is converted only with a rate recorded in the books (`business_book_rates`: pair, rate, date, source, who recorded it, and `origin`: typed by an owner or fetched from the ECB, see [Automatic exchange rates](#automatic-exchange-rates)), dated **on or before** the figure's date and at most `rate_max_age_days` old (31 by default), found with `finance/fx.py` (direct, inverse or a cross rate). Each conversion rounds once, half to even, to the base currency's minor unit. The report lists every rate used with its date and source. Without a rate the figure stays apart per currency as **not converted**, and the P&L says it is incomplete. ANUM never invents a rate; an owner records one with its source (for example a central bank reference rate of that day), or turns on automatic ECB rates. A rate an owner typed always wins over a fetched one for the same pair and day (either way round). Payables, receivables, tax and cash flow stay in their own currencies.

## Records

| Record | What it holds |
|---|---|
| Settings | Base currency, country, first month of the financial year, default basis, tax label, category map, maximum rate age; versioned (409 on a stale save). Until an owner saves them the pages show a setup step. |
| Cost centre | Code (unique per business, case-insensitive) and name; archived, never deleted. |
| Vendor | Name (unique per business), tax number, country, default category and section, notes. No personal contact details. |
| Bill | Vendor, supplier's reference, description, bill and due date, currency, net, tax and total kept apart (the database checks `total = subtotal + tax`), category, section, cost centre, project, recurrence, useful life (capital only); `open`, `void` or `draft` (made by a recurring bill set to drafts: counted nowhere and not payable until a member approves it, `POST /bills/{id}/approve`, which makes it `open`); `template_id` and `period_date` when a recurring bill made it. Created with an `Idempotency-Key` (a retry returns the first bill, 200 `replayed`; the same key with other figures, 409). Paid, part paid and overdue are worked out from live payments. Its money and currency cannot change once a payment counts (409 `has_payments`); a bill with payments cannot be voided. |
| Bill payment | `recorded` (bank transfer, cash, card, cheque or other outside ANUM, typed by a member, not in the future, at most the balance) or `allocation` (an active allocation row of this business in the bill's currency, at most the balance, dated when the member paid). Idempotent per bill. Voided by an owner with a reason, never deleted. If the allocation is undone or removed in Finance the payment stops counting and the bill says so. |
| Allocation classification | Section (or `excluded`), cost centre and a note for one allocation row; versioned. Changes how the books count it, never the payment. |
| Budget | Name, first month, 1, 3, 6 or 12 months, amount in the base currency, and optionally section, category, project and cost centre. |
| Rate | 1 base = rate quote on a date, from a source (required); `origin` `manual` or `ecb`. One per pair and day; an owner's rate for a pair and day replaces a fetched one. |
| Recurring bill | The bill's fields (vendor, reference, description, currency, net and tax, category, section, cost centre, project, useful life), the schedule (monthly, quarterly, yearly, or every N days, weeks or months; first date; due days after each bill date; an end date or a number of bills, or never), whether new bills are `open` or `draft`, its status (`active`, `paused`, `ended`, with why it ended), the next period and how many bills it made. Versioned. |
| Rate feed | One per business: on or off, the currencies followed, when the last fetch was tried and succeeded, the date of the rates stored, the last error, the chosen currencies the ECB does not publish. |
| Adjustment | Kind, date, currency, amount, section and category (accruals and prepayments), cost centre, project, why, start month and months (prepayments and capital items), reverse date (accruals). Idempotent. **A trigger refuses every update except voiding an active one**, so the trail stays. |
| Intercompany entry | Recharge, loan or settlement with another business of the organisation, direction (receivable or payable), date, currency, amount, why. Written in both businesses with one `pair_id` and the direction mirrored, in one transaction; voiding voids both. |

## Intercompany

The caller must hold `business:manage` in **both** businesses (an active membership in the same tenant, the other workspace not archived), checked on every create and void; anything else answers `403 workspace_not_allowed`. Both rows are written in one unit of work by setting the other workspace for that statement only (the pattern of the finance allocation rows). Each business's books show their side, and the balances per related business show who owes whom; the [consolidated books](#consolidated-books) take both sides out of the group's total. This never touches Tyre Pulse's own product database; Tyre Pulse here is a business workspace in ANUM.

## Consolidated books

Business, Books, **All businesses** (owners only; `GET /group`, `GET /group.csv`). One profit and loss, the balance figures and the cash flow of every business where the viewer holds `business:manage` and whose books are set up, in a reporting currency the owner chooses (by default this business's base currency), with a column per business, an **Eliminations** column and the **Total**.

- **Who is in it.** This business and every other business of the organisation where the caller is an owner: each is checked again (an active membership with `business:manage`, the same tenant, the workspace not archived; `books_service.manageable_workspace`) before anything of it is read, in that business's own RLS scope. Businesses without book settings are listed apart, never read further. A business the caller does not own is never part of it and never named; asking for one (`workspace=`), for one that does not exist or that is in another organisation answers the same `403 workspace_not_allowed`. Members and viewers get 403. With fewer than two businesses the answer says so (`enough: false`) and the page explains the next step. Owners pick which of their businesses to include (at least two).
- **Currencies.** Every figure is converted on its own date with **that business's own** rates (typed or fetched, within its maximum rate age, `finance/fx.py` direct, inverse or cross). A figure without a rate is listed per business, line and currency as **not converted**, left out of the columns and the total, never summed. Balances convert on the last day (or today, for a period that has not ended); cash flow movements on their own dates. Exchange differences on accrual are measured against the reporting currency. Every rate used is listed with its business, date and source.
- **Eliminations.** Intercompany entries are mirrored in both businesses. When both businesses are in the consolidation, a recharge (other income on one side, an operating expense on the other), a loan and a settlement between them are taken out of the total, each side by the value it was converted at: internal recharges and loans never inflate revenue, costs or debt (intercompany receivables and payables, loans lent and borrowed, settlements in and out all net to zero). An entry with a business outside the consolidation stays: for the group it is real.
- **CSV:** a row per line with a column per business, the eliminations, the total and the currency; then what is not converted and the rates used. Cells that start with `=`, `+`, `-` or `@` are escaped (so a negative amount reads `'-100.00`).

The figures are pure (`books_group.consolidate` over each business's `books_service.gather` inputs). Not built: a group balance sheet (the figures are management figures, as for one business), minority interests, and translation reserves.

## Automatic exchange rates

An opt-in per business (Books, Settings, **Automatic exchange rates**; `GET`, `PUT /rates/auto`, `POST /rates/auto/refresh`). Once a day the [daily job](#daily-job) reads the European Central Bank's euro reference rates with Finance's connector and strict parser (`finance/connectors.http_get`, `finance/fx.parse_ecb`: no redirects, a capped size, a pattern instead of an XML parser, a recent date) and stores in the business's own book rates `1 EUR = rate X` for the base currency and each currency chosen, with the ECB's date, the source `ECB` and `origin: ecb`. Conversions between other currencies go through the euro (a cross rate). The UAE dirham is not in the ECB list: it is derived from EUR to USD and the Central Bank of the UAE's fixed peg (3.6725 AED per USD), exactly as `fx.with_aed_peg` does, and its source says so. **No other peg is assumed**: Finance does not treat the Saudi riyal or any other currency as pegged, so a currency the ECB does not publish (SAR, KWD, QAR, BHD, OMR, EGP, PKR) gets no automatic rate and is listed as unavailable; an owner records those by hand.

- **A typed rate always wins.** The job never stores a rate for a pair and day that already has one (either way round); an owner's rate for a pair and day that has a fetched one replaces it (`rate_recorded` audit names the replaced id); and conversions drop a fetched rate when a typed one exists for the same pair, either way round, and day (`books_report.prefer_manual`).
- **A failed fetch changes nothing.** The feed keeps when it last succeeded and the rates' date, and records when and why the last attempt failed; the page shows both. Reports go on using the last rates while they are within the business's maximum rate age, then show figures as not converted.
- **Fetch now** (owners) reads the file at once for this business, at most every ten minutes (`429 too_soon`). The network read happens before any store is opened.
- The ECB file is read once per job run, and only when at least one business opted in.

## Recurring bills

Business, Books, **Recurring bills** (`/recurring`). A recurring bill is a template of a vendor bill with a schedule: monthly, quarterly, yearly, or every N days (1 to 366), weeks (1 to 52) or months (1 to 24); the first bill's date (at most a year in the past); the due date offset (0 to 366 days after each bill date); and an end: never, on a date, or after a number of bills (1 to 1,000). Its bill fields are checked exactly like a bill's.

- **Each due bill exactly once.** Each period makes one **normal bill** (`open`, or `draft` when the template says so) dated on the period, with the lines' figures (net, tax, total), category, section, cost centre, project and useful life copied, and `template_id` and `period_date` set. A partial unique index (`uq_business_book_bills_period`) allows one bill per template and period, and the template row is locked while its bills are made, so two runs that overlap (the job twice, or the job and a member saving the template) make each bill once; tested with parallel runs in PostgreSQL. A bill already due is made when the template is created or resumed; the job makes the rest, at most 60 per template per run (a longer backlog continues the next day). A month step keeps the first date's day and falls back to the month's last day (31 January, 28 or 29 February, 31 March).
- **Pause** makes nothing; **resume** continues from the first period on or after today (periods passed while paused are not made late). **Edit** changes the bills made from then on, never one already made; a new schedule starts after the last bill made (`422 start_before_last_bill`). **End** stops it for good (`ended`, with the reason: by a member, end date passed, or all its bills made); bills already made stay. Voiding a bill it made does not make that period again.
- **Draft bills** count nowhere (P&L, payables, bill totals) and cannot be paid (`409 bill_draft`) until a member approves them.
- **Where they show:** the Recurring bills section (each recurring bill with its schedule, cost per month and next bill, and the bills coming in the next 90 days), a drawer per recurring bill (its bills made, open each one), and the recurring costs report on the P&L (every active or paused recurring bill with its cost per month, a paused one left out of the monthly total, plus the bills only marked recurring that no recurring bill covers, and the bills coming).

## Daily job

`python -m anum_api.voice_retention` runs `books_jobs.run` after the purchase reminders and reports it under `business_books` (counts only: feeds, rates added, whether the fetch failed, businesses, bills made, failures; `--dry-run` changes nothing and fetches nothing). Businesses are discovered as `anum_maintenance`, which may read only `tenant_id`, `workspace_id`, `status` and `next_date` of active recurring bills and `tenant_id`, `workspace_id` and `enabled` of enabled rate feeds (column grants and the policies `maintenance_business_book_templates_due` and `maintenance_business_book_rate_feeds_on`), and cannot write. The work then runs as the application role inside each business's own tenant and workspace RLS context, as `system:books`; archived workspaces are skipped. Nothing bypasses row level security. One business's failure never stops the run.

## API

All under `/api/v1/business/books`. Refusals answer `{"detail": "<plain words>", "code": "<code>"}` with figures where they help (`remaining`). Audit rows (`business.books.*`) hold ids and codes only.

| Route | |
|---|---|
| `GET /setup` | Settings (or null), permissions, counts, the default period, open cost centres, allocation categories |
| `PUT /settings` | manage; `version` (null for the first save) |
| `GET /report?from&to&basis`, `GET /report.csv` | read; the period defaults to the current financial year; at most three years |
| `GET /projects?from&to&limit&offset` | read; each project's books figures next to its contract profitability (`GET /business/projects/{id}/profitability`, reused) |
| `GET`, `POST /cost-centres`, `PATCH /cost-centres/{id}` | read, write |
| `GET`, `POST /vendors`, `PATCH /vendors/{id}` | read, write; `q`, `archived`, paging |
| `GET /bills?q&status&vendor_id&project_id`, `POST /bills` (`Idempotency-Key`), `GET`, `PATCH /bills/{id}`, `POST /bills/{id}/void`, `GET /bills.csv` | read, write, manage for void; status `open`, `overdue`, `unpaid`, `partly_paid`, `paid`, `recurring`, `void`; the list carries open and overdue totals per currency |
| `POST /bills/{id}/payments` (`Idempotency-Key`), `POST /bills/{id}/payments/{payment}/void` | write, manage |
| `GET /allocations?status&q`, `PUT /allocations/{id}/tag` | read, write; status `active`, `needs_review`, `unpaid_bill`, `untagged` |
| `GET`, `POST /budgets`, `PATCH`, `DELETE /budgets/{id}` | read, write |
| `GET`, `POST /rates`, `DELETE /rates/{id}` | read, manage |
| `GET /adjustments?kind&include_void`, `POST /adjustments` (`Idempotency-Key`), `POST /adjustments/{id}/void` | read, manage |
| `GET /intercompany/counterparties`, `GET`, `POST /intercompany`, `POST /intercompany/{id}/void` | manage, read, manage in both businesses |
| `POST /bills/{id}/approve` (`{version}`) | write; a draft becomes open |
| `GET /group?from&to&basis&currency&workspace=...`, `GET /group.csv` | manage here, and only businesses where the caller holds manage |
| `GET /recurring?status&q&limit&offset` (with `upcoming`), `POST /recurring`, `GET`, `PATCH /recurring/{id}`, `POST /recurring/{id}/pause`, `/resume`, `/end` (`{version}`) | read, write |
| `GET /rates?origin=manual\|ecb` | read |
| `GET /rates/auto`, `PUT /rates/auto` (`{enabled, currencies, version}`), `POST /rates/auto/refresh` | read, manage, manage |

CSV cells that start with `=`, `+`, `-`, `@` and similar are escaped.

## Web

Business, tab **Books** (`#business-books`, sections `#business-books/<section>`): **Profit and loss** (period presets on the financial year, custom dates, accrual or cash, Export CSV; the summary band, the statement, not converted figures, tax, cash flow with what members paid, what is owed and its age, outstanding invoices linked to Invoices, the 12-month trend, customers, projects linked to Projects, cost centres, budget against actual, recurring costs, rates used and notes), **Bills** (search, filter, paging, Export CSV, new bill, the bill drawer with payments by hand or with an allocation, edit, void), **Vendors**, **Allocations** (classify each row), **Budgets**, **Journal** (adjustments, void with a reason), **Intercompany** and **Settings** (settings, category map, cost centres, exchange rates typed by owners, automatic exchange rates), plus **All businesses** (owners only: the consolidated books with a column per business, eliminations and the total, the businesses included, the reporting currency, what is not converted, the rates used and Export CSV) and **Recurring bills** (search, filter by status, new recurring bill, the bills coming, the drawer with pause, resume, edit and end, and the bills made). Every section handles loading, empty with a next action, errors with Try again, a member's read-only view of owner actions, and paging; English and Arabic, right to left. Tables that do not fit scroll sideways inside a focusable region.

## Storage, privacy and deletion

Migration `0138_books_v2` (expand only, after `0137_business_books`) adds `business_book_bill_templates` and `business_book_rate_feeds` (forced RLS, tenant and purge policies, the maintenance column grants above, listed in `tenant_lifecycle_tables.py`), `template_id` and `period_date` on bills with the per-period unique index and a composite foreign key to the template, the `draft` bill status, and `origin` on rates (default `manual` for every existing row). The previous release never writes these and ignores the new columns; a draft bill read by the previous release would fail to load, so roll back only before any recurring bill set to drafts made one.

Migration `0137_business_books` (expand only, after `0136_purchases`): `business_book_settings`, `business_book_cost_centres`, `business_book_vendors`, `business_book_bills`, `business_book_bill_payments`, `business_book_allocation_tags`, `business_book_budgets`, `business_book_rates`, `business_book_adjustments`, `business_book_intercompany`. Every table has forced RLS with `tenant_isolation_<table>` and `lifecycle_purge_<table>` (the 0079 grants), and is listed in `tenant_lifecycle_tables.py` for the organisation export, workspace deletion and purge. References between book tables are composite foreign keys on `(tenant_id, workspace_id, id)`. Checks keep totals equal to net plus tax, positive amounts, valid currencies, a void naming who voided it, one allocation per live bill payment; the adjustments trigger keeps the trail.

These are **business records** of the workspace, like invoices: they hold ids of who recorded them and no personal data (vendors carry no contact details). They are not part of a person's privacy export, and "Delete my data" leaves them with the business; deleting the business workspace or the organisation removes them. The allocation rows themselves follow [Finance links](finance-links.md#storage-privacy-and-deletion).

## Tests

`services/api/tests/test_business_books.py` (permissions and the setup step, vendors and cost centres, idempotent and validated bills with tax apart, payments by hand and voids, the acceptance case: an Apple Pay plus SMS fuel payment linked to a vehicle, a purchase and allocated to the business counts once in the P&L and once when it pays a bill, with the journal unchanged; undoing the payment in Finance takes it out of the books; invoices as revenue on both bases with credit notes and tax; a loan is cash, not profit; rates dated on or before and their source listed; adjustments spread, depreciate, reverse and only void; intercompany mirrored and refused without ownership of both; budgets, classifications, cost centres, projects and recurring costs; other businesses and tenants read nothing), `tests/test_business_books_report.py` (the pure figures: fiscal years, exact spreads, realised exchange differences, stale rates, cash-basis tax split and capital spend, prepayments), `tests/test_postgres_business_books.py` (forced RLS and purge policies, every kind written and read under RLS with nothing visible to other scopes, an allocation paying one bill when two requests race with no journal change, the frozen adjustments trigger, intercompany rows in both workspaces), web `test/businessBooks.test.ts` and `e2e/business-books.spec.ts` (the gate at five sizes in English and Arabic with axe), plus the Books entries in `e2e/support/gatePages.ts`. Second release: `tests/test_business_books_v2.py` (consolidation with conversions and eliminations, not converted never summed, an outsider entry kept, only businesses the viewer owns with a member of only one business and another tenant reading nothing, CSV; ECB rates with date, source and the AED peg, a typed rate winning both ways, a failed fetch keeping the rates, owners only and the ten-minute limit, no fetch without a feed; schedules, each bill once, overlapping runs, drafts, pause, edit for future bills, end, validation, the recurring costs report and upcoming bills, the dry run), `tests/test_postgres_business_books_v2.py` (forced RLS and the narrow maintenance policies, two job runs in parallel making one bill per period, the unique index, the maintenance role refused content columns and writes, fetched rates under RLS replaced by a typed one, the group across two workspaces, the draft status check), web `test/businessBooks.test.ts` and `e2e/business-books-v2.spec.ts` (the gate at five sizes in English and Arabic with axe), and two more gate pages.

## Not built here

- Flutter screens: the books are web only in this release.
- A business bank ledger visible to members (owner decision 1: allocations, not a workspace-visible ledger); payments from the business's own account are recorded by hand on the bill. Business card liabilities are not modelled.
- A group balance sheet, minority interests or translation reserves in the consolidation.
- Automatic rates for currencies the ECB does not publish: no peg other than the dirham's is assumed (the Saudi riyal's would need an owner decision to add it to `finance/fx.py`).
- Tax returns or filings of any jurisdiction, and a balance sheet; the figures are management figures.
