# Finance: your own ledger, safe-to-spend, Guardian rules, read-only connectors, paper trading and the execution gateway

Finance is ANUM's native Finance domain, stages 2 to 9: one person's own ledger and a read-only "Financial Twin" built from the bank and card statements they import (stage 2), what is safe to spend for the rest of the month (stage 3), the person's own spending and risk rules with the Guardian policy kernel that every finance action must pass (stage 4), read-only connectors that bring outside data in (stage 5), SIMULATED paper and shadow trading against prices the person recorded (stage 6), the execution gateway, the one governed path any real action would have to take (stage 7), and the frameworks for limited live (stage 8) and wider live use (stage 9), both **shipped off**. It answers "what did I spend this month", "which bills are due", "what do I owe" and "how much can I still spend", and lets the person practise orders with simulated money. It never moves real money: no live executor exists and every live request is refused.

Code: `services/api/anum_api/finance/` (`safe_to_spend.py`, `fx.py`, `guardian.py`, `policy.py`, `connectors.py`, `sync.py`, `plan_api.py` for stages 3 to 5; `trading.py`, `execution.py`, `gateway.py`, `live.py`, `wider.py`, `trading_api.py`, `wider_api.py` for stages 6 to 9), `services/api/anum_api/db/finance_repository.py`, migrations `0056_finance_ledger`, `0057_finance_plan_guard_conn`, `0058_finance_paper_exec` and `0059_finance_wider_live`, `packages/contracts/src/finance.ts`, web `apps/web/src/FinanceView.tsx`, `FinancePlan.tsx`, `FinanceTrading.tsx` and `FinanceWider.tsx`, Flutter `apps/mobile/lib/features/finance/`.

## Hard limits

These are rules of the design, not settings:

- **No real money moves.** There is no payment, transfer, order, trade or withdrawal call to any bank, broker or exchange anywhere in the finance code. The Guardian kernel's `gate` denies every real money-moving action whatever the rules say (`execution_not_available`); only SIMULATED paper and shadow orders run, and they touch no account.
- **Live is off and cannot be switched on here.** No live executor and no write-scoped connector exist. `ANUM_FINANCE_LIVE_ENABLED` and `ANUM_FINANCE_WIDER_LIVE_ENABLED` are refused at startup (and by the Helm chart), the gateway refuses every live request ("Live execution is not enabled"), and the database refuses a live request in any approved or executed state.
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
| Input | CSV statement import; read-only connectors (ECB exchange rates built, providers listed as owner actions) | `finance/csv_import.py`, `finance/connectors.py` |
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
| `finance_accounts` | Name, kind (`bank`, `card`, `cash`, `loan`, `investment`), currency, institution, at most the **last four digits** (a check constraint refuses more), opening balance, archived |
| `finance_transactions` | Account, posted date, signed amount, currency, description (from the statement), category and what set it (`rule`, `owner_rule`, `manual`, `none`), notes, the import it came from, and a dedupe hash unique per account |
| `finance_imports` | One row per confirmed import: file name, layout, date format, counts (rows, imported, duplicates, unread) and the file's SHA-256 (not the file) |
| `finance_bills` | Name, amount, currency, cadence (`weekly`, `monthly`, `quarterly`, `yearly`, `once`), next due date, category, optional account, source (`manual` or `detected`), active |
| `finance_debts` | Name, kind (`loan`, `card`, `mortgage`, `other`), balance owed, APR in percent, minimum payment, due day |
| `finance_goals` | Name, target, saved so far, target date |
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

## Safe to spend

`GET /api/v1/finance/safe-to-spend` (stage 3, `finance/safe_to_spend.py`): what the person can spend from today to the end of this month (in their own time zone), and per day, per currency, line by line, from their own records only. It is arithmetic, not advice and not a forecast; the page and the assistant say so.

