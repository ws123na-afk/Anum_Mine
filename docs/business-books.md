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

Code: `services/api/anum_api/business/books_models.py`, `books_store.py`, `books_service.py`, `books_report.py` (the pure figures), `api_books.py` (routes); `db/business_books_repository.py`; migration `0137_business_books`; web `apps/web/src/BusinessBooks.tsx`, `BusinessBooksForms.tsx`, `lib/businessBooks.ts`, `lib/businessBooksApi.ts`, `lib/messages/businessBooks.{en,ar}.ts`, `businessBooks.css`; contracts `packages/contracts/src/businessBooks.ts`.

## Who can do what

| | Owner (`business:manage`) | Member (`business:write`) | Viewer |
|---|---|---|---|
| Read the P&L, dashboard, lists and exports (`business:read`) | yes | yes | no (403 with who can give access) |
| Vendors, bills, recording bill payments, cost centres, budgets, classifying allocation rows | yes | yes | no |
| Book settings, exchange rates, journal adjustments, voiding a bill or a bill payment | yes | no (403) | no |
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

Each business has a base currency (Settings). **Different currencies are never added.** A figure in another currency is converted only with a rate recorded in the books (`business_book_rates`: pair, rate, date, source, who recorded it), dated **on or before** the figure's date and at most `rate_max_age_days` old (31 by default), found with `finance/fx.py` (direct, inverse or a cross rate). Each conversion rounds once, half to even, to the base currency's minor unit. The report lists every rate used with its date and source. Without a rate the figure stays apart per currency as **not converted**, and the P&L says it is incomplete. ANUM never invents a rate; an owner records one with its source (for example a central bank reference rate of that day). Payables, receivables, tax and cash flow stay in their own currencies.

## Records

