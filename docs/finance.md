# Finance: your own ledger and Financial Twin

Finance is Stage 2 of ANUM's native Finance domain: one person's own ledger and a read-only "Financial Twin" built from the bank and card statements they import. It answers "what did I spend this month", "which bills are due" and "what do I owe". It never moves money.

Code: `services/api/anum_api/finance/`, `services/api/anum_api/db/finance_repository.py`, migration `0056_finance_ledger`, `packages/contracts/src/finance.ts`, web `apps/web/src/FinanceView.tsx`, Flutter `apps/mobile/lib/features/finance/`.

## Hard limits

These are rules of the design, not settings:

- **No money moves.** There is no payment, transfer, order, trade, bank, card or broker API call anywhere in the finance code, and no button for one in any client.
- **No keys to the AI.** The assistant reads the ledger only through three read-only tools; it holds no bank, card or broker credential, and there is none to hold.
- **No live execution.** Nothing places, schedules or simulates an order.
- **No profit promises.** ANUM reports what the statements say. It gives no investment advice and makes no forecast of returns.
- **No market data.** Holdings are quantities and costs the person types; ANUM fetches no prices.

A request to pay or transfer ("pay the DEWA bill", "transfer 500 to the landlord") is not a finance question: the finance planner returns nothing for it and the request goes to the governed `anum.external-action` skill, which waits for an approval like every other external action.

## Defaults (assumptions until the owner answers the setup questions)

| Question | Default in Stage 2 | Where to change it |
|---|---|---|
| Whose data | The signed-in person's own finances, private to them, even from workspace owners | Row policy in `0056_finance_ledger`; `owner_user_id` on every table |
| Region | UAE: base currency AED, time zone Asia/Dubai | Per person and workspace under Finance, Settings (`PUT /api/v1/finance/settings`); defaults in `finance/models.py` |
| Input | CSV statement import only | `finance/csv_import.py` |
| Categories | Built-in keyword rules for common UAE merchants, plus the person's own rules | `finance/categories.py`, Finance, Settings |

## Money rules

