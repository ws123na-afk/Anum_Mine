# Finance: your own ledger, safe-to-spend, Guardian rules, read-only connectors, paper trading and the execution gateway

Finance is ANUM's native Finance domain, stages 2 to 9: one person's own ledger and a read-only "Financial Twin" built from the bank and card statements they import (stage 2), what is safe to spend for the rest of the month (stage 3), the person's own spending and risk rules with the Guardian policy kernel that every finance action must pass (stage 4), read-only connectors that bring outside data in (stage 5), SIMULATED paper and shadow trading against prices the person recorded (stage 6), the execution gateway, the one governed path any real action would have to take (stage 7), and the frameworks for limited live (stage 8) and wider live use (stage 9), both **shipped off**. It answers "what did I spend this month", "which bills are due", "what do I owe" and "how much can I still spend", and lets the person practise orders with simulated money. Since November 2026 one live path exists, for the owner's own Interactive Brokers account only: owner-approved limit orders for US-listed ETFs, paper first, behind every lock in [Live trading with Interactive Brokers](finance-live-trading.md). Everything else on this page that moves money stays refused.

Code: `services/api/anum_api/finance/` (`safe_to_spend.py`, `fx.py`, `guardian.py`, `guardian_review.py`, `policy.py`, `connectors.py`, `sync.py`, `plan_api.py` for stages 3 to 5; `trading.py`, `execution.py`, `gateway.py`, `live.py`, `wider.py`, `trading_api.py`, `wider_api.py` for stages 6 to 9), `services/api/anum_api/db/finance_repository.py`, migrations `0056_finance_ledger`, `0057_finance_plan_guard_conn`, `0058_finance_paper_exec`, `0059_finance_wider_live` and `0140_finance_guardian_reviews`, `packages/contracts/src/finance.ts`, web `apps/web/src/FinanceView.tsx`, `FinancePlan.tsx`, `FinanceGuardian.tsx`, `FinanceTrading.tsx` and `FinanceWider.tsx`, Flutter `apps/mobile/lib/features/finance/`.

Bank and card SMS forwarded from an iPhone, the review queue and the double-entry journal with its ledger dashboard are in [iPhone SMS banking and the double-entry ledger](finance-sms.md) (off by default). A transaction an SMS recorded is part of a journal entry: the Transactions tab cannot change its amount, date or category (correct it under iPhone SMS, Review), and deleting it reverses the whole entry.

## Hard limits

These are rules of the design, not settings:

- **No real money moves outside the IBKR path.** The only order call is the IBKR executor ([live trading](finance-live-trading.md)): limit orders for allowed US ETFs, each approved by the owner, inside hard limits, with every live gate. There is no payment, transfer or withdrawal call anywhere. The Guardian kernel's `gate` denies every real money-moving action whatever the rules say (`execution_not_available`); only SIMULATED paper and shadow orders run, and they touch no account.
- **Live is off by default.** Only the IBKR executor can lift it, and only with `ANUM_FINANCE_LIVE_BROKER=ibkr`, the paper proof, the owner's typed code and a cooling-off ([locks](finance-live-trading.md#modes-and-the-live-locks)). Otherwise: No live executor and no write-scoped connector exist. `ANUM_FINANCE_LIVE_ENABLED` and `ANUM_FINANCE_WIDER_LIVE_ENABLED` are refused at startup (and by the Helm chart), the gateway refuses every live request ("Live execution is not enabled"), and the database refuses a live request in any approved or executed state.
- **No keys to the AI.** The assistant reads the ledger only through read-only tools (and may propose a stricter rule). A connector's credentials are encrypted at rest and never reach a prompt, a tool result, agent memory, a log, an audit record, the browser or an export.
- **Read only connectors.** Every connector scope ends in `:read`, enforced by a database check constraint; no adapter has a write, order or transfer call. Third-party data is untrusted input.
- **The person's rules are the person's.** An agent or plugin can read the rules and propose a stricter change; it can never confirm a change, and a proposal that loosens anything is refused for agents (also by a database check). Every change is confirmed by the person with the payload hash shown.
- **Every request is the person's to approve.** An assistant may propose a SIMULATED order or a wider-live step; it can never approve, execute, turn live on, set live limits, change a kill switch or grant itself more authority. Approving needs the person's own session, the payload hash they were shown, once, before it expires.
- **No profit promises.** ANUM reports what the statements say. It gives no investment advice and makes no forecast of returns. Simulated results are labelled SIMULATED everywhere, with the fee and slippage assumptions, and say nothing certain about what a real order would earn or lose.
- **No market prices.** No market-price source is built (it needs a licensed provider). Holdings are quantities and costs the person types; paper orders fill only at prices the person records (`finance_prices`) or, for currencies, at the recorded exchange rates. The only fetched data is exchange rates from the European Central Bank.

A request to pay or transfer ("pay the DEWA bill", "transfer 500 to the landlord") is not a finance question: the finance planner returns nothing for it and the request goes to the governed `anum.external-action` skill, which waits for an approval like every other external action.

## Defaults (assumptions until the owner answers the setup questions)

| Question | Default in Stage 2 | Where to change it |
|---|---|---|
| Whose data | The signed-in person's own finances, private to them, even from workspace owners | Row policy in `0056_finance_ledger`; `owner_user_id` on every table |
| Region | UAE: base currency AED, time zone Asia/Dubai | Per person and workspace under Finance, Settings (`PUT /api/v1/finance/settings`); defaults in `finance/models.py` |
| Input | CSV and PDF statement import ([PDF bank statements](finance-statements.md)); read-only connectors (ECB exchange rates built, providers listed as owner actions) | `finance/csv_import.py`, `finance/connectors.py` |
| Categories | Built-in keyword rules for common UAE merchants, plus the person's own rules | `finance/categories.py`, Finance, Settings |

## Money rules

- Amounts are exact: `Decimal` in Python, `numeric(20, 4)` in PostgreSQL, each next to a three-letter ISO 4217 `currency`. Holdings' quantities are `numeric(28, 8)`.
- **The one rounding rule:** an amount is rounded once, when it enters ANUM (a CSV cell, a form field), to its currency's minor unit with banker's rounding (`ROUND_HALF_EVEN`): 2 decimals for AED and most currencies, 3 for BHD, IQD, JOD, KWD, LYD, OMR and TND, 0 for JPY, KRW and a few others. Sums of rounded amounts are exact and never rounded again (`finance/money.py`).
- Different currencies are never added together. Every total (balances, spend, income, net, bills due, debts) is a list of `{currency, amount}`.
- On the wire an amount is a decimal string (`"1234.50"`), so no client turns it into a binary float. Clients format it for display only.
- Signs: a transaction's amount is positive for money in and negative for money out. A bill, debt balance, goal and cost are positive.
- **FX, honestly.** Amounts are never converted silently. A converted figure exists only in safe-to-spend's optional total in the base currency, only when every rate needed is recorded and recent, and each conversion shows its rate, date and source ([Exchange rates](#exchange-rates)). There is still no cross-currency "net worth".

## Data model