| Record | What it holds |
|---|---|
| Settings | Base currency, country, first month of the financial year, default basis, tax label, category map, maximum rate age; versioned (409 on a stale save). Until an owner saves them the pages show a setup step. |
| Cost centre | Code (unique per business, case-insensitive) and name; archived, never deleted. |
| Vendor | Name (unique per business), tax number, country, default category and section, notes. No personal contact details. |
| Bill | Vendor, supplier's reference, description, bill and due date, currency, net, tax and total kept apart (the database checks `total = subtotal + tax`), category, section, cost centre, project, recurrence, useful life (capital only); `open` or `void`. Created with an `Idempotency-Key` (a retry returns the first bill, 200 `replayed`; the same key with other figures, 409). Paid, part paid and overdue are worked out from live payments. Its money and currency cannot change once a payment counts (409 `has_payments`); a bill with payments cannot be voided. |
| Bill payment | `recorded` (bank transfer, cash, card, cheque or other outside ANUM, typed by a member, not in the future, at most the balance) or `allocation` (an active allocation row of this business in the bill's currency, at most the balance, dated when the member paid). Idempotent per bill. Voided by an owner with a reason, never deleted. If the allocation is undone or removed in Finance the payment stops counting and the bill says so. |
| Allocation classification | Section (or `excluded`), cost centre and a note for one allocation row; versioned. Changes how the books count it, never the payment. |
| Budget | Name, first month, 1, 3, 6 or 12 months, amount in the base currency, and optionally section, category, project and cost centre. |
| Rate | 1 base = rate quote on a date, from a source (required). One per pair and day. |
| Adjustment | Kind, date, currency, amount, section and category (accruals and prepayments), cost centre, project, why, start month and months (prepayments and capital items), reverse date (accruals). Idempotent. **A trigger refuses every update except voiding an active one**, so the trail stays. |
| Intercompany entry | Recharge, loan or settlement with another business of the organisation, direction (receivable or payable), date, currency, amount, why. Written in both businesses with one `pair_id` and the direction mirrored, in one transaction; voiding voids both. |

## Intercompany

The caller must hold `business:manage` in **both** businesses (an active membership in the same tenant, the other workspace not archived), checked on every create and void; anything else answers `403 workspace_not_allowed`. Both rows are written in one unit of work by setting the other workspace for that statement only (the pattern of the finance allocation rows). There is no consolidated view yet: each business's books show their side, and the balances per related business show who owes whom. This never touches Tyre Pulse's own product database; Tyre Pulse here is a business workspace in ANUM.

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

CSV cells that start with `=`, `+`, `-`, `@` and similar are escaped.

## Web

Business, tab **Books** (`#business-books`, sections `#business-books/<section>`): **Profit and loss** (period presets on the financial year, custom dates, accrual or cash, Export CSV; the summary band, the statement, not converted figures, tax, cash flow with what members paid, what is owed and its age, outstanding invoices linked to Invoices, the 12-month trend, customers, projects linked to Projects, cost centres, budget against actual, recurring costs, rates used and notes), **Bills** (search, filter, paging, Export CSV, new bill, the bill drawer with payments by hand or with an allocation, edit, void), **Vendors**, **Allocations** (classify each row), **Budgets**, **Journal** (adjustments, void with a reason), **Intercompany** and **Settings** (settings, category map, cost centres, exchange rates). Every section handles loading, empty with a next action, errors with Try again, a member's read-only view of owner actions, and paging; English and Arabic, right to left. Tables that do not fit scroll sideways inside a focusable region.

## Storage, privacy and deletion

Migration `0137_business_books` (expand only, after `0136_purchases`): `business_book_settings`, `business_book_cost_centres`, `business_book_vendors`, `business_book_bills`, `business_book_bill_payments`, `business_book_allocation_tags`, `business_book_budgets`, `business_book_rates`, `business_book_adjustments`, `business_book_intercompany`. Every table has forced RLS with `tenant_isolation_<table>` and `lifecycle_purge_<table>` (the 0079 grants), and is listed in `tenant_lifecycle_tables.py` for the organisation export, workspace deletion and purge. References between book tables are composite foreign keys on `(tenant_id, workspace_id, id)`. Checks keep totals equal to net plus tax, positive amounts, valid currencies, a void naming who voided it, one allocation per live bill payment; the adjustments trigger keeps the trail.

These are **business records** of the workspace, like invoices: they hold ids of who recorded them and no personal data (vendors carry no contact details). They are not part of a person's privacy export, and "Delete my data" leaves them with the business; deleting the business workspace or the organisation removes them. The allocation rows themselves follow [Finance links](finance-links.md#storage-privacy-and-deletion).

## Tests

`services/api/tests/test_business_books.py` (permissions and the setup step, vendors and cost centres, idempotent and validated bills with tax apart, payments by hand and voids, the acceptance case: an Apple Pay plus SMS fuel payment linked to a vehicle, a purchase and allocated to the business counts once in the P&L and once when it pays a bill, with the journal unchanged; undoing the payment in Finance takes it out of the books; invoices as revenue on both bases with credit notes and tax; a loan is cash, not profit; rates dated on or before and their source listed; adjustments spread, depreciate, reverse and only void; intercompany mirrored and refused without ownership of both; budgets, classifications, cost centres, projects and recurring costs; other businesses and tenants read nothing), `tests/test_business_books_report.py` (the pure figures: fiscal years, exact spreads, realised exchange differences, stale rates, cash-basis tax split and capital spend, prepayments), `tests/test_postgres_business_books.py` (forced RLS and purge policies, every kind written and read under RLS with nothing visible to other scopes, an allocation paying one bill when two requests race with no journal change, the frozen adjustments trigger, intercompany rows in both workspaces), web `test/businessBooks.test.ts` and `e2e/business-books.spec.ts` (the gate at five sizes in English and Arabic with axe), plus the Books entries in `e2e/support/gatePages.ts`.

## Not built here

- Flutter screens: the books are web only in this release.
- A business bank ledger visible to members (owner decision 1: allocations, not a workspace-visible ledger); payments from the business's own account are recorded by hand on the bill. Business card liabilities are not modelled.
- Consolidation across related businesses and intercompany elimination.
- Automatic exchange rates: rates are recorded by an owner with their source (Finance's ECB connector stays personal).
- Tax returns or filings of any jurisdiction, and a balance sheet; the figures are management figures.
- Recurring bills created by a schedule (a bill is marked recurring for the run-rate only).