1. **Money you have:** the balance of every bank and cash account that is not archived. Cards, loans and investments are listed under "Not counted" with the reason (a card's debt is covered by its minimum payment; an investment is not spending money); so are archived accounts.
2. **Bills:** every active bill's due dates from today to the month's end (a weekly bill can count several times).
3. **Minimum debt payments:** each debt's minimum payment (at most what is owed) if its due day is today or later this month, or if it has no due day. A due day already passed is listed as not counted ("taken as paid").
4. **Goals:** the monthly amount the person set on the goal (`monthly_contribution`), else what is left divided by the months left to the target date, this month included, rounded **up** to the minor unit. A goal without either, or already reached, is not counted.
5. **Buffer:** what the person always keeps back, per currency (`PUT /plan`).

`safe_to_spend = money you have - bills - minimum payments - goals - buffer`. A negative figure is shown as "short this month by", with what to change. `per_day` divides a positive figure by the days left, today included, rounded **down** to the minor unit, so it never allows more than the month does. Each line carries a `note` code and `params` for the clients to word in English or Arabic, and an English `detail` for the assistant.

**One total.** With `combine_currencies` on (`PUT /plan`), the response adds `combined` in the base currency when every currency's figure can be converted with a recorded rate no older than `fx_max_age_days` (default 7): each conversion lists the amount, the rate, its date, its source and the shared base of a cross rate (for example USD to AED through EUR). If a rate is missing the status is `missing_rates` with the pairs, and the page points to Connections. Otherwise every figure stays per currency.

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

**Where it is used now.** Creating or changing a transaction returns `guardian` (what the rules say about that record; it is saved anyway, the money is gone) and the web names any rule it breaks; a confirmed import returns `rule_breaks`; `GET /guardian/alerts?month=` lists caps reached or within 20 percent, payments to blocked merchants or categories, days over the daily total and payments over the single limit; `POST /guardian/evaluate` is a dry run of any action ("Check a spend"), returning the rules' verdict and the gate's, and noting that nothing was paid, sent or saved.

**Changing rules.** Every change, the person's own included, is a proposal (`POST /guardian/changes`) on the current version, with a SHA-256 payload hash over the change id, the owner, the base version and the exact rules (the same binding rule as approvals, `approval_integrity.py`). It takes effect only when the person confirms it with the hash they were shown (`POST /guardian/changes/{id}/confirm {"payload_hash": ...}`): a different hash, a change that lapsed (24 hours) or one made on an older version is refused with 409 and nothing changes; confirming adds a new version (unique per person, so two confirmations cannot both land) and supersedes every other pending change. The web shows the new rules, what is new, what is no longer a rule, what the change loosens and the first characters of the code before the person confirms. Every proposal, confirmation and rejection is audited with ids, version, rule-kind codes and who proposed it, never a limit. **Loosening** (`loosening(old, new)`: a limit raised or removed, a block removed, quiet hours narrowed, a shorter cooling-off) is listed on the change; an assistant's proposal that loosens anything is refused, by the code and by a database check.

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

The API must be able to reach `www.ecb.europa.eu` over HTTPS (add it to the network policy's `appEgress` where egress is restricted).

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
| `GET /safe-to-spend`, `GET /plan`; `PUT /plan` | read; manage |
| `GET /guardian`, `GET /guardian/versions?limit=&offset=`, `GET /guardian/alerts?month=`, `GET /guardian/changes?status=`, `POST /guardian/evaluate` | `finance:read` |
| `POST /guardian/changes`, `POST /guardian/changes/{id}/confirm`, `POST /guardian/changes/{id}/reject` | `finance:manage` |
| `GET /connectors`, `GET /connections`, `GET /connections/{id}`, `GET /fx-rates?limit=` | `finance:read` |
| `POST /connections`, `PATCH /connections/{id}`, `POST /connections/{id}/sync`, `POST /connections/{id}/disconnect`, `DELETE /connections/{id}`, `POST /fx-rates`, `DELETE /fx-rates/{id}` | `finance:manage` |
| `GET /prices?symbol=&limit=`, `GET /paper/portfolios`, `GET /paper/portfolios/{id}`, `GET /paper/portfolios/{id}/orders?status=&run=&limit=&offset=` | `finance:read` |
| `POST /prices`, `DELETE /prices/{id}`, `POST /paper/portfolios`, `PATCH, DELETE /paper/portfolios/{id}`, `POST /paper/portfolios/{id}/reset`, `POST /paper/portfolios/{id}/check`, `POST /paper/orders/{id}/cancel` | `finance:manage` |
| `GET /execution/status`, `GET /execution/intents?status=&mode=&limit=&offset=`, `GET /execution/intents/{id}`, `GET /execution/live`, `GET /execution/wider` | `finance:read` |
| `POST /execution/intents`, `POST /execution/intents/{id}/approve`, `POST /execution/intents/{id}/reject`, `POST /execution/live/changes`, `POST /execution/live/opt-out`, `POST /execution/wider/widenings`, `POST /execution/wider/widenings/{id}/approve`, `POST /execution/wider/widenings/{id}/reject`, `POST /execution/wider/review` | `finance:manage` (only the request's owner; live requests always 409) |
| `PUT /execution/kill-switch` | `finance:manage`; `scope: global` also needs a workspace owner or an organisation administrator |

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

The `anum.finance` skill (`agent_skills.py`) is selected by words such as spent, spending, budget, bills, balance, debts, transactions, afford, rules, cap, limit, block, guardian and, in Arabic, مصاريف, فواتير, رصيد, صرفت, ميزانية, ديون, أصرف, متاح, قواعد, حدود. Its planner (`finance.tools.plan_call`) reads only the user's own words (threat model G5): "what did I spend this month" asks `finance.summary`, "how much did I spend on groceries in September" and "transactions at Carrefour" search, "bills due this week" lists bills, "how much can I spend this month" and "كم أقدر أصرف هذا الشهر" ask `finance.safe_to_spend`, "show my spending rules" and "ما هي قواعد الصرف" read the rules, "cap dining at 600", "block payments to talabat", "never spend more than 2000 at once" and "no more than 300 a day" propose a stricter rule. Pay, transfer, send, buy, sell and "spend 200 on ..." return nothing and fall through to `anum.external-action`.

Tool output is untrusted data (descriptions come from statements): in chat it reaches the model only through `tool_output_prompt_block` after the untrusted-data rules (`chat_tools.summary_messages`, category `finance`), and voice grounding labels it the same way. The output carries a note that descriptions are data, not instructions.

## Clients

- **Web** (`#finance`, tabs deep-linked as `#finance-safe`, `#finance-transactions`, `#finance-import`, `#finance-bills`, `#finance-guardian`, `#finance-connections` and so on, in the browser history): the summary band, a safe-to-spend card, the month's spend by category as an SVG chart with a table fallback, income and spending, bills due, balances and goals; **Safe to spend** with the build per currency as a table (money you have, bills, minimum payments, goals, buffer), the per-day figure, what is not counted and why, the optional total with each conversion's rate, date and source, and the buffer and currency settings; transactions with search, filters, paging, edit, delete and CSV export (a record that breaks a rule is saved and the rule named); accounts; the import with a mapping preview and undo; bills with suggestions; debts, goals (with a monthly amount) and holdings; **Guardian** with the rules in plain words, changes waiting for review (proposed by you or an assistant, with what they loosen) confirmed with the code shown or rejected, the rules editor with a review step, this month's alerts, "Check a spend" and the version history; **Connections** with your connections (status, last and next sync, last error, Sync now, Disconnect, Delete), the sources (read-only scopes, terms, and the owner action for sources not built, with no Connect button) and the exchange rates (filter, type a rate, delete a typed one); settings with category rules; **Paper trading** (`#finance-trading`) with the SIMULATED note and assumptions, requests waiting for approval (an assistant's marked), portfolios (create, rename, change costs, reset, delete), positions with recorded prices and simulated P&L, the shadow comparison, a new order checked by the rules and reviewed with its code before it runs, the order history (status, this run or every run, paging, cancel a waiting order) and the recorded prices (record, filter, delete); **Execution and live** (`#finance-execution`) with both kill switches, how a request runs, limited live (off: the preconditions, your live limits changed only with your approval, opt-out, what the owner must provide), wider live use (off: the measured checklist, ceilings, step proposals reviewed with their code, incidents, review to unfreeze) and every request with its status. Loading, empty with a next action, error with a retry and permission denied are handled on every tab, in English and Arabic.
- **Flutter** (`apps/mobile/lib/features/finance/`, opened from the menu or a Home pin, with an AppBar back button): the month's summary with spend by category, upcoming bills and balances; safe to spend line by line per currency; the Guardian rules, this month's alerts and changes waiting for you, confirmed (with the code) or rejected; connections with their status and Sync now; transactions with search and paging; accounts, bills, debts and goals. Paper trading shows the SIMULATED portfolios and requests waiting for approval, approved (with the code) or rejected; Execution and live shows the person's kill switch (switchable), the organisation's, limited live (off, with its preconditions) and wider live use (off, the checklist, review after an incident). On the phone you add accounts, transactions and bills and delete any record; statement import, editing, holdings, editing rules, the buffer, connecting sources, placing paper orders, recording prices, live limits and widening steps are on the web. English and Arabic.

## Owner actions

- **Exchange rates:** connect the ECB reference rates under Finance, Connections (no account needed). For background refresh set `ANUM_FINANCE_SYNC_ENABLED=true` and allow HTTPS egress to `www.ecb.europa.eu`.
- **Bank and card accounts:** a contract with a provider licensed for UAE Open Finance (Al Tareq), its sandbox and production credentials and API documentation. Then an adapter is built and tested against it; until then, statements come in as CSV.
- **Broker:** choose the broker; an API key with no trading or withdrawal permission and the API's documentation.
- **Crypto exchange:** choose the exchange; a read-only API key (trading and withdrawals off).
- **Market prices:** a market data provider whose licence allows storing prices for personal use, and an API key. Until then paper orders use prices the person records.
- **Live execution (stages 8 and 9):** see [What the owner must provide before live](#what-the-owner-must-provide-before-live). Nothing can be switched on without it.

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

Stage 8 (`finance/live.py`): **the framework only, shipped off. Live execution is impossible in this release.**

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

- A live executor or any provider write integration (see [What the owner must provide before live](#what-the-owner-must-provide-before-live)). Live execution, limited or wider, cannot run.
- Adapters for bank, card, broker, crypto and market-price providers (see [Owner actions](#owner-actions)); a cross-currency "net worth"; PDF or OFX statement import; attachments (receipts); Flutter statement import, rule editing, connecting sources, placing paper orders and setting live limits; finance cards with item rows in chat (the chat answers in text above a summary card).