- Amounts are exact: `Decimal` in Python, `numeric(20, 4)` in PostgreSQL, each next to a three-letter ISO 4217 `currency`. Holdings' quantities are `numeric(28, 8)`.
- **The one rounding rule:** an amount is rounded once, when it enters ANUM (a CSV cell, a form field), to its currency's minor unit with banker's rounding (`ROUND_HALF_EVEN`): 2 decimals for AED and most currencies, 3 for BHD, IQD, JOD, KWD, LYD, OMR and TND, 0 for JPY, KRW and a few others. Sums of rounded amounts are exact and never rounded again (`finance/money.py`).
- Different currencies are never added together. Every total (balances, spend, income, net, bills due, debts) is a list of `{currency, amount}`.
- On the wire an amount is a decimal string (`"1234.50"`), so no client turns it into a binary float. Clients format it for display only.
- Signs: a transaction's amount is positive for money in and negative for money out. A bill, debt balance, goal and cost are positive.
- **FX is not built.** There is no conversion between currencies, so there is no single "net worth" figure across currencies. This is a known gap (see [Not built yet](#not-built-yet)).

## Data model

| Table | What it holds |
|---|---|
| `finance_settings` | Base currency, time zone and the person's category rules (one row per person and workspace) |
| `finance_accounts` | Name, kind (`bank`, `card`, `cash`, `loan`, `investment`), currency, institution, at most the **last four digits** (a check constraint refuses more), opening balance, archived |
| `finance_transactions` | Account, posted date, signed amount, currency, description (from the statement), category and what set it (`rule`, `owner_rule`, `manual`, `none`), notes, the import it came from, and a dedupe hash unique per account |
| `finance_imports` | One row per confirmed import: file name, layout, date format, counts (rows, imported, duplicates, unread) and the file's SHA-256 (not the file) |
| `finance_bills` | Name, amount, currency, cadence (`weekly`, `monthly`, `quarterly`, `yearly`, `once`), next due date, category, optional account, source (`manual` or `detected`), active |
| `finance_debts` | Name, kind (`loan`, `card`, `mortgage`, `other`), balance owed, APR in percent, minimum payment, due day |
| `finance_goals` | Name, target, saved so far, target date |
| `finance_holdings` | Investment account, symbol, name, quantity and total cost. No prices |

Deleting an account deletes its transactions, imports and holdings; bills, debts and goals linked to it stay without the link. Undoing an import deletes the transactions it added.

## Privacy

- Every finance table has a foreign key to its workspace, **forced** RLS, the standard `tenant_isolation_<table>` policy and a **restrictive** `owner_only_<table>` policy: `owner_user_id = nullif(current_setting('anum.user_id', true), '')`. Restrictive policies are AND-ed with the permissive one, so a row is visible and writable only to its owner inside its own tenant and workspace. With no `anum.user_id` set nothing is visible. No role is granted anything new and no job reads finance across tenants or people ([Multi-tenancy](multi-tenancy.md)).
- The store opens its session with `open_scoped_store(..., user_scoped=True)` and repeats the tenant, workspace and owner in every query.
- Audit records for finance changes carry the action and the record id only: never an amount, description, account name or number.
- "Download my data" exports the whole ledger (section `finance`) and "Delete my data" deletes it (count `finance_records`) ([Privacy](privacy.md)).
- Statements are not kept: only the parsed rows and the file's hash.

## CSV import

`POST /api/v1/finance/imports/preview` reads a file and shows what an import would do; nothing is saved. `POST /api/v1/finance/imports` sends the same file and choices again and writes the new rows in one unit of work. Both take `{account_id, filename, content, preset?, date_format?, mapping?}`, where `content` is the file's text.

- **Layouts.** A generic layout (a date, a description, and either one signed amount column or a debit and a credit column; English or Arabic column names) and presets for common UAE bank exports: Emirates NBD, First Abu Dhabi Bank, ADCB, Mashreq and Dubai Islamic Bank. A preset is a set of column names written from typical exports; they are not certified against every bank's current file. When a file uses other names the preview says so and the person picks the columns (`mapping`).
- **Preamble lines.** The header row is found within the first 20 rows, so account details above the table do not break the import.
- **Dates.** The format is detected from every date in the file (rows that are no date at all, such as a footer, are ignored for detection and reported as unread). When the dates fit both day first and month first, ANUM takes day first (the UAE convention) and flags it; `date_format` overrides it.
- **Amounts.** `1,234.50`, `1.234,50`, `-12.00`, `(12.00)`, `12.00 DR`, `5.00 CR`, `AED 9`, Arabic-Indic digits.
- **Limits.** 2 MB, 5,000 rows, 50 columns, 1,000 characters a cell, 500 characters a description. Larger files are refused with what to do ("export one month at a time").
- **Untrusted input.** The file is text only: never evaluated or opened by a spreadsheet program. Descriptions are stripped of control, format and direction-override characters. Nothing in a file can choose an account, a tool or a rule.
- **Duplicates.** Each row's hash covers the account, date, amount, currency, cleaned description and its occurrence number among identical rows in the same file. The hash is unique per account, so importing the same or an overlapping statement again adds only new rows; two identical purchases on one day in one file stay two rows.
- **Export.** `GET /api/v1/finance/transactions/export.csv` (same filters as the list, at most 10,000 rows). Text cells that start with `=`, `+`, `-`, `@`, `|`, `%`, a tab or a carriage return get a leading apostrophe so a spreadsheet does not run them as formulas.

## Categories

Rule-based only, no AI and no network (`finance/categories.py`): the person's own rules first ("description contains" a phrase), then built-in keyword rules for common UAE merchants and statement wording (Carrefour, Lulu, Spinneys: groceries; DEWA, SEWA, ADDC: utilities; Etisalat, du: phone and internet; Salik, RTA, Careem: transport; ENOC, ADNOC: fuel; salary and WPS: income; transfers; ATM; fees), matched as whole words. Anything else is income for money in or other for money out. A category picked by hand is never changed by a rule.

## Summary

`GET /api/v1/finance/summary?month=YYYY-MM` (default: this month in the person's time zone):

- balances per account (opening balance plus every transaction) and per currency;
- spend by category for the month (money out, transfers and income excluded), per currency;
- income versus spend and the net, per currency;
- bills due in the next 30 days (a past due date of a recurring bill rolls forward);
- debts owed and minimum payments, per currency;
- goals and how far along they are.

`GET /api/v1/finance/bills/suggestions` finds recurring payments: money out with the same payee at a steady interval (weekly, monthly, quarterly or yearly) and amount (within 15 percent), three or more times, not yet saved as a bill. The person keeps the ones that are bills.

## API

All under `/api/v1/finance`, every route checking permissions on the server:

| Route | Permission |
|---|---|
| `GET /summary`, `GET /settings`, `GET /formats` | `finance:read` |
| `PUT /settings` | `finance:manage` |
| `GET, POST /accounts`; `GET, PATCH, DELETE /accounts/{id}` | read for GET, manage otherwise |
| `GET, POST /transactions`; `GET, PATCH, DELETE /transactions/{id}`; `GET /transactions/export.csv` (`q`, `account_id`, `category`, `from`, `to`, `direction`, `limit`, `offset`) | read for GET, manage otherwise |
| `POST /imports/preview`, `POST /imports`, `GET /imports`, `DELETE /imports/{id}` | manage (GET: read) |
| `GET /bills/upcoming?days=`, `GET /bills/suggestions` | `finance:read` |
| `GET, POST /bills`, `/debts`, `/goals`, `/holdings`; `GET, PATCH, DELETE /<kind>/{id}` | read for GET, manage otherwise |

## Permissions

`finance:read` and `finance:manage` (`anum_api/authorization.py`). Owners and members have both; viewers have neither, and the page tells them to ask an owner to change their role. Because every row is private to its owner, the permissions only decide whether a role may use Finance at all: a workspace owner cannot read a member's ledger.

## Assistant

Three read-only tools (`finance/tools.py`, registered in the API's and the Temporal worker's registries), each with an `input_schema`, `action_kind="read"` and `RiskLevel.LOW`, on the caller's own ledger only:

- `finance.summary` (`month`)
- `finance.transactions.search` (`query`, `category`, `from`, `to`, `direction`, `limit`)
- `finance.bills.upcoming` (`days`)

The `anum.finance` skill (`agent_skills.py`) is selected by words such as spent, spending, budget, bills, balance, debts, transactions and, in Arabic, مصاريف, فواتير, رصيد, صرفت, ميزانية, ديون. Its planner (`finance.tools.plan_call`) reads only the user's own words (threat model G5): "what did I spend this month" asks `finance.summary`, "how much did I spend on groceries in September" and "transactions at Carrefour" search, "bills due this week" lists bills. Pay, transfer, send, buy, sell and "spend 200 on ..." return nothing and fall through to `anum.external-action`.

Tool output is untrusted data (descriptions come from statements): in chat it reaches the model only through `tool_output_prompt_block` after the untrusted-data rules (`chat_tools.summary_messages`, category `finance`), and voice grounding labels it the same way. The output carries a note that descriptions are data, not instructions.

## Clients

- **Web** (`#finance`, tabs deep-linked as `#finance-transactions`, `#finance-import`, `#finance-bills` and so on, in the browser history): the summary band, the month's spend by category as an SVG chart with a table fallback, income and spending, bills due, balances and goals; transactions with search, filters, paging, edit, delete and CSV export; accounts; the import with a mapping preview and undo; bills with suggestions; debts, goals and holdings; settings with category rules. Loading, empty with a next action, error with a retry and permission denied are handled on every tab, in English and Arabic.
- **Flutter** (`apps/mobile/lib/features/finance/`, opened from the menu or a Home pin, with an AppBar back button): the month's summary with spend by category, upcoming bills and balances; transactions with search and paging; accounts, bills, debts and goals. On the phone you add accounts, transactions and bills and delete any record; statement import, editing and holdings are on the web. English and Arabic.

## Not built yet

Stage 2 is the ledger and the read-only Financial Twin. Not built, by design, until their own stage:

3. **Safe-to-spend:** what can be spent this month after bills, minimum payments and goals.
4. **Guardian policy kernel:** the person's own spending and risk rules, enforced before any finance action.
5. **Connectors:** bank (open finance), card, broker, market-data and crypto connections. Today there are none; input is CSV only.
6. **Paper and shadow trading:** simulated orders against recorded prices, never real.
7. **Execution gateway:** the one governed path for any real money movement, with approvals bound to a payload hash.
8. **Limited live:** small, capped, approved real actions through that gateway.
9. Wider live use after the limited stage has proved itself.

Also not built in Stage 2: FX conversion and a cross-currency total; PDF or OFX statement import; attachments (receipts); budgets per category; Flutter statement import; finance cards with item rows in chat (the chat answers in text above a summary card).