| Table | What it holds |
|---|---|
| `finance_settings` | Base currency, time zone and the person's category rules (one row per person and workspace) |
| `finance_accounts` | Name, kind (`bank`, `card`, `cash`, `loan`, `investment`), currency, institution, at most the **last four digits** (a check constraint refuses more), opening balance (a card or loan owes money as a **negative** balance: owes 3,000 is -3000; the API refuses a positive one unless `credit_balance` says the account was overpaid), archived |
| `finance_transactions` | Account, posted date, signed amount, currency, description (from the statement), category and what set it (`rule`, `owner_rule`, `manual`, `match`, `system`, `none`), notes, the import it came from, a dedupe hash unique per account, and the [transaction type](#transaction-types) with its source, confidence and reason code plus the purchase a refund gives back (`transaction_type`, `type_source`, `type_confidence`, `type_reason`, `related_transaction_id`; migration `0148_finance_txn_types`, nullable, checked against their lists) |
| `finance_imports` | One row per confirmed import: file name, layout, date format, counts (rows, imported, duplicates, unread) and the file's SHA-256 (not the file) |
| `finance_bills` | Name, amount, currency, cadence (`weekly`, `monthly`, `quarterly`, `yearly`, `once`), next due date, category, optional account, source (`manual` or `detected`), active |
| `finance_debts` | Name, kind (`loan`, `card`, `mortgage`, `other`), balance owed, APR in percent, minimum payment, due day |
| `finance_goals` | Name, target, saved so far, target date |
| `finance_advisor_reports`, `finance_advisor_settings` | The weekly [Money advisor](#money-advisor) notes (one per advice week, the note as JSON, when its notice went out) and the person's switches (on or off, weekday) |
| `finance_holdings` | Investment account, symbol, name, quantity and total cost. No prices |

| `finance_plans` | Safe-to-spend settings: a buffer per currency, whether to show one total in the base currency, the oldest rate to use (days) |
| `finance_guardian_versions` | Every confirmed version of the person's rules (`rules` JSON, version unique per person, the change it came from, who confirmed it) |
| `finance_guardian_changes` | Proposed rules waiting for confirmation: base version, rules, who proposed it (`person` or `agent`), reason, payload hash, what it loosens, status (`pending`, `confirmed`, `rejected`, `expired`, `superseded`), expiry (24 hours) |
| `finance_connections` | Read-only connections: provider, kind, label, status, read-only scopes (a check refuses any scope not ending in `:read`), Fernet-encrypted credentials (none once disconnected, also a check), next and last sync, last error, counts |
| `finance_fx_rates` | Exchange rates: base, quote, rate (`numeric(24, 10)`), date, source, source address, the connection that fetched it (rates go with the connection); unique per person, pair, date and source |
| `finance_prices` | Stage 6: prices the person recorded: symbol, currency, price (`numeric(24, 10)`), date, source (`manual`); unique per person, symbol, currency, date and source |
| `finance_paper_portfolios` | Stage 6: name, mode (`paper` or `shadow`), starting cash per currency, fee (basis points and a minimum), slippage (basis points), the oldest price to use (days), the current run (`epoch`) |
| `finance_paper_orders` | Stage 6: SIMULATED orders: portfolio, run, the execution request it came from (unique: one order per request), instrument, side, type, quantity (`numeric(28, 8)`), limit, status (`waiting`, `filled`, `refused`, `cancelled`) with the reason, the recorded price used, fill price after slippage, value, fee, slippage, the Guardian outcome and codes |
| `finance_exec_intents` | Stage 7: every request through the gateway: mode (`paper`, `shadow`, `live`, `settings`), kind, the exact request, who asked (`person` or `agent`), payload hash, idempotency key (unique per person), status, Guardian decision, estimate, refusal, expiry, who approved it and when, result. Checks: the approver is the owner; nothing runs unapproved; an agent only asks for paper or shadow trades; **a live request is never approved, executing or executed** |
| `finance_exec_settings` | Stages 7 to 9: the person's kill switch, live limits, opt-in and version, and the wider-live tier, action types and freeze (`wider`) |
| `finance_exec_global` | Stage 7: the organisation's kill switch, one row per tenant (tenant RLS; not personal finance) |
| `finance_live_widenings` | Stage 9: proposals to step up one tier: base tier, tier (always base + 1), limits, action types, who proposed it, payload hash, status, expiry, who decided, when it takes effect |
| `finance_live_incidents` | Stage 9: kill switch, failed execution or repeated denials, with the tier before and after the step down (never higher), and when it was reviewed |

`finance_goals.monthly_contribution` (stage 3, nullable) is what the person sets aside for a goal each month.

Deleting an account deletes its transactions, imports and holdings; bills, debts and goals linked to it stay without the link. Undoing an import deletes the transactions it added.

## Privacy

- Every finance table (migrations `0056` to `0059`) except the organisation's kill switch has a foreign key to its workspace, **forced** RLS, the standard `tenant_isolation_<table>` policy and a **restrictive** `owner_only_<table>` policy: `owner_user_id = nullif(current_setting('anum.user_id', true), '')`. Restrictive policies are AND-ed with the permissive one, so a row is visible and writable only to its owner inside its own tenant and workspace. With no `anum.user_id` set nothing is visible. No role is granted anything new and no job reads finance across tenants or people ([Multi-tenancy](multi-tenancy.md)).
- The store opens its session with `open_scoped_store(..., user_scoped=True)` and repeats the tenant, workspace and owner in every query.
- Audit records for finance changes carry the action and the record id only (plus rule codes, version numbers, provider names and counts): never an amount, a rule's limit, a description, an account name or number, or a credential.
- The background sync and key rotation find connections through `anum_maintenance`, which reads only id, scope, owner, `enabled`, `status` and `next_sync_at` of due connections (or of connections holding credentials, for rotation). For `finance_connections` the restrictive owner policy also admits a session whose `current_user` is `anum_maintenance` (a login that merely inherits the role never is); all work then runs as the application role in the owner's own scope.
- `finance_exec_global` holds only whether the organisation's kill switch is on, who changed it and when, under forced RLS on `tenant_id`.
- "Download my data" exports the whole ledger (section `finance`, including the safe-to-spend settings, rule versions and changes, connections and rates, recorded prices, paper and shadow portfolios and orders, execution requests, execution settings, wider-live proposals and incidents) and "Delete my data" deletes it (count `finance_records`) ([Privacy](privacy.md)). A connection's credentials are never exported, not even encrypted: only `has_credentials`.
- Statements are not kept: only the parsed rows and the file's hash.

## CSV import

PDF statements (text, scanned or password protected) are read and reviewed on the same surface and recorded through the same path, below: [PDF bank statements](finance-statements.md).

`POST /api/v1/finance/imports/preview` reads a file and shows what an import would do; nothing is saved. `POST /api/v1/finance/imports` sends the same file and choices again and writes the new rows in one unit of work. Both take `{account_id, filename, content, preset?, date_format?, mapping?}`, where `content` is the file's text.

- **Layouts.** A generic layout (a date, a description, and either one signed amount column or a debit and a credit column; English or Arabic column names) and presets for common UAE bank exports: Emirates NBD, First Abu Dhabi Bank, ADCB, Mashreq and Dubai Islamic Bank. A preset is a set of column names written from typical exports; they are not certified against every bank's current file. When a file uses other names the preview says so and the person picks the columns (`mapping`).
- **Preamble lines.** The header row is found within the first 20 rows, so account details above the table do not break the import.
- **Dates.** The format is detected from every date in the file (rows that are no date at all, such as a footer, are ignored for detection and reported as unread). When the dates fit both day first and month first, ANUM takes day first (the UAE convention) and flags it; `date_format` overrides it.
- **Amounts.** `1,234.50`, `1.234,50`, `-12.00`, `(12.00)`, `12.00 DR`, `5.00 CR`, `AED 9`, Arabic-Indic digits.
- **Limits.** 2 MB, 5,000 rows, 50 columns, 1,000 characters a cell, 500 characters a description. Larger files are refused with what to do ("export one month at a time").
- **Untrusted input.** The file is text only: never evaluated or opened by a spreadsheet program. Descriptions are stripped of control, format and direction-override characters. Nothing in a file can choose an account, a tool or a rule.
- **Duplicates.** Each row's hash covers the account, date, amount, currency, cleaned description and its occurrence number among identical rows in the same file. The hash is unique per account, so importing the same or an overlapping statement again adds only new rows; two identical purchases on one day in one file stay two rows. Beyond the hash, every row goes through the [statement matching engine](#statement-matching): rows that SMS, Apple Pay or the person already recorded are attached to that record instead of being added, and the preview groups rows by what the import will do.
- **Running balance and reference.** A `balance` column (the balance after the row) feeds the [balance check](#reconciliation); a `reference` column is the bank's reference, used by the matching engine. Both are detected from common names and can be mapped.
- **Balances.** `opening_balance` and `closing_balance` (optional, as printed on the statement) are checked against the rows.
- **Export.** `GET /api/v1/finance/transactions/export.csv` (same filters as the list, at most 10,000 rows). Text cells that start with `=`, `+`, `-`, `@`, `|`, `%`, a tab or a carriage return get a leading apostrophe so a spreadsheet does not run them as formulas.

## Statement matching

One engine reads every statement row against what the person's ledger already holds, for CSV and PDF alike (`finance/statement_match.py`, pure; `finance/statement_ledger.py` reads and writes the stores; `finance/statement_import.py` holds the views and the transfer routes). It runs when the preview is built and again, on the live ledger and under the owner's lock, when the rows are imported, so a preview that went stale cannot double count. It compares each row with the records on that account from any source: an earlier statement, an iPhone SMS, Apple Pay, an entry the person made (including installment payments once they are in the ledger). Reversal entries are not records of their own.

Every row gets one status, with a reason code and the record it matched:

| Status | When | What the import does by default |
|---|---|---|
| `exact_duplicate` | The same statement row was imported or confirmed before (same dedupe hash, including rows that only confirmed an SMS), is waiting in Review from an earlier import, or another statement already recorded the same day, amount and merchant (`same_statement_row`) | Left out. A `same_statement_row` duplicate may be ticked to import anyway; a row seen before by its hash never can |
| `matched_existing` | An SMS, Apple Pay or hand-entered record of the same purchase | No new transaction: the row is attached to the record as a `statement` source and the record is marked reconciled. If the bank billed a different amount for the same foreign purchase, the record is corrected to the billed amount (unless unticked) |
| `possible_duplicate` | Same amount and dates but the merchants say nothing or disagree, or two different records fit | Held in Finance, iPhone SMS, Review (`ambiguous_match`). Ticked: imported as a separate payment. Skipped: left out |
| `new` | Nothing recorded fits | Imported. Unticked: left out |

### How matching works

Stages run strongest first and stop at the first one that gives a unique, one to one answer; anything ambiguous goes to the person.

| Stage | Rule | Window |
|---|---|---|
| 0. Same row | Dedupe hash (account, date, amount, currency, cleaned description, occurrence) already in the ledger or in an attached or waiting statement row | Same date |
| 1. Identifier | The bank's reference on the row agrees with the record's; a conflicting reference rules the record out | Statement posts 0 to 3 days after the record |
| 2. Amount and merchant | Same direction, amount and currency, and the merchant words agree | 0 to 3 days |
| 2b. Foreign amount | The same original foreign amount and currency (the row's text or columns against the SMS reading), the billed amounts within 10 % of each other, merchants not disagreeing | 0 to 3 days |
| 3. Weak | Same amount and window, nothing else agrees | Never attached: `possible_duplicate` |

- **Descriptors are normalized first:** card numbers, masked digits, dates and times, `POS`/`VIS`/`DEB`/`MADA` style prefixes and common city and country suffixes are removed before words are compared, and a word with digits in it (a reference such as `88AB12`) is not a merchant word.
- **One to one.** A record absorbs at most one statement row. Rows are taken in date then line order, candidates in a fixed order (fewest days apart, most words shared, oldest), so the answer is the same every time.
- **Two identical purchases on one day stay two rows.** Two records nobody could tell apart (same day, amount and merchant) are interchangeable, so two such rows match two such records; with one record, the second row is `new`. Two records that differ (other days) make the row `possible_duplicate`. A PDF row's printed time can pick between them when exactly one is within 30 minutes.
- **No amount tolerance** for same-currency matches: amounts are equal or they are not. The only tolerance is for the same foreign purchase billed differently, and that difference is shown and corrected, never absorbed silently.
- **Corrections go through the audited path.** A hand-entered or statement record is reposted in its canonical chain (as `PATCH /transactions` does); an SMS or Apple Pay record is reversed and posted again from its message with the original amount and the billed amount (as Finance, iPhone SMS, Review does). The journal keeps both entries; the import's audit row counts `corrected`.
- **Not built:** one statement row matched to several SMS rows (one to many). Banks rarely split one card purchase into several SMS; if it happens the row is `new` or `possible_duplicate` and the person decides.

The preview answers, for every row, `match` (`status`, `code`, `reason` in English, `record`, `candidates`, `lag_days`, `amount_difference`, `bill`, `transfer`, `default_import`) and `fx`, plus `counts` per status. The import takes the person's choices as `decisions: [{line, action: "import" | "skip", bill: false, transfer: true | false, correct: false}]` (CSV, by line) or the same fields on each PDF row, and answers `reconciled`, `held_for_review`, `corrected`, `skipped`, `bills_paid`, `bills_review` and `transfers` next to the counts it always had.

### FX details from the row text

`finance/fx_text.py` is the one reader of money in words, shared with the SMS parser (it holds the currency words, the amount pattern and the "second amount in another currency is the billed one" rule). For a statement row it reads, from the description and any mapped columns (columns win): the original amount and currency (`USD 20.00`, `20.00 USD`, `20.00 دولار`), the rate (`@ 3.84`, `FX rate 3.8400`, `rate: 3.84`, `سعر الصرف 3.84`), a markup (`markup 2.5%`, `هامش 2.5%`) and a fee (`intl txn fee SAR 1.20`, `رسوم 1.20 ر.س`). A fee or markup on a row with no foreign amount or rate is not an FX detail: a row that is only a fee is its own `fees` expense.

- The posted ledger amount stays the bank-billed amount in the account's currency. The original amount, rate, fee and markup go in the transaction's notes (as before) and in the preview.
- **A rate is never invented.** It is printed (`printed`), from a mapped column (`column`) or worked out from two printed amounts (`implied`, labelled "computed from the two amounts").
- The preview checks billed against original x rate, plus the printed fee or markup when that comes closer, and shows the difference. Within two minor units of the account's currency (0.02 SAR, 0.002 KWD) it ties. When the rate is printed it also shows the markup the billed amount implies, labelled as computed.
- A fee printed inside the purchase row is informational; a separate fee row is its own `fees` expense (unchanged).

### Bills

A row that pays one of the person's saved, active bills is recorded as that due date's payment through the same path the Bills page uses (`links.create`, dimension `bill`, docs: [Bills and budgets](#bills-and-budgets)); the due date's status is then `paid`. A due date absorbs at most one row, and a due date already matched is not offered.

| Strength | Rule | On import |
|---|---|---|
| Strong | The bill's name shares a word with the row, the amount is within 10 % of the bill's expected amount, and the row is from 7 days before to 10 days after the due date | Recorded as paid (untick to skip) |
| Weak | The name agrees but the amount or the date does not (within 30 days) | A link waiting for review (`needs_review`, reason `weak_match`): the due date shows `review`, never `paid`, until the person confirms or removes it under Bills |

Without the bill's name in the row there is no bill match at all, whatever the amount.

### Own transfers

Money out of one of the person's accounts and the same money into another of them is one transfer, not an expense and an income (for example 1000 leaving the salary account and 1000 arriving on the card ending 5377).

| Rule | Detail |
|---|---|
| Accounts | Two different accounts of the same person in the same workspace; never two rows on one account |
| Amount | Equal and opposite; in different currencies, one side must state the other side's amount (an original amount) |
| Date | The money in arrives 0 to 5 days after the money out |
| Strong | Plus a transfer or card payment word in either text (`transfer`, `TRF`, `card payment`, `payment received`, `تحويل`, `سداد`), or the other account's last four digits or its own name in the text |
| Weak | Amount and date only: listed under "Needs your decision", paired only when ticked |

The pairing runs against rows already in the ledger too (the other side may have been imported earlier or come from an SMS), one to one, and skips records already paired. On import both sides become category `transfer` (out of spend and income in every summary, as before) where they are hand-entered or statement records; an SMS or Apple Pay side keeps its reading (an SMS transfer is already a transfer) and is corrected under Review if it is not. The pair is kept as a finance link in the new `transfer` dimension: the money-out side's canonical transaction links to the money-in side's (`target_type: finance_transfer`), once (`single_payment`), checked by `commitments.vet_transfer` (money out, two different accounts). Removing the link (`DELETE` on the links API) unpairs it; the categories stay as they are.

**Find transfers** pairs rows that are already in the ledger: `GET /api/v1/finance/transfers/candidates?start&end` (a preview, at most 400 days, nothing changes) and `POST /api/v1/finance/transfers {start, end, pairs: [{out_record_id, into_record_id}]}` (only the ticked pairs; each is checked again and refused with `not_available` or `not_a_transfer` if it changed). Both need `finance:manage` and read only the caller's own ledger. Finance, Import shows it under "Transfers between your accounts"; nothing pairs on its own.

### Reconciliation

The preview checks the statement against its own balances: the opening balance plus every row (new, already recorded and matched alike) against the closing balance. The balances come from the person (opening and closing fields, as printed on the statement), from a mapped running balance column (CSV: `Balance`, `Running balance`, `الرصيد`; a newest-first export is put in date order first) or from the PDF reader. The answer is `tied`, `differs` (with the difference and the rows after which the running balance breaks) or `unknown` (nothing to check against). It is stored on the import (`finance_imports.reconciliation`, migration `0145_finance_statement_match`) and `GET /accounts/overview` reads it: a tied statement counts as the account's last reconciliation (`last_reconciled_at`), the newest one is `last_statement`, and a statement that does not tie adds `statement_differs` to what needs attention. Finance, Accounts shows "Last statement ties" or the difference on the account's card.

### Fixed in this pass

- Re-importing a statement whose row only confirmed an SMS added it again (the confirming row had no transaction of its own, so its hash was never found). Such rows are now `exact_duplicate` (`reconciled_before`).
- A reference such as `88AB12` counted as a merchant word and made merchants disagree.
- A PDF row billed in another currency showed English-only, SAR-only help text; it is now in the message catalogs and names the account's currency.
- A preview showed only the first 20 rows; it now answers every row, grouped and paged in the web.

## Categories

Rule-based only, no AI and no network (`finance/categories.py`): the person's own rules first (a phrase matched as a whole word or phrase, so "gym" never matches "GYMNASTICS"; an income, cashback or refunds rule never files money out), then built-in keyword rules for common UAE and Saudi merchants and statement wording (Carrefour, Lulu, Spinneys, Panda: groceries; DEWA, SEWA, ADDC: utilities; Etisalat, du: phone and internet; Salik, RTA, Careem: transport; ENOC, ADNOC: fuel; salary and WPS: income; interest; transfers; ATM; fees), matched as whole words. **Anything else is uncategorized**: category `other`, [type](#transaction-types) `uncategorized`, held for review and counted as neither income nor spend (the owner's rule of November 2026: a credit is not automatically income, a debit is not automatically spending). A category or type picked by hand is never changed by a rule or a match.

Order matters, and these win over the generic words:

- **Card payments** ("credit card payment", "CC payment", "card repayment", "autopay", "payment received, thank you", "سداد بطاقة الائتمان", "تسديد البطاقة") are type `card_payment`, category `transfer`.
- **Money in on a card or loan account** is the payment from the person's own bank: a card payment, unless a rule files it as cashback, a refund, interest or a merchant's credit (a merchant's credit on a card, "CARREFOUR" +45, is a refund in groceries).
- **Salary and WPS** win over "transfer" ("WPS SALARY TRANSFER" is income), and a **specific spending word** (rent, room rent, landlord, school fees, tuition) wins over "transfer" (rent paid by transfer is housing or room rent spending).
- **Interest**: money in with interest or savings-profit words ("interest earned", "INTEREST", "profit credit") is income under category `interest`; money out with them ("LATE PAYMENT INTEREST", "finance charge") is type `interest` (spending) under category `interest`, never a bank fee. Fees ("annual fee", "late fee") stay `fees`, type `bank_fee`.
- **Transfers**: words that name the person's own accounts ("own account", "between your accounts", "إلى حسابك") are an `internal_transfer`. Generic transfer words ("transfer", "TRF", "IPP", "AANI", "remittance", "تحويل", "حوالة") on **money out** are a transfer to someone else: spending (type `expense`, category `other`, reason `transfer_out_third_party`) until a [match](#own-transfers) pairs it with an own account. On **money in** they are `uncategorized` (`transfer_in_unknown`): money from someone else is not income until the person says what it was.

## Transaction types

Every transaction has a **type** (what it is) beside its **category** (what it was for). The type decides how it counts (`finance/txn_types.py`, `flows.flow_for_type`); the category is what spending by category groups by. The API returns `transaction_type`, `type_source` (`rule`, `owner`, `system`, `sms`, `match`), `type_confidence` (0 to 1) and `type_reason` (a short machine code such as `no_rule_matched`, `transfer_out_third_party`, `paired_own_transfer`, `refund_matched`) on every transaction and register row.

| Type | Counts as |
|---|---|
| `income` | Income (a negative row, a salary reversal, lowers it). Interest received is `income` with category `interest` |
| `expense`, `bank_fee`, `interest` (charged), `tax` | Spending in the row's category |
| `refund`, `reversal` | Lowers the spending of its category when that is a spending category; under `refunds` (no purchase found) its own line; a reversal filed as income lowers income |
| `cashback` | Its own line |
| `internal_transfer`, `card_payment`, `loan_received`, `loan_principal`, `investment_contribution`, `investment_withdrawal`, `owner_contribution`, `owner_drawing`, `reimbursement`, `receivable_collection`, `payable_settlement`, `fx_exchange` | Neither income nor spending: money the person already has or owes moves |
| `uncategorized` | Neither, held for review: the register shows it with status `uncategorized` and its reason, and the summary and ledger dashboard say "N uncategorized, not counted" (`uncategorized_count`, with the amounts in and out per currency) instead of a silent zero |

Where a type comes from:

- **Rules** (`rule`): the [category rules](#categories) above, for statements, CSV and PDF imports, hand-entered rows without a category and synced rows. Built-in matches carry 0.8 to 0.9, the generic transfer reading 0.6 and an unrecognised row 0.
- **The owner** (`owner`, confidence 1): a category or a type chosen on Transactions (`PATCH /transactions/{id}` with `category` or `transaction_type`; a chosen type with no category files the row under that type's category, for example `income` under income). The owner's choice is never changed by a rule, a transfer pair or a refund match (audit C4).
- **The message** (`sms`): an iPhone SMS or Apple Pay kind ([iPhone SMS kinds](finance-sms.md#kinds)): salary is income; a purchase or a transfer to someone else is spending filed by the merchant's words (rent is housing, as from a statement); an own transfer, card repayment, loan disbursement and loan repayment are transfers (`internal_transfer`, `card_payment`, `loan_received`, `loan_principal`) and the loan interest the message states is `interest`; a fee is `bank_fee`; a refund `refund` (or `cashback`); a deposit or a transfer received from someone is `uncategorized` until the person files it.
- **A match** (`match`): a strong own-transfer pair at import files both plain sides as `internal_transfer` (or `card_payment`) with category `transfer` and source `match`; a pair the person ticks is their own choice (`manual`, `owner`). A refund that finds its purchase (below) is `match` too.

**Rows stored before types existed** (no stored type) are read with the type their category, sign and account kind stand for (`txn_types.derive`, reason `legacy_category`), the same answer for every reader; it is stored the next time the row is saved. This is the backfill: a migration cannot update the forced-RLS table without a tenant context, and the rule is deterministic. Only the two cases the November 2026 audit found wrong change: an `income` row no rule matched (`category_source: none`) and an `other` row no rule matched are `uncategorized`.

Loans have one policy: a repayment moves the whole payment from the bank to the loan (`loan_principal` on both sides, a transfer), and the interest is charged on the loan as its own row (type `interest`, category `interest`), so only the interest is spending; a payment whose split the message or statement does not state stays one `loan_principal` transfer until the person enters the interest. The debt estimate (`obligations.py`) takes each matched payment's interest out by the debt's rate.

### Refunds and purchases

A statement, CSV, PDF or hand-entered refund with no purchase yet (category `refunds`) looks for the person's own purchase it gives back (`finance/refunds.py`): money out in the same currency within 120 days before it whose merchant words agree (`statement_match.merchant_words`, without the words "refund", "reversal" and the like). One purchase of the same amount is a **match**: the refund is filed under the purchase's category, so it lowers that category's spending, and `related_transaction_id` names the purchase (`refund_matched`, source `match`). Anything less certain (a partial refund, two purchases that fit) is only a **suggestion**: the refund stays on its own line and `related_transaction_id` names the likeliest purchase with a low confidence (`refund_suggested`). A refund the owner filed is never touched. SMS and Apple Pay refunds keep their own merchant match ([iPhone SMS](finance-sms.md#kinds)).

## How figures are counted

One definition of spend, income, refunds, cashback and transfers (`finance/flows.py`), used by the summary, the ledger dashboard, the Money advisor, budgets, the Guardian's caps and every web page that reads them, so the same row is counted the same way everywhere. Bills and debts are read with the person's matched payments (`finance/obligations.py`). This follows what mature personal-finance apps do: a card payment is a transfer between two of the person's accounts, never spending ([YNAB](https://support.ynab.com/en_us/credit-card-payments-a-guide-r1_506Q1j.md)); transfers and card payments are left out of spending, income and cash flow, and paying a card lowers the cash and the debt by the same amount, so net worth does not move; only a loan payment's interest is an expense ([Monarch](https://help.monarch.com/hc/en-us/articles/360048393292), [Monarch tips](https://help.monarch.com/hc/en-us/articles/28953573066260-Tips-Tricks-for-Using-Monarch)); a refund goes back into the purchase's category and lowers its spending, it is not income ([Copilot](https://help.copilot.money/en/articles/5325170-refund-and-reimbursement-transactions), [Rocket Money](https://help.rocketmoney.com/en/articles/3584530-tracking-refunds), [PocketSmith](https://learn.pocketsmith.com/categorising-your-transactions/6a6X8SseDAvLKFX67jnPeQ/managing-refund-or-reimbursement-transactions-within-pocketsmith/8jrtdmUE4Y3X8gSki3Benh)).

- **Spend**: money out in a spending category, **net of the refunds filed in that category**. A category can be below zero in a month that only has a refund. Budgets never show less than zero spent.
- **The type decides** ([Transaction types](#transaction-types)): each row's type maps to one of the lines below; rows stored before types read the type their category stands for.
- **Uncategorized**: money in or out that no rule recognised is neither income nor spend. It waits for review, and every summary says how many rows it left out ("N uncategorized, not counted"). A credit is not income until something says it is.
- **Income**: money in of type `income` (salary, payroll, dividends, interest received, a credit the person filed as income). A negative `income` row (a salary reversal typed by hand) lowers it.
- **Refunds**: money in filed under a spending category lowers that category (an SMS or Apple Pay refund is filed under the purchase it gives back, found by merchant within 120 days). A refund whose purchase is not found is filed as `refunds` and shown on its own line. Neither is income.
- **Cashback**: card rewards, their own line, neither income nor spend. The month's net (cash flow) is income plus cashback plus unmatched refunds, less spend.
- **Transfers**: between the person's own accounts (own-account words, a matched pair, an SMS own transfer) they are neither spend nor income on either side. A transfer **to someone else** is spending, from a statement and from an SMS alike (rent sent to a landlord is housing; anything else `other`), until a match pairs it with an own account; money **received from someone** is uncategorized until the person files it (a client paying by transfer is then income). Rows stored as `transfer` before types existed stay transfers.
- **Lent and borrowed with people**: a transaction linked to a loan with a person ([Lent and borrowed](#lent-and-borrowed)), the money handed over or received and every repayment, is a transfer whatever its category says: lending is not spending and being repaid is not income. The rule is read at the time of reading (`flows.py`, the `lending` set from `obligations.load`) by the summary, the Money advisor, budgets, the planner and the ledger dashboard (a statement or hand-entered row's other side and an iPhone SMS entry's income or expense posting become a transfer; postings on the person's own accounts are unchanged); the stored row is not rewritten. Removing the link (or the loan) makes the row count by its category again.
- **Card payments**: a transfer on both sides. Money in on a card or loan account that a built-in rule (or nothing) filed as income is read as a card payment received, also for rows stored before this rule; a category the person chose by hand or with their own rule is kept.
- **Own transfers told by two messages** ("transferred to" from one own account, "received from" on another, same amount and currency, within 2 days) become one own transfer ([iPhone SMS](finance-sms.md#own-transfers-told-by-two-messages)).
- **Loans**: a repayment moves the whole payment from the bank to the loan (`loan_principal`, a transfer); the interest is charged on the loan as its own row (type and category `interest`, no longer `fees`), so only the interest is spending, the same on the summary and the ledger dashboard.
- **Net worth**: account balances, where cards and loans count by their sign (what they owe is a negative balance), less only the debts that have **no account of their own**. A card kept both as an account and as a debt is counted once.
- **Debts**: a debt kept on a card or loan account (`account_id`) owes what that account's ledger says. Any other debt owes the balance the person saved, less the principal of the payments matched to it since then (with a rate, each payment's interest, balance times APR over 12, is taken out first). It is an estimate from the person's own figures. Entering a different balance (or currency or account) makes it the new starting point; editing the name, rate, minimum or due day keeps the earlier matched payments.
- **Goals** kept in one of the person's own bank, cash or investment accounts in the goal's currency have saved what that account holds; other goals keep what the person entered.
- **Bills**: a due date with an active matched payment is paid and is not taken off again anywhere (upcoming bills, Safe to spend, the advisor's coming payments, the ledger dashboard). This month's due dates that passed without a matched payment stay in as **overdue** until a payment is matched or the person moves the bill's next due date. A debt minimum that is also saved as a bill (same currency, and the same name or the same account) is counted once, as the bill.

Rounding is the server's one rule everywhere, also on the web: half to even, to the currency's minor unit (`lib/financeStatements.ts` `fromMilli`). Classification at read time never rewrites a stored row: older rows keep their stored category and are read by these rules.

## Summary

`GET /api/v1/finance/summary?month=YYYY-MM` (default: this month in the person's time zone), by the rules in [How figures are counted](#how-figures-are-counted):

- balances per account (opening balance plus every transaction) and per currency;
- spend by category for the month (net of refunds in the category; transfers, income, cashback and unmatched refunds excluded), per currency;
- income, cashback, unmatched refunds, spend and the net, per currency;
- bills due in the next 30 days without a matched payment, and this month's overdue ones (`overdue: true`, `days_until` below zero);
- debts owed now and minimum payments, per currency; `unlinked_debts` (debts with no account of their own) and `net_worth` (balances less `unlinked_debts`);
- goals and how far along they are;
- `uncategorized_count`, `uncategorized_in` and `uncategorized_out`: the month's rows no rule recognised, left out of income and spend and shown as "N uncategorized, not counted";
- `month_rows_capped` when the month had more rows than one read returns (50,000): the figures are then partial.

`GET /api/v1/finance/bills/suggestions` finds recurring payments: money out with the same payee at a steady interval (weekly, monthly, quarterly or yearly) and amount (within 15 percent), three or more times, not yet saved as a bill. One payee is one group across accounts, and one missed period (a gap of about two) does not break the pattern. A saved bill with the same currency, cadence and amount (within 15 percent) counts as already saved even when its name differs from the bank's wording ("Electricity" and "DEWA BILL PAYMENT"), so the advisor never counts it twice. The person keeps the ones that are bills.

## Finance overview

`#finance` (`src/FinanceOverviewPage.tsx`) has its own page head with Settings, Import statement, the scope switch (All finance, Personal, Business), the month and, for personal figures in more than one currency, a currency choice.

- **All finance**: personal net worth (balances, with cards and loans as what they owe, less debts with no account of their own) and the business month from the books (income, expenses, net result, each against the month before). **Personal**: net worth, income, spending and net against the month before. **Business**: the business figures and cash flow. People who may not read the books see Personal only, and the switch says why; the chart then reads their own six months whatever scope was chosen.
- Accounts, bills due (total and the next date) and items to review (Guardian flags waiting plus business allocations in review), each opening its page.
- Income and outflow over six months (the books, or the person's own ledger on Personal) with the month's P&L beside it; business spending by category with Open Expenses and P&L, or the person's spending by category.
- Recent activity: the month's newest transactions (on Business, the newest bills). Safe to spend, bills due soon with the review queue, the read-only note, balances and goals.

Read only, like the rest of Finance: there is no pay, transfer or trade button.

## Register and account overview

The Accounts and Transactions and ledger pages read a joined, **read-only** view of the ledger (`finance/register_api.py`, contracts `packages/contracts/src/financeRegister.ts`, web `FinanceAccountsPage.tsx` and `FinanceTransactionsPage.tsx`). Nothing here posts, links or moves money, and nothing says an account is connected: ANUM has no live bank connection, so each account shows the real sources that feed it.

- `GET /api/v1/finance/register` takes the transaction filters (`q`, `account_id`, `category`, `from`, `to`, `direction`) plus `origin` (`sms`, `apple_pay`, `statement`, `manual`), `evidence` (`linked`, `missing`), `status` and `type` (a [transaction type](#transaction-types)), and pages on the server. Each row adds its canonical transaction id, where it came from (the journal entry's sources; a statement row says CSV or PDF), whether a statement confirmed an SMS or Apple Pay record (`reconciled`), how many receipts are linked (`document` evidence links, [Finance links](finance-links.md)), links waiting for review, a **possible duplicate** signal and, when one account is chosen, the balance after the row. Status: `duplicate_candidate`, else `needs_review` (a link needs review), else `uncategorized` (type `uncategorized`: no rule recognised it, held for review, counted in neither income nor spend; `type_reason` says why), else `linked` (a receipt) or `recorded`. Totals (count, categorized, uncategorized, receipts missing for spending and uncategorized money out, matched, money in and out per currency) cover every match, not just the page; `balances` give the beginning and ending balance per currency for the period; `review_count` is the SMS review queue. At most 10,000 matching rows are read (`truncated` says so).
- **Possible duplicate**: the same account, amount and currency within two days, descriptions sharing a word, not already one canonical transaction and not two lines of one statement. It is a signal only; the person checks it and deletes the wrong copy. A "not a duplicate" decision is not stored yet, so a pair the person keeps stays flagged.
- `GET /api/v1/finance/register/{id}` adds the sources with the **masked SMS text only while the retention setting keeps it** (`body_retention_days`, [Masking and retention](finance-sms.md#masking-and-retention)), the receipts' vault document ids, the rule-based category and type suggestion and what made them (your rule, a built-in rule or none), read on the row's own account kind (a credit on a card is suggested as a card payment; audit C5) and the matching record of a possible duplicate. Attach receipt uploads the file to the document vault (category receipt, private) and links it to the canonical transaction as evidence; Save category sets a manual category, which no rule changes again.
- `GET /api/v1/finance/accounts/overview`: per account the sources that feed it (`manual`, `sms` when its last four digits or the default account are mapped on Finance, iPhone SMS, `apple_wallet` for a mapped Wallet card, `statement` once a statement was imported), the last balance a bank message stated against the ledger's, the last import and reconciliation, what needs attention (`balance_differs`, `links_need_review`, `possible_duplicates`), a card's limit (from the SMS card mapping) and its next bill linked to that card within 60 days. Plus the review queue count, the newest reconciliation and the ledger kind.
- `GET /api/v1/finance/accounts/{id}/activity?days=7|14|30|90`: money in and out per day, in the account's currency.

The scope switch on both pages shows this workspace's ledger kind (Finance, iPhone SMS, Settings); each workspace keeps its own ledger, so the other options are disabled and say where that money lives. Import statement and the Imports tab open the existing CSV and PDF import; SMS review is the review queue of [iPhone SMS](finance-sms.md#review-queue), whose settings stay on the SMS capture page. Not built from the mockups: per-account "pause" and "hide from safe-to-spend" (no such setting exists), a stored "not a duplicate" decision, and live bank sync (no provider is connected).

## Safe to spend

`GET /api/v1/finance/safe-to-spend` (stage 3, `finance/safe_to_spend.py`): what the person can spend from today to the end of this month (in their own time zone), and per day, per currency, line by line, from their own records only. It is arithmetic, not advice and not a forecast; the page and the assistant say so.

1. **Money you have:** the balance of every bank and cash account that is not archived. Cards, loans and investments are listed under "Not counted" with the reason (a card's debt is covered by its minimum payment; an investment is not spending money); so are archived accounts.
2. **Bills:** every active bill's due dates from today to the month's end without a matched payment (a weekly bill can count several times; one paid early is not taken off again), and this month's passed due dates that still have none (`bill_overdue`: the money has not gone out yet).
3. **Minimum debt payments:** each debt's minimum payment (at most what is owed now) if its due day is today or later this month, or if it has no due day. A due day already passed is listed as not counted ("taken as paid"). A minimum also saved as a bill is listed as not counted (`minimum_in_bill`): the bill counts it once.
4. **Goals:** the monthly amount the person set on the goal (`monthly_contribution`), else what is left divided by the months left to the target date, this month included, rounded **up** to the minor unit. A goal without either, or already reached, is not counted.
5. **Buffer:** what the person always keeps back, per currency (`PUT /plan`).

`safe_to_spend = money you have - bills - minimum payments - goals - buffer`. A negative figure is shown as "short this month by", with what to change. `per_day` divides a positive figure by the days left, today included, rounded **down** to the minor unit, so it never allows more than the month does. Each line carries a `note` code and `params` for the clients to word in English or Arabic, and an English `detail` for the assistant.

**One total.** With `combine_currencies` on (`PUT /plan`), the response adds `combined` in the base currency when every currency's figure can be converted with a recorded rate no older than `fx_max_age_days` (default 7): each conversion lists the amount, the rate, its date, its source and the shared base of a cross rate (for example USD to AED through EUR). If a rate is missing the status is `missing_rates` with the pairs, and the page points to Connections. Otherwise every figure stays per currency.

## Money advisor

A weekly money note for each person: where the money went, how many days the money would last if income stopped, the payments coming in the next 30 days, advice on each card and a short ranked list of next steps. **ANUM only advises: it never moves money, pays a bill or touches a bank or card.** The page says so on every view, the notice and the push say so, and no route in it writes anything but the note and its switches.

Code: `finance/advisor.py` (pure arithmetic, no I/O, no model call), `finance/advisor_text.py` (the English and Arabic step templates), `finance/advisor_jobs.py` (gathering, writing and the weekly job), `finance/advisor_store.py` and `db/advisor_repository.py`, `finance/advisor_api.py`; migration `0143_finance_money_advisor`; web `apps/web/src/FinanceAdvisorPage.tsx` (`#finance-advisor`), `lib/financeAdvisor.ts`. Tests: `services/api/tests/test_finance_advisor.py`, `test_postgres_finance_advisor.py`, `apps/web/test/financeAdvisor.test.ts`, `apps/web/e2e/finance-advisor.spec.ts`.

Everything is per currency: nothing is ever added or converted across currencies. Amounts are exact decimals. The inputs are the person's own records only: accounts and their ledger balances, the transactions of the last 400 days (statement imports, PDF imports and iPhone SMS all land there), bills, debts, the credit limits entered in iPhone SMS settings and the last balance a bank message stated.

### Survival runway

If income stopped, how long would the money last? **Money you have** is the balance of every bank and cash account that is not archived. A card's available limit is not money you have: it is shown on its own and never counted. Three ways:

| Scenario | Monthly outflow |
|---|---|
| Bare minimum | Active recurring bills as a monthly figure (weekly x 52 / 12, quarterly / 3, yearly / 12; a one-off bill is not monthly) plus every debt's minimum payment (at most what is owed) |
| Essentials | Bare minimum plus the average monthly spending in housing, utilities, telecom, groceries, transport, fuel, health and education. A saved bill in one of those categories is taken out of that category's average, so it is never counted twice |
| As you spend now | The average of every spending category plus minimums, never less than essentials |

The average is over the last three full calendar months the records cover (a month counts when the records start in its first week); with no full month, the last 30 days scaled to a month. Days are `money you have x 30.4375 / monthly outflow`, rounded down, with the date the money runs out. No money gives 0 days; nothing going out gives no end date; a runway past the last date the calendar holds keeps its day count and shows no end date. **With fewer than 30 days of records** only the bare minimum is worked out (it needs no history) and the note says plainly that there is not enough history, instead of guessing. The page writes out every assumption under the figures. Salary detection only informs: a credit with salary, payroll, WPS or راتب in the last 60 days, or a steady monthly credit from one payer; without one the note says so, and the runway already assumes no income.

### Where money went

Spending is money out except transfers and income. The **last 7 days** (the week ending yesterday) against the 7 days before and against the three-month average for a week; **this month so far** against the same days of last month and the three-month monthly average. Top categories (7 days, this month, usual month), the top five payees for the week and the month, and **unusual** spending: a category already 30 % above a usual whole month (so a rent paid on the 1st is not flagged), or a category with nothing in the averaged months that took at least 10 % of a usual month in 7 days. Small amounts (under 5 % of a usual month) are not flagged.

### Coming payments

The next 30 days: saved bills (a weekly bill each time it falls due), debt and card minimums on their next due day (a minimum with no due day is listed as "no due day set"), and recurring payments found in the records but not saved as bills (same payee, steady interval and amount, three times or more; the [bill suggestions](#summary) rule), each date in the window by its cadence like a saved bill, marked as subscriptions when the category or payee says so. Totals per currency: saved, found, and due within 7 days.

### Card advice

Each card account (and each card under Debts with no card account). What it owes by the ledger (or the debt's balance when the ledger shows nothing), the last balance a bank message stated, the credit limit and what is available when the person entered a limit, the minimum and the next due date from the linked debt. With an interest rate the person entered (APR on the debt), the interest of paying only the minimum next month (`(owed - minimum) x APR / 12`, rounded up), the months to clear at the minimum and the interest paid until then (each month the minimum is paid first, then interest is charged on what is left, the same order as the next-month figure), or that the minimum never clears the balance. **A rate is never invented:** an APR of 0 or none means "add the rate", and every missing piece is listed (a debt record, the rate, the due day, the minimum, the limit) with where to add it.

### Next steps

At most 10, most urgent first, then the soonest date. Fixed templates in English and Arabic (`advisor_text.py`, figures and names wrapped in Unicode isolates in Arabic, no long dashes), each with the Finance page that does it: pay a card in full before its due date (with the interest the minimum would cost when the rate is known), a minimum that never clears, add a card's debt record, due day, minimum or rate, a card over 30 % of its limit, a bill or minimum due within 7 days, survival under 60 days (urgent under 30), import more history, import a recent statement (newest record over 7 days old), add an account, spending up or new in a category, review subscriptions, save recurring payments as bills, and no salary found. No model writes them.

### Weekly delivery

`finance_advisor_reports` holds one note per person per advice week (`week_start`, unique) and `finance_advisor_settings` the switches: on or off (on by default) and the weekday (Sunday by default, Monday is 0), in the person's Finance time zone (Finance, Settings; Asia/Dubai until the person changes it). Both tables have forced RLS with the tenant policy, a restrictive owner-only policy and the tenant purge policy; they are in the organisation lifecycle, in "Download my data" (`advisor_notes`, `advisor_settings` in the `finance` section) and in "Delete my data". Notes older than two years are deleted when a new one is written.

The daily job (`python -m anum_api.voice_retention`, reported under `finance_advisor` as counts only; `finance/advisor_jobs.py`) finds people with an active finance account as `anum_maintenance`, which may read only `tenant_id`, `workspace_id`, `owner_user_id` and `archived` of `finance_accounts` (a column grant, a select policy on unarchived rows and the restrictive owner policy admitting that role), never BYPASSRLS. Each person is then handled as the application role in their own scope: off, a viewer (no `finance:read`) or not their day is skipped; on the chosen day (or up to two days late if the job missed it) the week's note is written and `delivered_at` is claimed in one short transaction, then one in-app notification is sent (kind `finance_weekly_advice`, days and counts only, no amount or name). Its `notification.created` event is the phone push `finance_advice` ("Weekly money note" / "Your weekly money note is ready. ANUM only advises: it never moves money.", Arabic on an Arabic phone) when push is on. **Never an email** (owner rule): the notice is created without an address. A failed notice releases the claim so the next run tries again; a delivered week is never announced twice, also when the job runs twice or after "Generate now". The web bell opens `#finance-advisor`; the phone opens Finance.

### Routes

| Route | Permission |
|---|---|
| `GET /advisor` (switches, this advice week, next note date, newest note) | `finance:read` |
| `GET /advisor/notes?limit=&offset=` (newest week first, at most 50), `GET /advisor/notes/{id}` | `finance:read` |
| `POST /advisor/generate` (works this week's note out again; sends no notice) | `finance:manage` |
| `GET /advisor/settings`; `PUT /advisor/settings` `{enabled, weekday}` | read; manage |

Audit records carry the note id and counts only.

### Not built

The phone shows no advisor screen yet (the push and notice open Finance); the note covers personal finance only, not the business books; no forecast of future income; recurring income is not projected.
## Bills and budgets

Finance, **Bills and budgets** (`#finance-bills`, `#finance-budgets`; web `FinanceBillsBudgets.tsx`, figures in `lib/financeBills.ts`) has the tabs Bills, Budgets, Recurring and Calendar, the scope (Personal only: Business and All finance point to the business books, where business bills and budgets live), the month and Add bill.

- **A bill is a forecast until it is matched.** Each due date of an active bill in the window (`GET /bill-schedule?from&to`, at most 400 days; dates run forward from `next_due_on` and back from it, never before the bill was added) has a status worked out on every read: `paid` only when a recorded payment is matched to that due date, `review` when the match waits for review (the payment was corrected), else `overdue`, `due_soon` (7 days or less) or `planned`. Nothing else marks a bill paid.
- **Matching** is a [finance link](finance-links.md) in the `bill` dimension: target `finance_bill_due`, id `<bill id>:<YYYY-MM-DD>`, on the payment's `canonical_id`. `GET /payment-candidates?for=bill|debt&around&amount&currency&q` lists the person's own payments from 60 days before to 15 days after the date (money in is never offered), closest in amount first, with what is left to match. `POST /bills/{id}/matches` (`{canonical_id, due_on, amount?}`, optional `Idempotency-Key`) takes the expected amount, at most what is left of the payment; `DELETE /bills/{id}/matches/{link_id}` voids the link. The journal is never written.
- **Budgets** are the person's Guardian category caps. `GET /budgets?month` gives each cap with what was spent, what is left, `ok`, `near` or `over`, and for the current month where this month's pace ends (`projected`, `projected_over`), plus the day by day spend of the capped categories against the caps together (the chart). A new or changed cap is proposed and confirmed with its code like every rule ([Guardian](#guardian)); removing one waits in Guardian. `PUT /budgets/alerts` keeps, per cap, the alert threshold (50 to 99 percent, default 80) and whether to alert at it and at 100 percent; Guardian's `cap_near` and `cap_over` alerts follow them.
- The page shows the KPIs (bills this month, due in 7 days or overdue, active budgets, budget remaining, per currency, never added across currencies), the day strip and the bills table, Budget health with the spend chart (a table behind "Show as a table"), the budget most at risk, Budget rules, the Link and pay explainer, the recurring bills (pause, resume, edit, delete, the detected recurring payments to keep) and the month as a calendar.

## Debts and goals

Finance, **Debts and goals** (`#finance-debts`, `#finance-goals`; web `FinanceDebtsGoals.tsx`, the maths in `lib/financeDebtPlan.ts`) has the tabs Debts, Goals, Plan and History, the scope (Personal only), the currency (one plan per currency, never mixed) and the New plan wizard.

- **The plan** (`GET, PUT /debt-plan`, `{plans: [{currency, strategy, monthly_payment}]}`): avalanche (highest rate first) or snowball (smallest balance first), and the monthly payment for all debts of that currency (empty: the minimums only). Stored with the safe-to-spend settings (`finance_plans.debt_plans`) without changing them.
- **The maths** is pure and runs on the debts as entered: each month a twelfth of the yearly rate on the balance is added (rounded to the minor unit), every debt gets its minimum, the rest of the payment goes in strategy order, and a paid-off debt frees its minimum for the next. It gives the payoff month with the minimums only and with the plan, the interest, the payoff order, the what-if (an extra amount a month: months sooner and interest saved) and the 12-month outlook of total debt and goal savings. A payment below the minimums together is refused; payments that never cover the interest say so instead of a date. The page says plainly that rates and balances are the person's own estimates.
- **Payoff progress** needs a starting balance: `finance_debts.original_balance` (filled with today's balance when a debt is added on the web; left out of an edit, it is kept; `""` clears it).
- **Debt payments** are links in the `debt` dimension (target `finance_debt`): `POST /debts/{id}/payments`, `DELETE /debts/{id}/payments/{link_id}`, `GET /debt-payments?debt_id&limit&offset` (date, account, amount, status and what was paid per debt). The stored balance stays what the person entered; every figure that reads it (the summary, net worth, Safe to spend, the advisor and the ledger dashboard) takes off the principal of the payments matched since it was saved, with the interest estimated from the person's own rate ([How figures are counted](#how-figures-are-counted)). A debt kept on a card or loan account follows that account instead.
- **Goals** have a purpose (`finance_goals.purpose`: general, emergency fund, family at home, a purchase, education, travel; migration `0147_finance_personal_money`); the planner reads the emergency fund when it says what to avoid. Goals show progress, the monthly target (the amount set, else what is left over the months left) and the due date. `finance_goals.paused`: a paused goal keeps what is saved, is not set aside by safe to spend (`goal_paused`, under "Not counted") and is left out of the commitments and the outlook. Upcoming commitments list each debt's next minimum payment and the goal contributions for the month.

Code: `finance/commitments.py` (pure figures and the link targets), `finance/commitments_api.py` (routes); migration `0142_finance_debts_bills` (expand only: `paused`, `original_balance`, the two plan columns and the `bill` and `debt` link dimensions); contracts `packages/contracts/src/financePlanning.ts`. Tests: `tests/test_finance_debts_bills.py`, `tests/test_postgres_finance_debts_bills.py`, web `test/financeDebtPlan.test.ts`, `test/financeBills.test.ts`, `e2e/finance-planning.spec.ts`.

Not built yet: bill reminders a set number of days before the due date (a daily job like the purchase reminders: the mockup's Reminder column is left out until it exists), splitting a debt payment into interest and principal from the lender's own figures (the split is estimated from the rate the person entered), automatic matching without a person's pick, and business bills and budgets on these pages (they are in the business books).

## Lent and borrowed

Finance, **Lent and borrowed** (`#finance-people`; web `FinancePersonalMoney.tsx`, tab People): interest-free loans with people (qard hasan), in both directions: **I lent** (they owe me) and **I borrowed** (I owe them). Each loan (`finance_person_loans`) has the person, amount and currency, the date, an optional due date, notes, the reminder switch with how many days before the due date (0 to 30, default 3) and "closed without full repayment" (forgiven or settled another way).

- **Linked to the ledger.** What changed hands and every repayment are [finance links](finance-links.md) in the `person_loan` dimension (target `finance_person_loan`) on real recorded transactions: lending and repaying a loan are money out (`payment`), borrowing and being repaid are money in (`deposit`); a transaction in the wrong direction is refused (`not_a_payment`, `not_money_in`). Repaid is the sum of the active repayment links in the loan's currency; left is the amount less that. Status: `open`, `due_soon` (7 days or less), `overdue`, `repaid` or `closed`.
- **Not spending, not income.** A linked transaction is a transfer everywhere ([How figures are counted](#how-figures-are-counted)). Deleting a loan voids its links; the transactions stay in the ledger and count by their category again.
- **Reminders** in ANUM only: one notification `remind_days_before` days before the due date and one more once it is overdue (kind `finance_loan_due`, in-app only: never an email and never a phone push). The daily job (`python -m anum_api.voice_retention`, reported under `finance_loan_reminders`, counts only; `finance/personal_jobs.py`) finds people the way the weekly money note does (an active finance account) and works in each person's own scope; the due date announced is claimed with a compare and set before the notice and released if the notice fails, so each reminder goes out once. Changing the due date re-arms it. A viewer gets none.
- Routes: `GET /people-loans?q&direction&status&limit&offset` (with what is owed per currency over every open loan and the overdue count), `POST /people-loans`, `GET|PATCH|DELETE /people-loans/{id}` (the detail lists the linked transactions), `POST /people-loans/{id}/links` (`{canonical_id, kind: handed|repayment, amount?}`, optional `Idempotency-Key`: a retry of a link that succeeded returns that link, `replayed: true`, also for payslips and remittances), `DELETE /people-loans/{id}/links/{link_id}`. `GET /payment-candidates?for=person_loan&direction=in|out` offers the person's own transactions.

## Payslips

Finance, **Payslips** (`#finance-payslips`): one payslip per pay month and employer (`finance_payslips`): gross pay and each deduction (`social_insurance` such as GOSI or a pension, `medical_insurance`, `loan`, `advance`, `tax`, `other`, each with an optional label). Net is gross less the deductions; deductions above gross are refused.

- **Reconciled with the ledger.** The salary credit is linked (`payslip` dimension, target `finance_payslip`, money in only). `received` is what the linked credits add up to and `difference` is received less net: `matched`, `differs` (shown with the amount) or `not_linked`.
- **By month:** gross against net, the deductions by kind and what was received, per month and currency, for the year chosen.
- Routes: `GET /payslips?year&q&limit&offset` (with `months`), `POST /payslips`, `GET|PATCH|DELETE /payslips/{id}`, `POST|DELETE /payslips/{id}/links[/{link_id}]`.

## Sending money home

Finance, **Send home** (`#finance-remittances`): each transfer home (`finance_remittances`): the date, the amount sent and its currency, the fee, the exchange rate, what arrived and in which currency (either one is enough: the other is worked out), the recipient, the provider and whether it is sent every month.

- **Totals** per month and send currency: sent, fees, the number of transfers and what arrived per received currency.
- **Providers compared from the person's own transfers:** per provider and corridor (`AED>INR`), the fee as a share of the amount and the effective rate (what arrived per unit paid, fee included; higher is cheaper). Live fees and rates are in the [Plan](#planning-and-advice).
- Linked to the money-out transaction that paid for it (`remittance` dimension, target `finance_remittance`). A regular transfer counts in [Fixed commitments](#fixed-commitments).
- Routes: `GET /remittances?q&month&limit&offset` (with `months` and `providers`), `POST /remittances`, `GET|PATCH|DELETE /remittances/{id}`, `POST|DELETE /remittances/{id}/links[/{link_id}]`.
- Savings for the family use the existing goals with the purpose "Family at home" ([Debts and goals](#debts-and-goals)); there is no second goals feature.

## Fixed commitments

Finance, **Fixed costs** (`#finance-commitments`, `GET /fixed-commitments`; `personal.fixed_commitments`): everything that goes out every month, one total per currency, never added across currencies:

- active saved bills by their monthly figure (weekly x 52 / 12, quarterly / 3, yearly / 12), shown as rent (housing, room rent), subscriptions or bills; a one-off bill is listed as not monthly;
- installment plans from the imported provider statements (Tamara, Tabby): this month's unpaid installments per order;
- debt minimums (loans and cards, at most what is owed);
- regular remittances: the newest per recipient and currency, amount plus fee.

Nothing counts twice: a debt minimum also saved as a bill (`obligations.bill_for_debt`), an installment order whose merchant or provider is a saved bill and a regular remittance to a recipient saved as a bill are listed under "Left out" as `in_bill`. A loan deduction already taken from the salary (Payslips) is not a commitment of its own.

## Planning and advice

The **Plan** view inside the Money advisor (`#finance-advisor`, tab Plan; web `FinanceAdvisorPlan.tsx`) and the assistant's planning tools answer "can I afford this", "which debt first", "which loan" and "the cheapest way to send money home" from the person's own records. **Advice only: ANUM never pays, buys or transfers.** Every figure is labelled as the person's own data or as a live source with its address and the date it was read. Code: `finance/planner.py` (pure arithmetic), `finance/planning_service.py` (reading the records, live research), `finance/personal_api.py` (routes), `finance/plan_tools.py` (the assistant).

- **Which debt to pay first** (`GET /planner/payoff?payment&currency`): avalanche (highest rate first) against snowball (smallest balance first) with today's balances ([How figures are counted](#how-figures-are-counted)), the rates and the minimums: months to debt-free, total interest and the payoff order for each, which one is cheaper and by how much, and when snowball clears its first debt. The payment is the one asked, else the saved [debt plan](#debts-and-goals), else the minimums. The month by month rule is the Debts plan's (a twelfth of the yearly rate on each balance, rounded half to even to the minor unit; every debt its minimum; the rest in strategy order). A debt with no rate is named and counted at 0 percent, never guessed.
- **Can I afford it** (`POST /planner/afford {price, currency, item, monthly_cost}`): against this month's Safe to spend, money in bank and cash, the average monthly income of the last three full months (the shared rule), the fixed commitments and the essential monthly spending from the advisor's runway. Verdicts: within Safe to spend, from savings, cash leaves under three months of essentials, and for a monthly payment ANUM's rule of thumb (stated as such): commitments up to a third of income are comfortable, up to half tight, over half too much; no income recorded says so. **How to pay** lists the person's bank and cash accounts in that currency that cover it, then cards (interest at the person's own rate, or "add the rate"), then accounts in another currency (FX). **What to avoid** comes only from the records: FX fees the person paid (journal `fx_fee`, 400 days), late fees (a `fees` row that says late, overdue or penalty), carrying a card balance at its rate, commitments over half of income, an emergency fund below three months of essentials. After a purchase the page offers Purchases to record it ([Purchases](purchases.md)).
- **Cash, a loan or installments** (`POST /planner/compare`): for each option the monthly payment, months, total cost (down payment, every payment and fees) and the interest or profit, cheapest first. Standard amortisation `P x r / (1 - (1 + r)^-n)` with `r` the yearly rate over 12, the payment rounded up to the minor unit, or a flat rate (profit `P x rate x years`, common for UAE car and personal finance). An estimate: the lender's offer decides. **A rate is never invented:** the person's own, or a live source with its address (`rate_source`, `https://` only) and date; an option without one is `rate_unknown` and not costed, installments included.
- **Live figures** (`POST /planner/research {topic: price|loan_rate|installments|remittance, query, country}`): one call of the registered [`web_search`](tools-and-integrations.md#web-search) tool (the private SearXNG, `ANUM_SEARXNG_URL`) through the runtime's policy and audit (`business.pipeline.call_tool`, surface `finance_plan`). Each result keeps its address, the date read and the percentages and amounts found in its title and snippet; results are untrusted data. Without web search the answer is `unavailable` ("Live prices are unavailable") and the plan continues with the person's own data; nothing found is `not_found`, the figure unknown. Reading a page in full stays with [browser research](browser-research.md) (`web.browse_page`) when the person gives an address.
- **The cheapest way to send money home**: the person's own provider comparison ([Sending money home](#sending-money-home)) and, when web search is on, today's fees and rates with their sources.

### Assistant

The planning tools are part of the existing `anum.finance` skill (no new agent): `finance.plan.afford`, `finance.plan.debt_order`, `finance.plan.compare` and `finance.plan.send_home`, each `action_kind="read"`, `RiskLevel.LOW`, on the caller's own records, with "Advice only" in every answer and every number labelled. The skill's triggers add buy, loan, loans, installment(s), first, cheapest, remittance(s), payoff, avalanche, snowball, payslip(s), salary, lent, borrowed and, in Arabic, أشتري, اشتري, أسدد, اسدد, للأهل, قرض, قروض, تقسيط, أقساط, حوالة, راتب. The planner (`plan_tools.plan_call`, from the user's own words only) answers "can I afford a car for 80,000", "should I buy a new phone", "أقدر أشتري سيارة بـ 80 ألف", "هل أشتري" (afford), "which loan is better: 80000 over 4 years at 3.5% flat" and "أي قرض أفضل" (compare), "which debt should I pay first" and "أي دين أسدد أولا" (debt order), "what is the cheapest way to send money home" and "أرخص طريقة للتحويل للأهل" (send home). An action ("buy it for me", "send 500 AED to my mother") is never a planning question: it falls through to `anum.external-action`, which waits for approval as before. Replies pass the punctuation filter (`anum_api/punctuation.py`).

### Not built

The phone shows Payslips and Send home on the web only (a line in Finance links to them), and its planner is the question box of the Money planner, not the Plan view's afford and compare forms ([Mobile app features](mobile-features.md#personal-money)); the planner does not read a page in full by itself (the person asks browser research with the address); reminders are not sent for loans of people without a Finance account (the job finds people by their accounts); the Guardian's monthly caps still read the stored category of a lent or borrowed payment (file it as a transfer, or the cap counts it); payslips are typed by hand (no payslip PDF import).

## Exchange rates

`finance/fx.py`. ANUM never invents a rate. A rate (`finance_fx_rates`) is either:

- **published** by the European Central Bank's euro foreign exchange reference rates (about 30 currencies against the euro, once a working day, `https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml`), fetched by the `ecb_fx` connector and stored with the ECB's date, source name and address. The ECB publishes them for information and allows reuse with the source named; ANUM names the source and date on every rate. The UAE dirham is not in the ECB list: it is pegged by the Central Bank of the UAE at 3.6725 dirhams per US dollar, so the connector also stores EUR to AED as the ECB's EUR to USD rate times that peg, with the source saying so (`ECB EUR/USD x CBUAE peg 3.6725 AED per USD`);
- **typed** by the person (`POST /fx-rates`, source `manual`), with the date they give (within the last year, never in the future).

The ECB answer is untrusted internet content: it is parsed by a strict pattern (never an XML parser that could expand entities), capped at 64 KB, every code must be three letters and listed once, every rate a number between 0.000001 and 10,000,000, the date within the last 10 days and not in the future; anything else is refused and the connection shows the error. Conversions use the newest rate within the age limit (a published rate wins over a typed one on the same date): direct, inverse, or a cross through a shared base. A converted figure is rounded once, half to even, to the target currency's minor unit.

## Guardian

Stage 4 (`finance/guardian.py`, `finance/policy.py`). The person's own spending and risk rules, stored per person and versioned, and a pure, deterministic evaluator:

    evaluate(action, rules, ledger_state) -> Decision(allow | needs_approval | deny, reasons)

**Rules** (`GuardianRules`; empty means "no rule of this kind"): allowed currencies; a single payment limit and a daily total limit per currency; monthly caps per category and currency; blocked categories; blocked merchants (whole words or phrases of the cleaned description); "your approval at or above" an amount per currency; quiet hours (local time, may wrap past midnight); a cooling-off period (hours) for amounts at or above a threshold per currency. One limit per currency (and per category for caps); amounts follow the money rules.

**Evaluation.** The same inputs always give the same decision; nothing reads a clock, a store or the network. Reasons come in a fixed order, each with a code, an outcome, a message, the rule and parameters. Deny: an invalid amount or time, a currency not allowed, a blocked category or merchant, over the single limit, over the daily total (today's spend plus this amount), over a monthly cap (this month's spend in the category plus this amount). Needs approval: at or above the approval amount, within quiet hours, within the cooling-off period (until first asked plus the hours), or asked by an assistant for a money-moving action. The outcome is the most severe reason. A `record` (writing down money already gone) is checked against the limits and blocks only: approval and the time rules do not apply to it.

**The gate.** `gate(action, rules, state)` is the mandatory check every finance action passes: the rules plus the kernel's own limits, which no rule can lift. Today the first kernel limit is absolute: ANUM moves no money, so every `spend`, `bill_payment`, `transfer` and `trade` is denied (`execution_not_available`). The execution gateway (stage 7) calls `gate` for every request and acts only on `allow`, or on `needs_approval` once the person approved that exact request; for a SIMULATED paper or shadow order (`gate(..., simulated=True)`) the kernel adds only the note `simulated_only` and the rules decide, so the person sees which rules would block the real action.

**Where it is used now.** Creating or changing a transaction returns `guardian` (what the rules say about that record; it is saved anyway, the money is gone) and the web names any rule it breaks; a confirmed import returns `rule_breaks`; `GET /guardian/alerts?month=` lists caps reached or within 20 percent, payments to blocked merchants or categories, days over the daily total and payments over the single limit (uncategorized money out is watched too: it may be spending, so a big unknown debit is flagged before the person files it); `POST /guardian/evaluate` is a dry run of any action ("Check a spend"), returning the rules' verdict and the gate's, and noting that nothing was paid, sent or saved.

**Pausing a rule.** `paused` names kinds of rule the person set but does not want applied for now (for example `["max_single"]`). A paused kind is kept as written and resumed with one change; while paused, `evaluate` (and so `gate`, the alerts, the review queue and import rule breaks) leaves it out. The kernel's own limits are never paused: ANUM still moves no money. Only kinds that are set can be paused (others are dropped). Pausing is a change like any other (proposed, then confirmed with its payload hash) and it **loosens** the kind, so an assistant can never pause a rule; raising a paused limit is also loosening, checked on the rules as written. `paused` is left out of the payload hash while empty, so rules saved before pausing existed keep their hash. Pausing every kind is how the page's "Pause monitoring" works: the rules stay, nothing is flagged until they are resumed.

**Changing rules.** Every change, the person's own included, is a proposal (`POST /guardian/changes`) on the current version, with a SHA-256 payload hash over the change id, the owner, the base version and the exact rules (the same binding rule as approvals, `approval_integrity.py`). It takes effect only when the person confirms it with the hash they were shown (`POST /guardian/changes/{id}/confirm {"payload_hash": ...}`): a different hash, a change that lapsed (24 hours) or one made on an older version is refused with 409 and nothing changes; confirming adds a new version (unique per person, so two confirmations cannot both land) and supersedes every other pending change. The web shows the new rules, what is new, what is no longer a rule, what the change loosens and the first characters of the code before the person confirms. Every proposal, confirmation and rejection is audited with ids, version, rule-kind codes and who proposed it, never a limit. **Loosening** (`loosening(old, new)`: a limit raised or removed, a block removed, quiet hours narrowed, a shorter cooling-off) is listed on the change; an assistant's proposal that loosens anything is refused, by the code and by a database check.

### Review queue

`finance/guardian_review.py`, migration `0140_finance_guardian_reviews`. Guardian is a monitoring aid: for one month (`GET /guardian/review?month=&state=awaiting|resolved|snoozed|all&limit=&offset=`) it checks every payment out of the person's ledger against the rules that apply now, as `evaluate` checks a `record`, in posting order (each payment against the month's and the day's spend before it). Every payment a rule flags is an item with the rules' reasons, the rule kinds, the account, and what is typical: the median of the person's earlier payments (up to a year back) with the same cleaned description and currency, and how many there were. The answer also counts the month's flagged, awaiting, resolved and snoozed items and, per rule kind that is set, how many payments it flagged and the last date. Approval, quiet hours and cooling off apply to an action asked for (a paper or live order, a dry run), never to money already recorded, so they never flag a payment.

`POST /guardian/review/{transaction_id} {"decision": "expected" | "reviewed" | "snoozed" | "reopen", "snooze_days": 1 | 3 | 7 | 30}` records the person's decision (`finance:manage`): one row per person and payment (`finance_guardian_reviews`, forced RLS with the owner-only restrictive policy and the purge policy), with the amount and currency it was decided on, so a corrected payment waits for review again. A snooze lasts until the start of the day that many days ahead in the person's time zone; `reopen` ends the decision. A decision is a note only: nothing is paid, held, approved or sent to a bank. It is audited as `finance.guardian.review` with the transaction id, the decision and rule codes, never an amount or a description; the decisions are in the privacy export (`guardian_reviews`) and deleted with the person's finance data.

`GET /guardian/activity?limit=&cursor=` is the person's own Guardian audit trail (`finance.guardian.*` records whose actor is the caller: proposals, confirmations, rejections and review decisions), newest first, keyset paged. Ids, codes and versions only. The workspace audit log stays owner only.

### Notifications

Guardian does not send notifications today. Its alerts and the review queue are shown in Finance, Guardian (and the Safe to spend preview); there is no bell notification, e-mail or SMS for them. ANUM sends no e-mail or SMS on its own, so the page shows e-mail and SMS delivery as not available with that reason. Guardian's quiet hours are a rule on actions (an action asked for in that window needs approval), not a notification setting; the page shows them with the time zone from Finance settings.

## Connectors

Stage 5 (`finance/connectors.py`, `finance/sync.py`). Read-only sources that bring outside data into the person's own ledger.

- **Registry** (`GET /connectors`): each source with its kind, read-only scopes, whether it needs credentials, sync interval, terms, and for a source that is not built, what the owner must set up first. Only sources with a built adapter can be connected.
- **Built:** `ecb_fx`, the ECB reference rates ([Exchange rates](#exchange-rates)): no account, no key, every 12 hours.
- **Listed, not built (owner actions):** bank and card accounts through a UAE Open Finance (Al Tareq) licensed provider, a broker (positions and cash), a crypto exchange (balances), market prices. Each needs a provider account and keys ANUM does not have and an adapter checked against the provider's documented API; they cannot be connected and nothing about them is faked.
- **Connections** (`GET, POST /connections`, `GET, PATCH /connections/{id}`): one per person and provider (a source without credentials is connected once). Credentials are write only: validated against the source's fields, Fernet-encrypted with `ANUM_SECRETS_KEY` at once, never returned (only `has_credentials` and a short hint), re-encrypted by `python -m anum_api.rotate_secrets` in each owner's own scope, and never exported.
- **Health:** status (`active`, `error`, `disconnected`), last sync, last success, next sync, the last error as a safe message, and the last counts.
- **Sync now** (`POST /connections/{id}/sync`): runs one sync; **disconnect** (`POST /connections/{id}/disconnect`) stops syncing and erases the credentials (a database check keeps a disconnected connection without them), data already fetched stays; **delete** removes the connection, its credentials and the rates it fetched (transactions it imported stay in the ledger, undoable as an import).
- **Imported transactions** land in the stage 2 ledger only for accounts the connection maps, with the same dedupe hash (keyed by the provider's own id), cleaned like statement text and categorized by the same rules, so a repeated sync adds nothing twice.

### Sync

A sync never holds a database transaction across the network call: it reads the connection in one short unit of work in the owner's scope, fetches with none open (one GET, no redirects, a 256 KB cap, a 15-second timeout), then writes the results and the connection's health in a second unit of work. A failure is recorded on the connection and retried after `ANUM_FINANCE_RETRY_MINUTES`. With `ANUM_FINANCE_SYNC_ENABLED=true` every API process runs the background loop: every `ANUM_FINANCE_SYNC_POLL_SECONDS` it finds due connections as `anum_maintenance` (id, scope, owner, `enabled`, `status`, `next_sync_at` only), claims each with a compare-and-set update of `next_sync_at` and syncs it as the application role in the owner's own tenant, workspace and user scope ([Multi-tenancy](multi-tenancy.md#maintenance-role)). The API login needs `anum_maintenance` granted `WITH INHERIT FALSE, SET TRUE`.

### Operator settings

| Setting | Default | What it does |
|---|---|---|
| `ANUM_FINANCE_SYNC_ENABLED` | `false` | Run the background sync (otherwise **Sync now** only) |
| `ANUM_FINANCE_SYNC_POLL_SECONDS` | `300` | How often the loop looks for due connections |
| `ANUM_FINANCE_SYNC_BATCH_SIZE` | `20` | Connections per pass |
| `ANUM_FINANCE_REQUEST_TIMEOUT_SECONDS` | `15` | One request to a source |
| `ANUM_FINANCE_RETRY_MINUTES` | `60` | Wait after a failed sync |
| `ANUM_FINANCE_EVAL_MAX_LOAD_PER_CPU` | `0.85` | Trading intelligence: a heavy evaluation does not start while the 1-minute load per CPU is above this ([load guard](finance-trading-intel.md#load-guard)) |
| `ANUM_FINANCE_EVAL_MIN_FREE_MEMORY_MB` | `256` | ... nor while less memory than this is free (the container's cgroup limit when it has one) |
| `ANUM_FINANCE_EVAL_MAX_CONCURRENT` | `2` | ... nor while this many evaluations already run in the process |
| `ANUM_FINANCE_COMMENTARY_ENABLED` | `true` | Model commentary on the daily trading report, only when the workspace has a real model ([model commentary](finance-trading-intel.md#model-commentary)) |
| `ANUM_FINANCE_COMMENTARY_MONTHLY_LIMIT` | `31` | At most this many commentaries per person per UTC month, inside the workspace's model budget |
| `ANUM_FINANCE_COMMENTARY_MAX_CHARS` | `1500` | The longest commentary kept |
| `ANUM_FINANCE_COMMENTARY_TIMEOUT_SECONDS` | `45` | How long the model may take |
| `ANUM_FINANCE_RESEARCH_CONTACT` | empty | Contact e-mail SEC EDGAR asks automated readers for ([Fund research](finance-research.md#operator-settings)); empty: SEC fund facts are not read |
| `ANUM_FINANCE_RESEARCH_CACHE_MINUTES` | `30` | How long research figures are kept per person |
| `ANUM_FINANCE_RESEARCH_TIMEOUT_SECONDS` | `20` | Timeout for each research source and the model |
| `ANUM_FINANCE_RESEARCH_SUMMARY_ENABLED` | `true` | AI research summaries on or off |
| `ANUM_FINANCE_RESEARCH_SUMMARY_DAILY_LIMIT` | `10` | AI research summaries per person per day, inside the model budget |

The API must be able to reach `www.ecb.europa.eu` over HTTPS (add it to the network policy's `appEgress` where egress is restricted).

## Connections and data capture

The web page `#finance-connections` (`apps/web/src/FinanceConnections.tsx`, its own lazy chunk; helpers `src/lib/financeConnections.ts`, catalogs `messages/financeConnections.{en,ar}.ts`) gathers every way data reaches the ledger. `#finance-sms` opens the same page on its SMS capture tab. It reads only existing routes; nothing on it moves money.

- **Head:** Work scope (Business or Personal) is the ledger's kind (`PUT /sms/settings` `ledger_kind`): a label for this workspace's ledger, since each workspace keeps its own books.
- **KPI row:** accounts in ANUM (not archived), iPhone Shortcut routes that work now (an integration switched on and carried by an active device, from `GET /integrations/iphone`), records awaiting review (`review_count`), and the last data received (the newest SMS or Apple Pay report, statement import or exchange-rate sync, with which one it was).
- **Connected accounts:** the read-only notice, then each account with the sources that really feed it: SMS capture (its last four digits are mapped, it is a Wallet card's, the default or the cash account), Statements (it has an import) or Manual; the last statement import, records, currency and balance; Import statement, Map SMS and Edit open the real screens. **ANUM has no live bank connection**, so the page shows no bank sync, sync count, consent expiry, Pause sync or Reconnect; "Add a source" offers an account by hand, a CSV or PDF statement and iPhone SMS, and says Open Finance is an owner action ([Owner actions](#owner-actions)). The exchange-rate connections and sources ([Connectors](#connectors)) follow below.
- **iPhone SMS capture panel:** status (Active, Off, Safety check needed), the device count, last received, messages received, recorded (posted and attached) and waiting for review; how it works; the endpoint with Copy; the selected device's token as its hint only, with Rotate (the new token is shown once in the usual dialog; a token is never shown again); the device's scopes, a device picker, last check-in and status; Pause or Resume capture, Revoke access and the security log (refused and failed requests per address, `GET /integrations/iphone/integrations/{key}/events`); the five most recent messages (status, merchant or sender, time, amount, the reading's kind and reason); the review policy (Always require review = `auto_post` off; Erase message text = `body_retention_days` 0, switching back keeps 30 days) and the privacy note.
- **SMS capture:** the full iPhone SMS tools ([iPhone SMS](finance-sms.md)). **iPhone Shortcuts:** Settings, Integrations, iPhone ([iPhone integrations](iphone-integrations.md)). **Import history:** every statement import, CSV, PDF or scanned PDF, with rows added, duplicates, matched and held for review, paged, with Undo. **Permissions:** what ANUM can and cannot do (read, change, own records, no bank connection, AI help, message retention) and the device tokens with their scopes, hint and last check-in.
- Not backed by data, so not shown: a per-message confidence percentage and a suggested category before recording (the reader gives neither), screenshot attachments, live bank sync.

## API

All under `/api/v1/finance`, every route checking permissions on the server:

| Route | Permission |
|---|---|
| `GET /summary`, `GET /settings`, `GET /formats` | `finance:read` |
| `PUT /settings` | `finance:manage` |
| `GET, POST /accounts`; `GET, PATCH, DELETE /accounts/{id}` | read for GET, manage otherwise |
| `GET, POST /transactions`; `GET, PATCH, DELETE /transactions/{id}` (`POST` and `PATCH` take an optional `transaction_type`); `GET /transactions/export.csv` (`q`, `account_id`, `category`, `from`, `to`, `direction`, `limit`, `offset`; the last column is the type) | read for GET, manage otherwise |
| `POST /imports/preview`, `POST /imports`, `GET /imports`, `DELETE /imports/{id}` | manage (GET: read) |
| `GET /transfers/candidates`, `POST /transfers` ([own transfers](#own-transfers)) | manage |
| `GET /bills/upcoming?days=`, `GET /bills/suggestions` | `finance:read` |
| `GET /bill-schedule`, `/payment-candidates`, `/debt-payments`, `/debt-plan`, `/budgets` | `finance:read` |
| `POST, DELETE /bills/{id}/matches`, `/debts/{id}/payments`; `PUT /debt-plan`, `/budgets/alerts` | `finance:manage` |
| `GET, POST /bills`, `/debts`, `/goals`, `/holdings`; `GET, PATCH, DELETE /<kind>/{id}` | read for GET, manage otherwise |
| `GET /safe-to-spend`, `GET /plan`; `PUT /plan` | read; manage |
| `GET /advisor`, `GET /advisor/notes`, `GET /advisor/notes/{id}`, `GET /advisor/settings`; `POST /advisor/generate`, `PUT /advisor/settings` ([Money advisor](#money-advisor)) | read; manage |
| `GET /guardian`, `GET /guardian/versions?limit=&offset=`, `GET /guardian/alerts?month=`, `GET /guardian/changes?status=`, `POST /guardian/evaluate`, `GET /guardian/review?month=&state=&limit=&offset=`, `GET /guardian/activity?limit=&cursor=` | `finance:read` |
| `POST /guardian/changes`, `POST /guardian/changes/{id}/confirm`, `POST /guardian/changes/{id}/reject`, `POST /guardian/review/{transaction_id}` | `finance:manage` |
| `GET /connectors`, `GET /connections`, `GET /connections/{id}`, `GET /fx-rates?limit=` | `finance:read` |
| `POST /connections`, `PATCH /connections/{id}`, `POST /connections/{id}/sync`, `POST /connections/{id}/disconnect`, `DELETE /connections/{id}`, `POST /fx-rates`, `DELETE /fx-rates/{id}` | `finance:manage` |
| `GET /prices?symbol=&limit=`, `GET /paper/portfolios`, `GET /paper/portfolios/{id}`, `GET /paper/portfolios/{id}/orders?status=&run=&limit=&offset=` | `finance:read` |
| `POST /prices`, `DELETE /prices/{id}`, `POST /paper/portfolios`, `PATCH, DELETE /paper/portfolios/{id}`, `POST /paper/portfolios/{id}/reset`, `POST /paper/portfolios/{id}/check`, `POST /paper/orders/{id}/cancel` | `finance:manage` |
| `GET /execution/status`, `GET /execution/intents?status=&mode=&limit=&offset=`, `GET /execution/intents/{id}`, `GET /execution/live`, `GET /execution/wider` | `finance:read` |
| `POST /execution/intents`, `POST /execution/intents/{id}/approve`, `POST /execution/intents/{id}/reject`, `POST /execution/live/changes`, `POST /execution/live/opt-out`, `POST /execution/wider/widenings`, `POST /execution/wider/widenings/{id}/approve`, `POST /execution/wider/widenings/{id}/reject`, `POST /execution/wider/review` | `finance:manage` (only the request's owner; live requests always 409) |
| `PUT /execution/kill-switch` | `finance:manage`; `scope: global` also needs a workspace owner or an organisation administrator |
| `POST /broker/readiness` ([live eligibility preflight](#live-eligibility-preflight)), `POST /broker/statement-preview`, `/broker/reconcile`, `/broker/analyze`, `/broker/history`, `/broker/paper/simulate` | `finance:read` (read only: nothing is saved except the preflight's audit row) |

## Permissions

`finance:read` and `finance:manage` (`anum_api/authorization.py`). Owners and members have both; viewers have neither, and the page tells them to ask an owner to change their role. Because every row is private to its owner, the permissions only decide whether a role may use Finance at all: a workspace owner cannot read a member's ledger.

## Assistant

Read-only tools (`finance/tools.py`, registered in the API's and the Temporal worker's registries by `register_finance_tools`), each with an `input_schema`, `action_kind="read"` and `RiskLevel.LOW`, on the caller's own ledger only:

- `finance.summary` (`month`)
- `finance.transactions.search` (`query`, `category`, `from`, `to`, `direction`, `limit`)
- `finance.bills.upcoming` (`days`)
- `finance.safe_to_spend` (no arguments): the figure per currency, line totals, the per-day figure and, when on, the combined total; always "an estimate, not advice"
- `finance.guardian.rules`: the rules in plain words, the version, this month's alerts and how many changes wait for the person
- `finance.guardian.check` (`amount`, `currency`, `category`, `merchant`, `kind`): a dry run through `gate`, as an assistant's request (so a money-moving kind always needs the person); it says nothing was done

One tool writes, and only a proposal: `finance.guardian.propose` (`action_kind="create"`, `RiskLevel.LOW`, `edit`: a category cap, a blocked merchant or category, a single or daily limit, an approval amount). It applies the edit to the current rules and stores a pending change marked `agent`; a change that would loosen anything is refused ("Only you can loosen your own rules"). There is no tool that confirms, rejects or deletes rules, connects a source, reads a credential, or pays, transfers or trades: confirming is only the person's own `POST .../confirm` with the hash they were shown.

Stages 6 to 9 add one read tool and two proposal tools: `finance.paper.portfolios` (read: the SIMULATED portfolios, cash, positions, P&L and the assumptions, "not real trades and no promise of returns"), `finance.paper.propose` (`action_kind="create"`: a paper or shadow trade request marked `agent`, checked by the Guardian gate, which adds "an assistant asked for this: you approve it yourself"; `mode: live` is refused) and `finance.live.widening.propose` (`action_kind="create"`: the next wider-live tier, refused while wider live use is off). No tool approves, executes, turns live on, sets live limits or changes a kill switch. The planner proposes a simulated order only when the user's own words say paper, simulated, shadow or تجريبي ("paper buy 10 EXMPL", "اشتر 10 EXMPL تجريبي"); "show my paper portfolio" and "المحفظة التجريبية" read the portfolios; any other buy, sell, pay or transfer still goes to `anum.external-action`. The skill's triggers add paper, shadow, simulate, simulated, simulation, portfolio and, in Arabic, تجريبي, تجريبيا, تجريبية, محاكاة, محفظة, المحفظة, محفظتي.

Fund research adds the read tool `finance.research` (`symbol`, or none for the owner's IBKR portfolio as IBKR last reported it): text built from the gathered data only, never a free model answer, so no number outside the data reaches the reply ([Fund research](finance-research.md#assistant)). "research SPUS" and "how is my portfolio doing" ask it. Not financial advice; no guaranteed returns.

The `anum.finance` skill (`agent_skills.py`) is selected by words such as spent, spending, budget, bills, balance, debts, transactions, afford, rules, cap, limit, block, guardian and, in Arabic, مصاريف, فواتير, رصيد, صرفت, ميزانية, ديون, أصرف, متاح, قواعد, حدود. Its planner (`finance.tools.plan_call`) reads only the user's own words (threat model G5): "what did I spend this month" asks `finance.summary`, "how much did I spend on groceries in September" and "transactions at Carrefour" search, "bills due this week" lists bills, "how much can I spend this month" and "كم أقدر أصرف هذا الشهر" ask `finance.safe_to_spend`, "show my spending rules" and "ما هي قواعد الصرف" read the rules, "cap dining at 600", "block payments to talabat", "never spend more than 2000 at once" and "no more than 300 a day" propose a stricter rule. Pay, transfer, send, buy, sell and "spend 200 on ..." return nothing and fall through to `anum.external-action`.

Tool output is untrusted data (descriptions come from statements): in chat it reaches the model only through `tool_output_prompt_block` after the untrusted-data rules (`chat_tools.summary_messages`, category `finance`), and voice grounding labels it the same way. The output carries a note that descriptions are data, not instructions.

## Clients

- **Web** (`#finance`, tabs deep-linked as `#finance-safe`, `#finance-transactions`, `#finance-import`, `#finance-bills`, `#finance-guardian`, `#finance-connections` and so on, in the browser history): the summary band, a safe-to-spend card, the month's spend by category as an SVG chart with a table fallback, income and spending, bills due, balances and goals; **Safe to spend** with the build per currency as a table (money you have, bills, minimum payments, goals, buffer), the per-day figure, what is not counted and why, the optional total with each conversion's rate, date and source, and the buffer and currency settings; transactions with search, filters, paging, edit, delete and CSV export (a record that breaks a rule is saved and the rule named); accounts; the import (CSV or PDF) with a mapping preview, the PDF review with its balance checks, taught layouts and undo; bills with suggestions; debts, goals (with a monthly amount) and holdings (see [Holdings](#holdings)); **Guardian** with the rules in plain words, changes waiting for review (proposed by you or an assistant, with what they loosen) confirmed with the code shown or rejected, the rules editor with a review step, this month's alerts, "Check a spend" and the version history; **Connections** with your connections (status, last and next sync, last error, Sync now, Disconnect, Delete), the sources (read-only scopes, terms, and the owner action for sources not built, with no Connect button) and the exchange rates (filter, type a rate, delete a typed one); settings with category rules; **Paper trading** (`#finance-trading`, see [Paper trading lab](#paper-trading-lab); the full panel below is the Paper orders tab, `#finance-trading-orders`) with the SIMULATED note and assumptions, requests waiting for approval (an assistant's marked), portfolios (create, rename, change costs, reset, delete), positions with recorded prices and simulated P&L, the shadow comparison, a new order checked by the rules and reviewed with its code before it runs, the order history (status, this run or every run, paging, cancel a waiting order) and the recorded prices (record, filter, delete); **Execution and live** (`#finance-execution`) with both kill switches, how a request runs, limited live (off: the preconditions, your live limits changed only with your approval, opt-out, what the owner must provide), wider live use (off: the measured checklist, ceilings, step proposals reviewed with their code, incidents, review to unfreeze) and every request with its status. Loading, empty with a next action, error with a retry and permission denied are handled on every tab, in English and Arabic.
- **Flutter** (`apps/mobile/lib/features/finance/`, opened from the menu or a Home pin, with an AppBar back button): the month's summary with spend by category, upcoming bills and balances; safe to spend line by line per currency; the Guardian rules, this month's alerts and changes waiting for you, confirmed (with the code) or rejected; connections with their status and Sync now; transactions with search and paging; accounts, bills, debts and goals. Paper trading shows the SIMULATED portfolios and requests waiting for approval, approved (with the code) or rejected; Execution and live shows the person's kill switch (switchable), the organisation's, limited live (off, with its preconditions) and wider live use (off, the checklist, review after an incident). Personal money on the phone: the **Money planner** (a question sent through the Assistant's finance skill, the answer with "Advice only" and its live sources as links), **Lent and borrowed** (totals per direction and currency, overdue badges, add an entry, record a repayment by linking a recorded transaction), **Fixed costs** (read only) and the avalanche against snowball card on Debts (`GET /planner/payoff`); a loan reminder opens Lent and borrowed ([Mobile app features](mobile-features.md#personal-money)). On the phone you add accounts, transactions and bills and delete any record; statement import, editing, holdings, editing rules, the buffer, connecting sources, placing paper orders, recording prices, live limits and widening steps are on the web. English and Arabic.

### Finance shell

Every Finance page uses the same shell (`apps/web/src/FinanceShell.tsx`, styles `financeShell.css`, model `lib/financeNav.ts`), from the owner's mockups of 5 Nov 2026. In the app sidebar the life modules (Vehicles, Document vault, Attendance, Purchases) sit above Finance; while Finance is open its sections appear under it.

- `FinanceSectionNav({ tab, onPick })`: the sections as one `<nav aria-label="Finance sections">`. On screens wider than 760 px it renders into the sidebar slot `#nav-sub-finance` (Shell.tsx), so Finance reads as an expanded group with the current section as a filled green pill; on phones it is a scrolling strip under the head. Groups (`FINANCE_NAV`): Finance (Overview, Transactions, Accounts, Bills, Budgets, Debts, Goals, Personal money, Reports, Safe to spend, Money advisor, Guardian), data capture (Connections, iPhone SMS, Import, Ledger), Holdings and Research, Trading (Paper trading, Trading intelligence, Execution and live, IBKR trading), then Business Expenses and P&L (a link to `#business-books`, NEW badge). An entry exists only for a real tab: the list is filtered by `FINANCE_TABS`. Budgets opens Bills and budgets on its Budgets tab ([Bills and budgets](#bills-and-budgets)). Personal money is one entry for the page with its own tabs (Lent and borrowed, Payslips, Send home, Fixed costs): it opens Lent and borrowed and stays current on all four (`also` on the entry, `isCurrent`). Sub-menu rows are 28 px so Finance and its last section (IBKR trading) fit together in a 900 px window.
- `FinancePageHead({ title, subtitle?, actions?, controls?, tabs?, crumbs? })`: the large title, a one-line subtitle, the primary actions on the end side (the main one a filled button), the controls row and the tabs. A section that draws it goes into `OWN_HEAD_TABS`; Finance then leaves out its generic header and summary band for that section.
- `ScopeSwitch({ value, onChange, options?, unavailable? })`: Personal (your own ledger, `/api/v1/finance`), Business (the workspace books, `/api/v1/business/books`) and All finance. `unavailable` disables an option and says why.
- `PeriodPicker({ month, onChange, max?, min? })`: previous, the month, next.
- `FinanceTabs({ label, items, value, onChange, idPrefix })` with `tabPanel(idPrefix, value)` for the panel: WAI-ARIA tabs with the green underline and arrow keys (mirrored in Arabic).
- `KpiRow({ label })` with `KpiCard({ icon, label, value, hint?, delta?, deltaText?, tone? })` and `DeltaChip`: `delta(current, previous, higherIsBetter)` from `lib/financeNav.ts` gives the direction, the percentage (none from a zero base, never an invented one) and green or red.
- `FinCard({ title, icon?, subtitle?, actions? })`, `FinBanner({ tone: 'warn' | 'ok' | 'info', title?, action? })` and `StatusChip({ tone })`, plus the `finGrid` table class (light header row) inside `<div className="finTableWrap" tabIndex={0}>` (focusable, so a keyboard can scroll a wide table).

Props only gain optional members, so pages built on the shell keep working. Labels are in `lib/messages/financeShell.{en,ar}.ts`.

### Reports

`#finance-reports` (`apps/web/src/FinanceReportsPage.tsx`, figures in `lib/financeStatements.ts`, charts in `FinanceCharts.tsx`, all lazy chunks). Scope Personal reads six month summaries (`GET /finance/summary?month=`), Business the business books report for the month and the month before (`GET /business/books/report?from&to`); a person who cannot read the books sees Business disabled with the reason and the personal report still works. Views:

- **Profit and loss**: income, expenses, net result and savings rate (personal) or operating margin (business) with the change from last month; income and expenses as bars with the net result as a line over six months (a legend, a hover tooltip and the same figures as a table); the three largest category changes; spending by category this month and last with the change and share; the business P&L card linking to Business Expenses and P&L; report quality (share of spending with a real category, transactions counted; for the books, allocations in review, payments without a tax split, unconverted amounts); business against personal; what the reports leave out.
- **Cash flow** (money in, out and net per month; the books' cash lines), **Spending** (each category's share), **Net worth** (account balances less tracked debts per currency; the books' receivables, payables and loans), **Tax summary** (the books' tax per currency; personal finance records no tax and says so) and **Custom** (any dates, account and category, the number of matching transactions and their CSV).
- Downloads are the API's own exports: the transactions CSV (`/finance/transactions/export.csv`) and the books' P&L CSV (`/business/books/report.csv`). Sums and differences are done in thousandths, never in floating point and never across currencies.
- Not built: saving a report, scheduled exports and a PDF (the mockup's buttons), since no API stores or schedules finance reports.

### Safe to spend page

`#finance-safe` (`apps/web/src/FinanceSafePage.tsx`): the estimate banner; account balance, bills and minimum payments, goals and buffer and the estimated safe amount; the transparent calculation (balance less bills less goals and buffer equals the estimate, from `GET /finance/safe-to-spend`); the runway to the month end (start from the balance after goals and buffer, each bill taken off on its due day from `GET /finance/bills/upcoming`, and the average daily spend of the last 30 days from two month summaries; the band is that pace 10% higher or lower, stated as an assumption); review and assumptions with Edit assumptions (the buffer and currency settings) and Refresh; upcoming commitments; spending guardrails (the Guardian monthly category limits against this month's spending); a stale notice when the newest transaction is more than three days old, with Import a statement; the Guardian rules in words with Review rules and Open Guardian; the full build per currency underneath. Business scope says the estimate is personal (the books hold no bank balances) and shows the business budgets and their use. Rules shows the Guardian rules and this month's alerts. Not built from the mockup: a scenario planner, rule toggles on this page (rules change only through the Guardian review with its code) and per-account selection (the API counts every bank and cash account).

### Guardian page

`#finance-guardian` (`apps/web/src/FinanceGuardian.tsx`, helpers in `lib/financeGuardian.ts`, a lazy chunk; in `OWN_HEAD_TABS`): the head with Scope (Personal only: Guardian checks the personal ledger, so Business and All are shown unavailable with that reason), Period and the Monitoring switch (Enabled, Paused or No rules) with Pause or Resume monitoring; KPIs (alerts this month, rules enabled, items resolved, awaiting review); the monitoring rules (each kind set, with its switch, trigger text, last triggered, threshold, Edit, Test and Pause, every change confirmed with its payload hash); the [review queue](#review-queue) with bounded paging; this month's near and over cap alerts; privacy and boundaries; delivery as built (see [Notifications](#notifications)); quiet hours with the time zone; and the person's own audit log. `FinanceGuardianPreview.tsx` exports `GuardianRulesPreview({ onOpen, max })` for other Finance pages.

## Owner actions

- **Exchange rates:** connect the ECB reference rates under Finance, Connections (no account needed). For background refresh set `ANUM_FINANCE_SYNC_ENABLED=true` and allow HTTPS egress to `www.ecb.europa.eu`.
- **Bank and card accounts:** a contract with a provider licensed for UAE Open Finance (Al Tareq), its sandbox and production credentials and API documentation. Then an adapter is built and tested against it; until then, statements come in as CSV.
- **Broker:** choose the broker; an API key with no trading or withdrawal permission and the API's documentation.
- **Crypto exchange:** choose the exchange; a read-only API key (trading and withdrawals off).
- **Market prices:** a market data provider whose licence allows storing prices for personal use, and an API key. Until then paper orders use prices the person records.
- **Live execution (stages 8 and 9):** see [What the owner must provide before live](#what-the-owner-must-provide-before-live). Nothing can be switched on without it.

## Holdings

Finance, Holdings (`#finance-holdings`, `apps/web/src/FinanceHoldings.tsx`, a lazy chunk with its charts in `FinanceCharts.tsx`; pure valuation in `apps/web/src/lib/financeHoldings.ts`, chart geometry in `lib/financeCharts.ts`) values the person's manual holdings with the prices they record (`GET /prices?symbol=`, the same `finance_prices` the paper simulator uses). No new API: it reads `/accounts`, `/holdings` and `/prices`, and writes only through `POST, PATCH, DELETE /holdings`, `POST /prices` and `DELETE /prices/{id}`.

- **Controls**: portfolio (all investment accounts or one), currency (only when holdings are in more than one; figures are never converted or summed across currencies), period for the value line (3, 6 or 12 months, or every recorded price) and Add holding.
- **Summary**: portfolio value (positions with a price in their own currency), change since each position's previous recorded price (there is no daily price feed, so this is not a "day change"), unrealised gain against the cost of the priced positions, cash (the balance of the investment accounts in that currency); the value line (today's quantities at each recorded price, a day before some position has any price is a gap, hover or arrow keys for a crosshair, a table view); allocation as a donut with a legend (five fixed series colours checked for colour blindness, the rest folded into Other); the top five positions; recent activity (positions added or edited and prices recorded, all marked Manual); data quality (positions without a price, prices older than 3 days, the oldest price) with Review prices and Record a price; the not-advice notice.
- **Positions**: search by symbol, name or account, sort by asset, quantity, value or gain, 25 a page, a sparkline of the last 12 recorded prices, and per row Record a price, Edit and Delete (with a confirmation).
- **Performance**: the figures, a taller value line and the gain of each position against what was paid.
- **Valuation settings**: how values are worked out, the latest price of each position with Record a price, and the recorded prices of held symbols (delete with a confirmation).
- Not shown because ANUM has no source for them: allocation targets and drift, corporate actions (splits, dividends), a broker-reconciled audit status, a Personal or Business scope (holdings belong to the person), and a market-price day change.

## Paper trading lab

Finance, Paper trading (`#finance-trading`, `apps/web/src/FinanceLab.tsx`, a lazy chunk; pure helpers in `apps/web/src/lib/financeLab.ts`) is one page for SIMULATED trading, drawn with the Finance shell. It adds no API and changes no safety rule: every write goes through the existing endpoints, every paper order still runs only after the person approves the payload hash shown, and live trading stays locked exactly as [Live trading with IBKR](finance-live-trading.md) and [readiness](finance-trading-readiness.md) say.

- **Head**: title, the portfolio picker (paper and shadow portfolios), the amber **PAPER MODE · SIMULATED ORDERS ONLY** banner and the tabs, deep-linked as `#finance-trading-<tab>`: Overview, Holdings (the selected portfolio's simulated positions), Research (fund research), Strategies (trading intelligence), Paper orders (portfolios, the order ticket, history and recorded prices, as before) and Broker connections (IBKR).
- **Overview**: figures (simulated value = simulated cash plus positions at recorded prices, unknown while a position has no fresh price; paper return against the starting cash; open simulated positions; strategies paper active and paper suspended from `GET /intel/overview`); the SIMULATED P&L line against buy and hold of the same symbols (`GET /intel/portfolios/{id}/equity`, one axis, a legend, a table view; the full charts with drawdown stay under Strategies); allocation; the latest simulated orders with sortable columns (a page of the newest ten) and **New simulated order**, which opens the existing ticket and then the review with its code; the research brief for the first fund on the research list (its checked AI summary, unavailable sources, evidence links, the disclaimer); broker status from `GET /ibkr/overview` with Connect or Manage (the Broker connections tab) and **Test connection** (`POST /ibkr/connections/{id}/check`, a read of the session; owner only, as before), or the built-in simulator; the strategy lab (five newest strategies by lifecycle state); risk caps (the Guardian rules in words, read only, with a link to edit them in Guardian); the kill switches (the same component and confirmation as Execution); the newest backtest or walk-forward run with its metrics and benchmark and the "past simulation is not a guarantee" note; **Live execution** locked, listing the real IBKR live gates met and unmet; links to the request history (Execution) and Guardian.
- Not shown because ANUM has no data for them: a Personal or Business scope (trading is the person's own), a strategy backtest curve (runs store metrics, not a curve), and a market-data timestamp beyond the research report's own dates.

## Paper and shadow trading

Stage 6 (`finance/trading.py`). **SIMULATED**: nothing is bought, sold, paid or sent, and no real account is used. Every screen, tool answer and result says so, with the assumptions used, and says nothing certain about what a real order would earn or lose.

- **Portfolios.** A **paper** portfolio has simulated starting cash per currency; a **shadow** portfolio has no cash and records what each order would have done. Up to 20 per person. Each has its own fee (basis points of the order's value, with a minimum in the order's currency), slippage (basis points against the order: a buy pays more, a sell gets less) and the oldest price it uses (1 to 30 days, default 3). New costs apply to later fills only.
- **Prices, never invented.** A security fills only at the newest price the person recorded for that symbol and currency (`POST /prices`, source `manual`, within the last year, never a future price) within the portfolio's age limit. A currency (instrument `fx`) fills at the newest recorded exchange rate (stage 5), direct, inverse or across a shared base. With no price a **market** order is refused with the reason ("record a price, or place a limit order"); a **limit** order waits, and is tried again whenever a price is recorded or on **Check waiting orders**.
- **Fills.** Fill price = recorded price moved by the slippage; value = quantity x fill price, rounded once to the currency; fee = the larger of the fee rate and the minimum. A limit buy fills only at or below its limit, a sell at or above. Paper checks the simulated cash (value plus fee) and units held (no short selling) at fill time; shadow has no cash and never refuses for it.
- **Book.** Cash, positions and P&L are replayed from the filled orders of the current run, average cost: realised P&L on each sale (proceeds after fee minus the cost of the units sold), unrealised P&L at the newest recorded price (a position without a recent price is listed as not valued). Fees and slippage paid are shown per currency. **Reset** starts a new run (cash back to the start or new starting cash, waiting orders cancelled); earlier runs stay in the history.
- **Shadow comparison.** Each filled shadow order against the newest recorded price: a buy gains when the price rose, a sell when it fell, fees taken off, with how many days ago it was placed and a total per currency.
- **Agents** may only propose paper or shadow orders; the person approves them like their own.

## Execution gateway

Stage 7 (`finance/gateway.py`). The one governed path for any finance action that would move money or trade. Every request goes through it in this order and stops at the first refusal:

1. **Intent.** `POST /execution/intents` with the mode, the exact request and an optional idempotency key (the same key returns the same request; a different request with it is refused). A `live` request is refused here, before anything else.
2. **Kill switches.** The organisation's or the person's switch stops every executor, simulated ones too.
3. **Guardian.** The same `gate` as stage 4, on the order's value (estimate from the recorded price, or the limit), with the symbol as the merchant; for paper and shadow the kernel adds only `simulated_only`, so the person sees exactly which of their rules would block the real action. **Deny stops**: the request is stored as `denied` with the reasons and cannot be approved. A cooling-off reason keeps the approval open past its end.
4. **Approval bound to the payload hash.** The same binding rule as ANUM's approvals (`approval_integrity.py`): SHA-256 of the canonical JSON of the id, owner, mode, kind, portfolio, instrument, currency, side, order type, quantity, limit, amount, account and idempotency key. The person approves with the hash they were shown; the gateway recomputes it from the stored request, so any change invalidates it. It expires after `ANUM_FINANCE_APPROVAL_MINUTES` (default 30), is used once (a compare-and-set claim from `pending_approval` to `executing`), only the owner can approve (the store, and a database check on the approver), and there is no agent tool for it. At approval the kill switches and the Guardian gate are checked again with the current rules and price: a rule added since blocks it. Finance approvals live in the owner-only `finance_exec_intents` table rather than the workspace approvals queue, because that queue is routed to workspace approvers and would show one person's private finance to others.
5. **Executor.** `paper` and `shadow` (stage 6) are the only executors. The request id is the idempotency key: one simulated order per request, ever (unique in the database).
6. **Result.** Stored on the request and audited (`finance.exec.intent.create`, `.approve`, `.reject`, `.refused`, with modes, statuses and rule codes only; refusals with `outcome: denied`).

**Kill switches** (`PUT /execution/kill-switch`): the person's own, and the organisation's (owners and organisation administrators). Both audited (`finance.exec.kill_switch.person`, `.global`).

## Limited live

Stage 8 (`finance/live.py`): **the generic framework, shipped off.** The one live executor is the IBKR one, which has its own gates, limits, approvals and kill switch: see [Live trading with Interactive Brokers](finance-live-trading.md). Everything below describes the generic path, which still refuses every live request.

- `LiveExecutor` is an interface with **no implementation and no provider adapter**: `LIVE_EXECUTORS` is empty. Connectors stay read only (the stage 5 database check is unchanged).
- `ANUM_FINANCE_LIVE_ENABLED` defaults to false; the API refuses to start with it on while no live executor exists, and the Helm chart refuses it too (`infra/helm/ci/lint.sh`).
- The person's **live limits** (`LiveLimits`: at most per action, per day and per month per currency, allowed currencies, instruments and accounts, a cooling-off of 1 to 168 hours; empty means nothing is allowed) change only through a request of mode `settings` that the person approves with its payload hash (`POST /execution/live/changes`). **Opting in** needs the same approval and is refused while live is not enabled; **opting out** is immediate. `check_live_limits` is the pure check a live executor would have to pass.
- `GET /execution/live` lists every **precondition**: the deployment flag, a built and reviewed live executor, a write-scoped connector, the person's opt-in, their limits, both kill switches off. Today the first three never hold, and every live request is refused with "Live execution is not enabled" (tested with the flag forced on and the person opted in).

### What the owner must provide before live

Nothing below exists; each item needs the owner, and the code needs a separate, reviewed change:

1. A provider account with a licensed broker or bank in the owner's name, and its API documentation.
2. An API key for that provider with exactly the write permission needed (no withdrawals), stored encrypted with `ANUM_SECRETS_KEY`, never shown to an assistant.
3. A live executor built and reviewed against that provider's documented API, with its own tests, and a contract migration that lifts the database's no-live check.
4. The owner's live limits: per action, per day, per month, currencies, and the instruments or accounts allowed.
5. The owner's explicit written approval to switch on bounded live actions, recorded before the operator sets `ANUM_FINANCE_LIVE_ENABLED`.

## Live eligibility preflight

One place answers "what is still missing before live trading could ever be considered": `POST /api/v1/finance/broker/readiness` (`finance/broker_readiness.py`), shown on Finance, Execution as **Live eligibility check** (English and Arabic). It merges the stage 8 preconditions above with the broker onboarding checklist ([Broker onboarding](finance-broker-onboarding.md)), and replaces the separate preflight endpoint proposed in PR #237.

- **Input:** the person's own ticks in three groups (broker, account and data; research and paper testing; live review), and optionally a profile: the broker (Alpaca, Interactive Brokers or another), the two-letter country of residence and the assets (stocks, crypto). No account number, key or password is accepted: unknown fields are refused (422).
- **Answer:** the missing items per group and a stage (`not_connected`, `read_only`, `live_blocked`); the person's **own live preconditions read from the database** (`gateway.preconditions`: deployment flag, live executor, write connector, opt-in, limits, both kill switches), so it reads only the caller's rows under RLS; informational notes. It always says `live_trading_enabled: false`, `eligibility_confirmed: false`, `credentials_connected: false`, `deposits_into_anum_supported: false` and `orders_sent: 0`, and the response model cannot hold any other value. A fully ticked list still leaves live off, because ANUM has no live executor.
- **Read only:** it changes no setting, connects nothing, reads no key and sends no order. The only write is an audit row `finance.broker.live_preflight` with the actor, tenant, workspace and the person's id as target, and empty metadata: no country, assets or ticks are logged.
- **Permission:** `finance:read` (owners and members); viewers get 403 with who can change it.
- **Saudi residents and crypto** (informational, never a gate it can lift): when the country is `SA` and crypto is chosen, the answer adds a note with its source. Saudi Arabia's standing committee on virtual currencies, which includes the Saudi Central Bank (SAMA) and the Capital Market Authority (CMA), stated on 12 August 2018 that virtual currencies are not regulated in the Kingdom and that no party is licensed by the regulators to deal in them ([CMA statement](https://cma.gov.sa/en/MediaCenter/PR/Pages/Virtual_Currencies.aspx), [SAMA statement](https://www.sama.gov.sa/en-US/News/Pages/news12082018.aspx)). This is the most recent official statement found on 2 November 2026; re-check it, the broker's own terms for Saudi residents and legal advice before any crypto.
- Tests: `services/api/tests/test_finance_live_preflight.py`, `test_finance_broker_readiness.py`, `test_postgres_finance_live_preflight.py` (RLS as the app role, audit rows), `apps/web/test/financePreflight.test.ts`, `apps/web/e2e/finance-live-preflight.spec.ts` (main path, Saudi note, every ticked, client checks, error with retry, denied, Arabic, the responsive gate at five sizes).

## Wider live

Stage 9 (`finance/wider.py`, `finance/wider_api.py`): **the framework only, shipped off.** Wider live use could only follow a limited stage that has proved itself; nothing here runs live.

- `ANUM_FINANCE_WIDER_LIVE_ENABLED` defaults to false and is refused unless `ANUM_FINANCE_LIVE_ENABLED` is on (itself refused); the Helm chart refuses it too. While it is off every proposal and approval is refused ("Wider live use is not enabled").
- **Graduation** (`GET /execution/wider`), computed on the server from the stage 7 and 8 records over `ANUM_FINANCE_WIDER_WINDOW_DAYS` (default 30): every stage 8 precondition met, at least `ANUM_FINANCE_WIDER_MIN_DAYS` (30) since the live opt-in, at least `ANUM_FINANCE_WIDER_MIN_ACTIONS` (20) executed live actions, no Guardian denial overridden, no failed execution or reconciliation, no kill switch turned on, not frozen. Shown as a checklist with each value and what is needed. Today it is never met (no live action can run).
- **Widening** is a proposal by the person (`POST /execution/wider/widenings`) or an assistant (`finance.live.widening.propose`) to move **exactly one tier** up: each limit at most `ANUM_FINANCE_WIDER_STEP_PERCENT` (50) percent of the stage 8 limits above the last tier, at most `ANUM_FINANCE_WIDER_MAX_TIER` (3, hard maximum 5) tiers, no new currency, no shorter cooling-off, at most one new action type and none removed. The owner approves it with its payload hash, once, within 24 hours (`.../approve`); it takes effect only after `ANUM_FINANCE_WIDER_COOLING_HOURS` (72). An assistant can never approve, and a widening never changes the Guardian rules: loosening a rule is only ever the person's own Guardian change, reviewed with its code.
- **Automatic step down.** A kill switch turned on, a failed execution, or three Guardian denials within a day is an incident: the tier drops one step, an approved step still cooling off is cancelled, and widening is frozen until the owner reviews it (`POST /execution/wider/review`). Incidents are checked whenever wider live use is read, proposed or decided, and audited (`finance.wider.step_down`).
- The execution gateway still refuses every live request whatever the tier (tested with both flags forced on and a tier above zero).

### Operator settings, stages 7 to 9

| Setting | Default | What it does |
|---|---|---|
| `ANUM_FINANCE_APPROVAL_MINUTES` | `30` | How long an approval of an execution request stays valid (1 to 1440) |
| `ANUM_FINANCE_LIVE_ENABLED` | `false` | Refused at startup while no live executor exists |
| `ANUM_FINANCE_WIDER_LIVE_ENABLED` | `false` | Refused unless the stage 8 flag is on |
| `ANUM_FINANCE_WIDER_WINDOW_DAYS`, `_MIN_DAYS`, `_MIN_ACTIONS` | `30`, `30`, `20` | The graduation window and proof |
| `ANUM_FINANCE_WIDER_MAX_TIER`, `_STEP_PERCENT`, `_COOLING_HOURS` | `3`, `50`, `72` | Hard ceilings and the cooling-off |

## Not built yet

Stages 2 to 7 are built; stages 8 and 9 are built as frameworks and **shipped off**. Not built, by design:

- A live executor for any provider other than IBKR. The IBKR executor is built ([live trading](finance-live-trading.md)); wider live (stage 9) still cannot run.
- Adapters for bank, card, broker, crypto and market-price providers (see [Owner actions](#owner-actions)); a cross-currency "net worth"; OFX statement import; receipt attachments in the Flutter app (the web links receipts from the vault, [Register and account overview](#register-and-account-overview)); Flutter statement import, rule editing, connecting sources, placing paper orders and setting live limits; finance cards with item rows in chat (the chat answers in text above a summary card).


## Installment statement import, reimport, and purchase dashboard

Finance > Bills > **Import CSV** accepts a provider installment statement with due date, amount, currency, merchant, source status, provider, order reference, and installment number. Extra supported columns are purchase date, total installments, source email, and review notes. Column mapping can be corrected before import.

- **Full private statement history:** `GET /api/v1/finance/installment-statement` returns the signed-in user's own saved rows, including provider-reported paid and refund/review entries. They persist inside the owner's `finance_plans.installment_entries` JSONB with the existing owner-specific RLS. Migration: `0144_finance_installment_statement`.
- **Idempotent statement syncing:** `POST /api/v1/finance/installment-statement/sync` upserts by provider, order reference, and installment number. Reimporting the same file leaves existing source history unchanged. A changed amount or due date is reported as a conflict and the prior financial terms are retained until reviewed. One exception: the same installment and due date with a **lower** amount is Tamara's own new schedule after a refund. It is not a conflict: the original amount stays, the lower figure is kept on the row (`reported_amount`) and listed in the result's `refund_signals`, and [Tamara refunds](#tamara-refunds) applies it on the owner's confirmation. A refund the owner applied (`refunded_amount`) survives every reimport, and an upload can never set either field. Source-status changes are saved. An upload that lists the same provider order installment twice, or an installment number above the plan length, is refused as a whole (422) and nothing is saved.
- **Bills are payment obligations only:** New scheduled installments become one-time Finance Bills. Existing bills are checked by source order and installment; they are not duplicated. CSV-reported paid or refund-review rows do not become new payable bills. If a previously scheduled existing bill is subsequently reported paid or under review, the importer pauses it instead of fabricating a bank payment. Human-paused bills remain paused.
- **Verified versus provider-reported payment:** The full summary includes all purchases, installment counts, source-reported paid amounts, scheduled outstanding and refund-review amounts. Independently **verified** payment matches are shown separately and may override the provider status for displayed outstanding calculations. Only the Finance ledger's matched payment links count as verified bank payments.
- **Source provenance:** Saved statement rows preserve the imported source email link and review notes. The full dashboard allows purchase search, completed/open/review filtering, order drill-down, monthly due totals and CSV export.
- **Money and privacy:** No import charges cards or creates canonical bank transactions, and no personal statement is committed to the repository. Source email links and notes belong only to the signed-in owner. Amounts under refund/dispute review are excluded from payable totals, and original statement amounts are not represented as confirmed refunds or invoice totals.

**Known limitations:** Provider email status may lag real payment activity. A final bank/Tamara reconciliation is needed for disputed refunds and reported paid amounts. The source CSV describes merchants/orders, not the actual item-level receipts, unless receipts are supplied separately. A new installment record may be due even if it is not yet matched to a real payment.


### Gmail receipts alongside manual and CSV import

Finance > Bills now presents **Sync Gmail**, **Import CSV**, and **Add Bill** independently. Manual Bills are never deleted when Gmail is connected or disconnected. Gmail integration reuses the ANUM Email module's existing Gmail OAuth connection (ANUM does not reuse the ChatGPT Gmail connection).

1. Connect the intended Gmail account in **ANUM Email**, preferably as **Private**. Finance prefers `shahzeb.rahmanllc@gmail.com` when that mailbox is among the authenticated user's own connections; it never assumes this Gmail identity is already authorized.
2. In Finance > Bills > **Sync Gmail**, select the mailbox, refresh ANUM Email's existing incremental sync, and preview Tamara messages.
3. Finance's read-only preview endpoint `GET /api/v1/finance/installment-email/preview?account_id=...&limit=500` only allows Gmail mailboxes connected by the current user, requires both finance manage and email read permissions, reads relevant metadata from the existing inbox, and fetches bodies transiently through the provider. It does not send, archive, or mark mail read. Its mailbox and message reads run in a worker thread in their own short unit of work, never on the event loop ([Event loop](realtime.md#event-loop)). The mailbox list is `GET /api/v1/finance/installment-email/mailboxes`.
4. Parsing is **deterministic** and limited to Tamara sender addresses, order numbers, original due schedules and repayment confirmations. It flags amount mismatches for human review. A refund or order-updated email that names an amount, or says the whole order was refunded or cancelled, is returned as refund evidence (`refunds` in the preview) and the schedule stays as Tamara sent it; approving the import saves that evidence with the rows (`refund_notices` on the sync) for [Tamara refunds](#tamara-refunds). A read that finds only refund emails and no new schedule rows can still be approved: the sync then takes `entries: []` with the notices (an empty sync with neither is refused, 422). A refund email with neither still sets the order's unpaid installments to review. No model tokens or LLM provider are required.
5. The user explicitly approves the preview before its rows are stored using the same idempotent Finance statement sync API and before any future scheduled Bills are created. Provider-reported paid installments are preserved in statement history but not fabricated as bank transactions. Refund review cannot be automatically treated as money received.
6. Provider replies, invoices and SKU-level product details cannot be guaranteed from an order-only email. Original invoices/receipts must be separately scanned if SKU-level detail is required.
7. The Email module's normal background mailbox sync continues under its own schedule, but **Finance does not silently import or modify bill records on a background timer**: approve the Finance import for each new batch. This prevents accidental financial obligations from unreviewed email content.

The scanner checks at most 500 locally synchronized messages per manual preview and warns if it hits the limit. Older invoices absent from the local Email cache are not claimed as scanned; use CSV or the existing Email archive/backfill process to cover them. None of these endpoints require or publish OAuth tokens or Gmail bodies in the Finance response.

### Tamara refunds

When a Tamara order is refunded in full or in part, the installments the refund cancelled must stop being due everywhere in ANUM. Finance > Bills > **Refunds** (above the installment statement) finds the refund evidence for every order, shows the new schedule and saves it only when the owner presses **Apply refunds** and confirms. Nothing here moves money, marks a payment as received, or changes an email.

**Evidence, searched in three places and linked to its order** (`anum_api/finance/installment_refunds.py`):

| Source | Strong (can be applied) | Weak (Needs review, never applied) |
|---|---|---|
| Tamara refund or order-updated emails kept from an approved Gmail import (`finance_plans.installment_refund_notices`) | An email naming the order number and an amount, or saying the whole order was refunded or cancelled. Two emails with the same amount within 3 days are one refund, counted once | An email without an amount |
| The owner's imported Tamara statement | A row the statement marks refunded or cancelled (CSV status `refunded`, `cancelled`, `void`), or a lower amount a reimport reported for the same installment and due date | A review row whose notes mention a refund |
| Money-in transactions in the ledger that name Tamara | A credit naming the order number; a credit matching a refund email of that order (within 3 days before to 45 days after it), by its amount or as the paid share coming back (up to what was paid), which is shown as **money back received**, not as a second refund, even when it also names the order | A credit naming only the merchant, or matching only an amount, without the order number |

Each order lists the evidence used, its strength and a link back to the email (owner only).

**The arithmetic.** Tamara's rule is not the same for every merchant and region: Tamara's [Key Fact Statement](https://tamara.co/en-ae/key-fact-statement) says a refund smaller than the outstanding balance is applied proportionally to the upcoming installments; Amazon.ae and Amazon.sa's Tamara help shows a different example (an AED 1,200 plan with an AED 500 return keeps the 2nd installment and cancels the 3rd and 4th; a return equal to what is outstanding completes the plan; a larger one cancels the rest and the excess goes back to the card); and some merchants recalculate an unpaid plan to the new order value. So ANUM never guesses one:

1. **Tamara's own data wins.** When the statement (or a reimport) shows the new schedule, refunded rows or lower amounts, exactly that is applied. It is checked first, so a refund email whose amount covers everything unpaid never cancels installments the revised statement still shows as due (the difference is listed as a reason).
2. **Certain without a schedule:** a full refund, or refunds that add up to at least what was still unpaid, cancel every unpaid installment. Paid installments stay paid; the rest of the refund, up to what was paid, is **money back to your card**, shown as expected (and as received when a matching ledger credit exists). These can be applied with the one-click confirmation.
3. **A partial refund with no new schedule from Tamara** is shown as an **estimate** (the Key Fact Statement's proportional split over the upcoming installments, each share rounded to the minor unit and the last taking the remainder), labelled "confirm in the Tamara app", and the order stays in **Needs review**. An estimate is never applied, and never marks one installment cancelled.

Amounts are exact `Decimal` in the currency's minor unit; refunds in another currency are ignored. Refunds that add up to more than the order total, or less than what is already applied, put the order in Needs review and cannot be applied.

**Order status:** Active (no refund), Partly refunded (some still due), Refunded (nothing left to pay), Needs review (weak evidence or an estimate). The installment statement shows the same status per order, a Refunded filter and the refunded amount per installment and currency.

**Saving.** `GET /api/v1/finance/installment-refunds` returns every order's plan (evidence, old and new amount per installment, outstanding and next due before and after, money back, reasons) with a `plan_hash`. `POST /api/v1/finance/installment-refunds/apply` takes the orders and the hashes the page showed: a changed plan answers 409 (reload and check again), an order with nothing strong to apply 422, and the request is all or nothing. Applying records `refunded_amount` on each statement row (the original `amount` stays) and changes the one-time bills imported for those installments (matched by `Order:` and `Installment:` in the bill's notes and the same due date): a fully refunded installment's bill is paused, a partly refunded one is lowered to what is still due. Each apply is kept as a record (`finance_plans.installment_refunds`, migration `0146_installment_refunds`) with what changed, and `POST /api/v1/finance/installment-refunds/{id}/undo` puts the latest one of an order back exactly (a bill the owner changed since is left as they set it). Both are audited (`finance.installment_refunds.apply`, `.undo`) with counts only, never amounts or names. Reading needs finance read; applying and undoing need finance manage; each person sees only their own statement (owner RLS on `finance_plans`). The routes are plain `def` routes, so the database work runs in the thread pool, never on the event loop ([Event loop](realtime.md#event-loop)).

**Downstream.** A paused bill is not due anywhere and a lowered bill is due at its new amount: bills due and upcoming bills (`/summary`, `/bill-schedule`), Safe to spend, the money advisor's coming payments, and the installment statement's outstanding, next due, monthly and overdue totals all exclude the refunded part (tested in `services/api/tests/test_finance_installment_refunds.py`).

**Not done by design:** ANUM does not read the Tamara app or its API (no public API for customers), so a partial refund without an updated statement or email schedule stays an estimate until the owner imports Tamara's new figures.


## Multi-currency bank/card statement import: USD purchase and SAR posting

Finance > Import supports both CSV and reviewed PDF bank statements with international card charges. **The account ledger always records the amount and currency actually billed by the bank.** For example, a USD 20.00 purchase with SAR 76.80 billed becomes one SAR 76.80 debit (signed `-76.80`) and retains the original USD 20.00, bank FX rate and fee in its notes. It does not create a second USD withdrawal or use an estimated FX rate.

- CSV optional column mapping: *transaction currency, original/foreign amount, original currency, bank-billed amount, billed currency, exchange rate, FX/transaction fee*. Common header variants are detected automatically and each one may be manually mapped. Original amounts, actual settlement and bank-stated fee/rate appear in the preview and survive import in the transaction's notes.
- The account's actual ledger currency governs the posted amount. A foreign currency row with no bank-billed account-currency amount is flagged in the preview as a row error, not silently converted, and other valid rows can still be imported. The user can map the billed amount column or select an account denominated in the original currency.
- PDF statement review shows a separate bank-billed amount input for rows in a currency other than the selected account. The user must supply the exact signed account-currency amount before including the row; debit versus credit direction must match. PDF balance checks use the billed amount.
- If a PDF reader already extracts the amount in the account currency and shows the foreign purchase in the `original_amount` / `original_currency` columns, the bank-billed account amount is preserved as-is. A printed FX fee or VAT on the **same transaction** remains informational to avoid counting it twice. **A separately posted fee line** is imported as its own `fees` expense.
- The rules categorize positive `cashback` and `refunds` separately from other `income`. International transaction charges, FX markup, interest and annual fees take priority over generic merchant descriptions. Existing owner-created category rules still take precedence, and manual categories are not replaced.
- CSV/PDF duplicate protection remains based on date, posted account-currency amount and cleaned merchant/description. Reuploading the same file will not double post transactions.
- Three-letter ISO currency codes use the applicable minor units (e.g. KWD three decimal places); no fabricated FX rates or currency conversion is performed.

**Scope:** These safeguards apply to new reviewed imports. Previously imported misclassified transactions are not silently rewritten. Review the transaction's import preview and the bank's own original and billed figures before confirming. The existing Finance PDF source parser may require layout mapping or OCR corrections on unusual bank statement formats.


## Personal expense learning and corrected rent direction

- **Room Rent** (`room_rent`) and **Food and meals** (`food`) are first-class expense categories alongside existing Housing, Dining and Groceries. Common Arabic/English descriptions and Saudi merchants are recognized for new records.
- In Finance > Transactions, choose a category, optionally tick **Remember this category for future matching descriptions**, and review a specific merchant/description phrase. The backend stores an owner-scoped Finance category rule, which is applied to future bank statement previews, CSV/PDF imports and manually recorded transactions. It does **not** silently rewrite historical transactions or learn from another user's data. Generic phrases such as `rent`, `payment`, `card` and `food` are rejected as overbroad.
- A **positive** amount with a description indicating room rent is flagged for review in CSV import preview and the transaction details drawer. No positive amount is automatically changed to a negative value. For a manual or statement-origin transaction, the drawer offers an explicitly confirmed **Correct to money out** action that changes the sign through the normal finance transaction PATCH and canonical journal repost. For SMS/Apple Pay-origin transactions, correct their payment through Finance > iPhone SMS > Review.
- If you paid SAR 2,600 for room rent but it appears as +SAR 2,600, inspect the actual bank statement and correct the specific entry to -SAR 2,600 with the Room Rent category. The application must not infer a specific transaction ID or make an automatic correction based solely on amount or text.
- The monthly Finance summary separates income, cashback, merchant refunds, expenses and net cash flow. Cashbacks and refunds do not inflate earned income; confirmed positive credits remain part of actual net cash movement.
- Previously imported records are never silently recategorized or reversed. The audit trail, source import fingerprint and ledger correction rules remain intact. Reimporting the original statement after a human correction does not double count that same bank entry.

- **Bank DR/CR column:** CSV statement mapping supports a separate DR/CR or Debit/Credit direction column. Explicit DR converts a positive unsigned number to a ledger debit; CR means credit. Conflicts between the supplied sign and credit indicator are held as row errors. Descriptions alone never change the money direction. The selected bank account currency still determines the posted currency.
