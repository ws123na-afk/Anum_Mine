# Finance and Business Books audit (9 November 2026)

Phase 1 of the chartered-accountant-level Finance upgrade: what `main` (9c41f1c4, after #414, #416, #417 and #418) already does, what is defective, missing or duplicated. Read from the code, migrations, tests and docs; nothing was run for this audit. Each finding names its evidence so it can be checked. Classes: **1** implemented and tested, **2** implemented but incomplete, **3** implemented but defective, **4** missing, **5** duplicated, **6** in an open PR (none: there were no open Finance PRs on 9 November).

The fixes are delivered as functional-area pull requests in this order: transaction correctness and statement import, reconciliation, accounting reports and period close, learning from corrections and the suggestion queue, business accounting, personal finance consolidation. Each PR updates the matching row here.

## Contents

- [Summary](#summary)
- [Accounting foundation and Business Books](#accounting-foundation-and-business-books)
- [Ingestion, matching and reconciliation](#ingestion-matching-and-reconciliation)
- [Classification, learning and automations](#classification-learning-and-automations)
- [Personal finance and the Finance screens](#personal-finance-and-the-finance-screens)
- [Duplicated logic](#duplicated-logic)
- [What needs no replacement](#what-needs-no-replacement)
- [Never autonomous](#never-autonomous)

## Summary

- Personal Finance keeps a real double-entry journal: per-currency balance enforced by a deferred database trigger (`migrations/versions/0131_finance_sms_ledger.py`, `finance_postings_balanced`), postings immutable, forced RLS. Every channel (statement, CSV, SMS, Apple Pay, manual) ends at one canonical journal entry.
- Business Books keep **no journal**: every report is derived from invoices, bills, allocations and adjustments (`business/books_report.py`, `books_service.py:4-6`). There is no chart of accounts, general ledger, trial balance, balance sheet, opening balance, close or lock.
- Classification has **no transaction type**: an unrecognised credit counts as income and an unrecognised debit as spending (`finance/categories.py:106-108`, `flows.py:58-67`).
- The most likely reasons statement rows look missing are the Transactions page defaulting to the current month, rows held in review or attached to other records by amount alone, and rows the PDF reader drops without saying so (details below).
- There is no reconciliation workspace, no accountant review, no unified suggestion queue and no period close.

## Accounting foundation and Business Books

| Capability | Class | Evidence |
|---|---|---|
| Double-entry ledger | 1 personal, 4 business | Trigger `finance_postings_balanced` (0131:234-251), test `test_postgres_finance_sms.py:128`; business books write no journal (`books_service.py:4-6`) |
| Chart of accounts with codes | 2 | Personal prefix codes `asset:`, `liability:`, `income:`, `expense:`, `equity:`, `suspense:` (CHECK in 0131); no numbered chart; business uses section and category only (`books_models.py:22-30`) |
| General ledger, opening and closing balances | 4 | Only `opening_balance` on personal accounts |
| Trial balance and imbalance detection | 4 | Not found |
| Balance sheet | 4 | `docs/business-books.md` lists it as not built; `balances()` (`books_report.py:562`) lists AR, AP, owed to members, debt and intercompany only |
| Profit and loss | 1 | `books_report.py:255, 409`, `GET /books/report`, accrual and cash tests |
| Cash-flow statement | 2 | Direct method per currency (`books_report.py:500-551`); no opening or closing cash |
| Changes in equity | 2 | Period contributions and drawings only (`books_report.py:651`) |
| Journal inspection | 2 | Business Journal tab lists adjustments; postings cannot be inspected |
| Adjustments with approval and audit | 2 | Owner only, frozen by trigger except void (0137:336-358); no maker and checker; audit rows hold ids only |
| Period comparison | 2 | Month against previous month and a 12-month trend only |
| Year-end carry-forward, monthly and yearly close, locked periods | 4 | `closeChecklist` in `lib/ops.ts:157` is a checklist only |
| Accruals and prepayments | 1 | `books_report.py:330-337`, tests |
| Depreciation, fixed-asset register | 2 | Straight-line depreciation tested; no register, accumulated depreciation, net book value or disposal |
| Intercompany | 1 | Mirrored pairs (`api_books.py:939-980`), tests |
| Consolidation | 2 | Eliminations in `books_group.consolidate`; no group balance sheet or translation reserve |
| Policy notes | 4 | Disclaimers only |
| Cash and accrual basis | 1 | Setting and per-report override, tested |
| IAS 21 | 2 | Dated rates with source and maximum age; realised FX on accrual only (`books_report.py:357`); no unrealised revaluation |
| Personal and business separation | 1 | Forced RLS on book tables (0137:102-104), owner-only personal policies; tests |
| Customers, suppliers, AR, AP, partial settlement | 1 | Aged receivables and payables, settlement tests |
| Cost centres, business budgets | 1 | CRUD and `budget_vs_actual` with `comparable:false` for other currencies |
| Project profitability | 3 and 5 | False zeros (defect B1); contract profitability and books figures are two calculations |
| Tax and VAT | 2 | Output and input summary per currency; no jurisdiction rates or return |
| ZATCA e-invoicing | 4 | Roadmap mention only |
| Loan schedules | 2 | Loan adjustments only, no amortisation |
| Owner-paid expense and reimbursement | 1 | Reimbursable allocations, tested |
| Evidence attachments on bills, accountant role | 4 | Not built |

Confirmed defects:

- **B1** `books_report.py:687, 846` keeps the top 50 rows, then `GET /books/projects` fills `"0"` for the rest (`api_books.py:1064, 1076`); `_by` drops unconvertible figures without a flag (`books_report.py:677-679`). The same applies to the customer and cost-centre breakdowns.
- **B2** The P&L CSV (`api_books.py:1019-1047`) omits the incomplete-data notes, `complete`, `truncated` and `allocations_in_review`.
- **B3** The trend chart adds FX differences to revenue (`books_report.py:709`).
- **B4** Over-reimbursement and over-repayment are accepted (`books_service.py:300-333`), so owed-to-members and debt can go negative.
- **B5** Loan interest is always treated as cash paid on its date (`books_report.py:329, 515`).
- **B6** Closed periods can be restated without a trace: settings (`api_books.py:196-216`), paid bill dates and categories (`api_books.py:523-525`); bills and intercompany rows have no freeze trigger.
- **B7** The personal balance trigger fires on posting insert and delete only (0131:250-251), so an entry with no postings is never checked.
- **B8** Draft-bill approval needs only `business:write`, including by its author (`api_books.py:532-548`).
- **B9** The consolidated `complete` flag ignores truncation (`books_group.py:203`).
- **B10** `gather` reads projects and accounts without the truncation check (`books_service.py:440-441`).

## Ingestion, matching and reconciliation

| Capability | Class | Evidence |
|---|---|---|
| Bank CSV | 2 | `csv_import.py:378`; presets are UAE banks only, Saudi banks use the generic header reader |
| PDF statements | 3 | `statement_pdf/engine.py`, 48 tests; row-drop defects I4 to I10 |
| Al Rajhi layout | 4 | Only the SMS sender (`sms_parser.py:87`, unverified) |
| Credit-card PDF | 3 | Amount-due check works (`engine.py:425-431`); card detection misfires (I4) |
| Bank SMS | 2 | 8 Saudi and 8 UAE banks, all unverified until the owner confirms one message |
| Apple Pay and Wallet, manual entry | 1 | `wallet.py`, `/wallet/ingest`; `api.py:562` |
| Read-only bank API | 4 | Only the ECB rates connector is built |
| Installment statements and Tamara email | 2 | SAR only |
| One canonical entry per event | 1 | `record_plain`, balanced postings trigger |
| Cross-source dedupe | 3 | Too aggressive (I1 to I3) |
| Statement self-check (opening plus rows equals closing, running balance) | 1 | `engine.py:440-444`, `pdf_api.py:470-485` |
| Statement closing against book balance | 4 | Only an SMS-observed balance is compared |
| Reconciliation workspace (bank and card) | 4 | Not built |
| Reversal pairing, pending versus posted, unrecognised fees, missing periods | 4 | Not built |
| Multi-currency (original amount, billed amount, no invented rates, Decimal, per-currency totals) | 1 | `csv_import.py:521-585`, `fx.py` |
| Realised and unrealised FX in personal Finance | 4 | Not built |

Confirmed defects:

- **I1** A same-amount record within 0 to 3 days is held as `possible_duplicate` even when the merchants disagree (`statement_match.py:305-306`, `api.py:976-979`).
- **I2** Generic words ("transfer", "payment", "شراء", "حوالة") count as a merchant match because the stop list has 15 words (`statement_match.py:284-288`, `wallet.py:39`).
- **I3** Occurrence numbering for identical rows counts ticked rows at import but all rows in the preview (`pdf_api.py:490` against `:261`), so a row imported later collides as `imported_before`.
- **I4** Any "credit card" text marks a current account as a card statement (`engine.py:253`, `meta.py:40`), which switches off the balance check that would reveal dropped rows.
- **I5** Import history has no held, attached or skipped counts (`models.py:147-161`); attached rows count as duplicates (`api.py:994`).
- **I6** A "tied" statement marks the account reconciled even when rows were held or skipped (`pdf_api.py:486-489`).
- **I7** Rows outside the period window are dropped silently; with no period line the window falls back to the statement date (`engine.py:402, 515-525`).
- **I8** A line with two dates and no amount is read as a period line (`engine.py:324-333`), which loses Gregorian plus Hijri rows.
- **I9** Pages with "terms and conditions", "example of" or "مثال" are skipped whole (`engine.py:187-191, 271-275`).
- **I10** Wrapped lines too far from their date are discarded (`engine.py:362-365`).
- **I11** The Transactions page defaults to the current month in UTC (`FinanceTransactionsPage.tsx:71-73, 84`), so an imported earlier statement looks empty.

## Classification, learning and automations

| Capability | Class | Evidence |
|---|---|---|
| Transaction type separate from category | 2 and 5 | No type on `Transaction` (`models.py:131-144`); SMS-only `sms_parser.Kind`; categories mix types (TRANSFER, INCOME, CASHBACK, REFUNDS) |
| Direction | 1 | Signed amount, `suspected_direction` hint |
| Subcategories | 4 | 23 flat categories |
| Merchant and aliases | 2 and 4 | SMS merchant only; no aliases |
| Personal or business per transaction | 2 | `business` link dimension and allocations |
| Duplicate suggestion | 1 | `statement_match` statuses |
| Confidence and explanation | 2 | SMS high or low, matches strong or weak; category has none |
| Refund linked to purchase | 2 | SMS only (`sms_service.py:333`) |
| Cashback separate from refund | 1 | `flows.py:63-66` |
| Card payment not double counted | 1 | `categories.py:95-105`, `flows.py:60`, tests |
| Loan principal and interest | 3 and 5 | Three treatments: SMS when stated (`ledger.py:204-214`), estimate (`obligations.py:146-152`), stale "no split" docstring (`commitments.py`); interest mapped to fees |
| Rules engine and owner rules | 1 and 2 | `categories.py`; owner rules are plain substrings with no scope, version, provenance or rollback (`models.py:96`, `api.py:206`) |
| Account-scoped, amount or reference rules, history reuse | 4 | Not built |
| Unified review queue and suggestion object | 4 | Four separate queues (SMS review, statement holds, Guardian review, link review) |
| Upcoming bills, subscriptions | 1 and 2 | `detect_recurring`, `/bills/suggestions`; reminders not built |
| Missing statement periods, exception notifications, reconciliation prompts | 4 | Not built |

Confirmed defects:

- **C1** Unrecognised credits count as income (`categories.py:106-107`, `flows.py:58-62`), locked in by the test case at `test_finance_correctness.py:129`.
- **C2** Unrecognised debits count as spending (`categories.py:108`, `flows.py:67`).
- **C3** The same payment is classified differently by source: an SMS transfer out is never spend (`ledger.py:164-165`) while the statement row becomes housing spend.
- **C4** Transfer pairing overwrites manual categories and stamps them `MANUAL` (`statement_ledger.py:268-275`, `api.py:998, 1043`).
- **C5** The register suggestion ignores the account kind (`register_api.py:362`).
- **C6** Owner rules match inside words and skip the sign guards (`categories.py:93`).
- **C7** A bare "INTEREST" credit lands in fees (`categories.py:40`).

## Personal finance and the Finance screens

| Capability | Class | Evidence |
|---|---|---|
| Net worth | 2 | Assets less liabilities (`summary.py:252-253`), but leaves out holdings, person loans and installment plans without saying so |
| Available cash | 3 | Three definitions; the Accounts page counts investments and sums floats (P1) |
| Card obligations, bills due, savings goals, subscriptions, health alerts, spending trends | 1 | `safe_to_spend.py`, `commitments.py`, goals CRUD, `detect_recurring`, Guardian alerts |
| Fixed costs, lent and borrowed, payoff planning, income | 5 | See [Duplicated logic](#duplicated-logic) |
| Salary profile (joining date, expected salary, pay day, advances, recurring deductions, detection across months) | 4 | Payslips record a month's figures only (`personal_api.py:446-520`); the advisor detects only `salary_found` |
| Scheduled obligations against paid transactions | 3 | Past debt minimums are assumed paid (P2) |
| Category budgets | 2 | Set only through the Guardian confirm flow |
| Emergency reserve | 2 | Indirect only |
| Month-end forecast | 2 | Web only (`financeStatements.ts:180`), ignores expected income |
| Reconciliation, Accountant review, Automations and suggestions, Finance settings pages | 4 | Not built (settings is a dialog) |
| English and Arabic | 1 | Paired catalogs, parity tests |

Confirmed defects:

- **P1** `FinanceAccountsPage.tsx:153-159` counts investment accounts as available and sums with `Number()`, cutting three-decimal currencies.
- **P2** `safe_to_spend.py:192-195` drops a past-due minimum as paid without checking the debt-payment links (`commitments_api.py:434`).
- **P3** Net worth leaves out holdings, person loans and installment plans with no note (`summary.py:252-253`).
- **P4** `FinanceInstallmentStatement.tsx:26` writes CSV amounts with two decimals for every currency.

## Duplicated logic

- Monthly commitments are computed four times: `personal.fixed_commitments`, `safe_to_spend.compute`, `advisor._coming` and `advisor._runway`.
- Two debt payoff engines: `lib/financeDebtPlan.ts` and `planner.py`.
- Lent and borrowed sits beside Debts; borrowed money never reaches payoff, Safe to spend or fixed costs.
- Three income figures: `planning_service.income_monthly`, the advisor's month income, and its salary detection; payslips feed none of them.
- Four exact-duplicate checks: `csv_import.dedupe_hash`, `sms_service.fingerprint`, `register_api._duplicates`, `statement_match`; own-transfer pairing three times.
- Helpers: `monthly_equivalent` twice (disagreeing on one-off bills), liquid balance twice, debt minimum written out five times, month helpers in four files.
- Sidebar: Connections and SMS capture open the same page; a separate Personal money entry.

## What needs no replacement

The personal double-entry journal and its trigger, the forced RLS on every Finance and Business Books table, the canonical-entry pipeline with source fingerprints, the PDF statement engine's self-checks, multi-currency handling with Decimal and no invented rates, the Guardian proposal and confirm pattern (the template for versioned rules), the links model, AR, AP, accruals, prepayments, intercompany, cash and accrual basis, and the English and Arabic catalogs. The upgrade builds on these.

## Never autonomous

Moving money, changing an amount, currency or date, overwriting a manual category, creating or loosening rules, applying rules retroactively, confirming matches, marking a reconciliation, posting adjustments, deleting records, changing bank mappings and opening or closing periods all need the owner.
