# Live trading with Interactive Brokers (IBKR)

ANUM can send real orders to the owner's own Interactive Brokers account, behind every lock below. Paper is the default and stays the default until the owner finishes the paper period and turns live on with a typed code. This page is the spec for the code in `services/api/anum_api/finance/ibkr_*.py`, `services/api/anum_api/db/ibkr_repository.py`, migration `0122_finance_ibkr_live` and the web tab Finance, IBKR trading (`apps/web/src/FinanceIbkr.tsx`).

This is not legal, tax or investment advice. Real money can be lost.

## Owner decisions (2 November 2026)

* Broker: **Interactive Brokers**. Market: **US-listed ETFs only**.
* **Every order is approved by the owner.** ANUM never places an order by itself. A buy plan only proposes.
* Strategy: long-term buy and hold of broad ETFs, a small fixed amount on a schedule. No day trading, no selling on drops.
* **Halal first**: the allowed list defaults to Shariah-screened US ETFs. A fund that is not screened needs an explicit confirm from the owner.
* Starting limits: total capital **USD 50**, about **USD 10 per order**, a daily loss stop of **USD 5** (editable by the owner).
* Only honest numbers from IBKR's own records: today's profit or loss, unrealised and realised apart, dividends received. ANUM never predicts or promises earnings.
* The owner's IBKR account is opened with **tax residency Saudi Arabia**.

## What works today

| Part | State |
|---|---|
| Connect a paper or live IBKR account in the owner's form, encrypted, revocable | Built |
| Read account, cash, positions, open orders, executions and today's P&L; statements and dividends from Flex | Built |
| Reconciliation against ANUM's records; any difference blocks new orders until explained | Built |
| Limit order ticket, approval with the payload hash, checks at approval and again right before sending | Built |
| Partial fills, rejects, cancels, IBKR questions declined, no answer looked up by client order id, duplicate approvals refused | Built |
| Kill switch that cancels every open order at IBKR and confirms each cancel | Built |
| Buy plans that propose a buy on a day of the month | Built (proposals are made when the owner opens the IBKR tab in US market hours) |
| Paper proof gate, live switch with typed code and cooling-off | Built |
| In-app alerts for fills, rejects, limit hits, mismatches, session end and the kill switch | Built (in the IBKR tab; not yet push or email) |

## Connector

How IBKR lets an individual use its API: the **Client Portal Gateway**, a small Java program IBKR publishes, runs next to ANUM. The owner logs in to the gateway in a browser with the IBKR user name, password and phone approval. The gateway then holds the brokerage session and answers the Web API on `https://<gateway>:5000/v1/api`. ANUM never asks for, sees or stores the IBKR password.

ANUM keeps per owner and workspace, Fernet-encrypted with `ANUM_SECRETS_KEY` (`finance_broker_connections.secret`):

* the gateway address, one of a fixed list: `ibkr-gateway` (a container on ANUM's network), `127.0.0.1` or `localhost`, always port 5000;
* the SHA-256 fingerprint of the gateway's own certificate. The gateway uses a self-signed certificate, so ANUM **pins** it: the connection is refused before any request byte is written if the certificate does not match;
* the IBKR account id (paper ids start with `D`; a paper connection refuses a live id and the other way round);
* optionally a Flex Web Service token and query id for statements.

Disconnecting erases all of it (the database refuses a revoked connection that still holds a secret). The API returns a masked hint only (`DU...567`).

Safety properties (`ibkr_client.py`, tests in `tests/test_finance_ibkr_client.py`): fixed hosts, pinned TLS to the gateway, CA-verified TLS 1.2+ to IBKR's Flex hosts (`ndcdyn.interactivebrokers.com`, `gdcdyn.interactivebrokers.com`), **no redirects followed**, 512 KB per answer, 8 second timeout, at most 8 requests a second (IBKR allows 10), path values checked before they are sent, secrets never in `repr`, logs or errors.

### Endpoints used

From IBKR's Web API v1.0 reference ([IBKR Web API](https://www.interactivebrokers.com/campus/ibkr-api-page/cpapi-v1/), [Place Order](https://www.interactivebrokers.com/docs/web-api/web-api-v-1-0-documentation/endpoints/orders/place-order), [Authentication FAQ](https://www.interactivebrokers.com/docs/web-api/authentication/faq.md), [pacing limits](https://www.interactivebrokers.com/docs/web-api/trading/usage-and-availability/pacing-limitations.md)). The IBKR sites could not be opened from the build session (the network blocks them), so the paths were checked against IBKR's published reference pages as found by web search; **confirm them against the gateway you run during the paper period**.

| Purpose | Call |
|---|---|
| Session | `POST /iserver/auth/status`, `POST /tickle`, `POST /iserver/auth/ssodh/init` |
| Accounts | `GET /iserver/accounts`, `GET /portfolio/accounts` |
| Cash and P&L | `GET /portfolio/{account}/ledger`, `GET /iserver/account/pnl/partitioned` |
| Positions | `GET /portfolio/{account}/positions/{page}` |
| Open orders, executions | `GET /iserver/account/orders?force=true`, `GET /iserver/account/trades?days=7` |
| Contract and price | `GET /trsrv/stocks?symbols=`, `GET /iserver/marketdata/snapshot?conids=&fields=31,84,86,6509` |
| Orders | `POST /iserver/account/{account}/orders`, `POST /iserver/reply/{id}` (always `confirmed: false`), `DELETE /iserver/account/{account}/order/{id}` |
| Statements | Flex `SendRequest` then `GetStatement` |

### The session lifecycle

* The brokerage session ends after about **5 idle minutes**; ANUM calls `/tickle` on every check and read.
* The gateway login itself ends at least once a day (and when IBKR resets it). Then the IBKR tab shows **Log in to IBKR again** and raises one alert. The owner opens the gateway page, logs in and approves on the phone, and presses Check. ANUM never automates the login or the two-factor step.
* "Start a new session" calls `/iserver/auth/ssodh/init` for a gateway that is still logged in.

### Fractional shares and cash-quantity orders

**Not available through the API, as far as could be checked.** IBKR's campus pages and the QuantConnect team say fractional trading is supported through FIX and CTCI but not through the API, and IBKR answered that cash-quantity orders are supported only for forex through the TWS API ([IBKR fractional shares](https://www.interactivebrokers.com/campus/trading-lessons/fractional-shares/), [QuantConnect forum](https://www.quantconnect.com/forum/discussion/12955/fractional-shares-interactive-brokers-margin-account/), [Elite Trader thread](https://www.elitetrader.com/et/threads/cash-quantity-stock-orders-via-the-api.345955/)). The Web API reference does document a `cashQty` field, so this is **NOT CONFIRMED** either way for a Saudi-resident account.

What ANUM does: **whole shares only** by default; `cashQty` is never sent. A "fractional" switch exists (off) for the day IBKR confirms it works for the account; IBKR's reject is handled honestly if it does not.

What it means: with USD 10 per order and whole shares, an ETF must cost under USD 10 a share (plus the fee buffer). Most broad and Shariah-screened ETFs cost more than that, so with the starting limits **most orders will be refused by the per-order limit**. The owner decides: raise the per-order limit (for example to the price of one share, still within the USD 50 total), or wait for IBKR to confirm fractional orders through the API.

### Market data on a small account

IBKR provides **free delayed data** (about 15 minutes old) by default for available exchanges; real time needs a paid subscription ([IBKR market data pricing](https://ndcdyn.interactivebrokers.com/en/index.php?f=14193)). ANUM asks the snapshot for field 6509 (market data availability) and labels the price **Delayed** in the ticket. The price band is the same width with delayed data: nothing is widened silently. A frozen price, a previous close or no permission means no usable price, and the order is refused. The cheapest optional subscription found is the **US Securities Snapshot and Futures Value Bundle** for non-professionals, listed at about **USD 10 a month** (waived in a month with at least USD 30 of commissions) plus USD 0.01 per snapshot ([IBKR knowledge base](https://ibkb.interactivebrokers.com/fr/node/2840), [a broker fee schedule quoting it](https://www.lhv.ee/en/trader-info-fees)): **NOT CONFIRMED** for this account, and on a USD 50 account it would likely cost more than the account earns. The owner decides; delayed data is enough for limit orders that wait.

## Read only first

`POST /finance/ibkr/sync` reads account, cash, positions, open orders, executions and today's P&L, updates ANUM's orders (fills, partial fills, cancels, rejects) and reconciles. `POST /finance/ibkr/statements` reads the owner's Flex statement: dividends received, tax withheld and the trade count.

**Reconciliation** (`ibkr_rules.reconcile`): every open IBKR order on an allowed ETF must be one ANUM sent (its client order id), every ANUM order must show the same filled quantity at IBKR, and positions in allowed ETFs must equal the accepted starting point plus every ANUM fill since then. The starting point is taken when the account is first checked. Any difference raises an urgent alert and **blocks new orders** until the owner explains it with a note (`POST /reconciliation/accept`), which makes IBKR's current positions the new starting point. Every difference counts against the paper proof until it is explained.

## The order path

1. **Intent.** The owner's order ticket (or a buy plan, or an assistant's paper proposal) creates an intent in `finance_exec_intents`: mode `paper` or `live`, kind `trade`, limit order, US dollars, `account_id = ibkr:<connection>`, with the payload hash. Nothing is sent. Assistants can only propose paper orders (code and database).
2. **Approval** (`POST /intents/{id}/approve` with the payload hash): only the owner, only while pending, only within `ANUM_FINANCE_BROKER_APPROVAL_MINUTES` (5), once. ANUM reads IBKR fresh and checks every hard limit. If any fails the intent is refused and nothing is sent. Otherwise it is claimed and the order row written (`submitting`) in one short transaction.
3. **Right before sending**, with no transaction open: settings and kill switches are read again, IBKR is read again, the client order id is looked up at IBKR, and every limit is checked once more. Then one limit DAY order with `outsideRTH` false and `cOID` = the intent id is sent.
4. **Answers.** An order id: `submitted` (or `filled`). A question from IBKR (for example "no market data"): ANUM declines it and the order is `rejected` with IBKR's words; ANUM never answers yes for the owner. A refusal: `rejected`. No answer: `unknown`; "Look up and send" looks the id up first and adopts the order if IBKR has it; IBKR also refuses a repeated `cOID` within 24 hours. An API restart while `submitting` becomes `unknown` after two minutes and is looked up, never sent blind.

### Hard limits

Checked at approval **and** immediately before sending (`ibkr_rules.order_checks`), each from fresh IBKR data:

| Limit | Default | Notes |
|---|---|---|
| Total capital in use | USD 50 | Market value of every position plus open buy orders plus this order |
| Most per order | USD 10 | Quantity times limit price |
| Daily loss stop | USD 5 | From IBKR's "today's P&L". A safety stop: when reached, ANUM makes **no new orders** for the rest of the day. **It never sells anything.** |
| Orders per day | 3 | New York trading day |
| Market hours | US regular session | Not in the first and last 15 minutes; early closes; refused when the year's holidays are unknown |
| Price band | 2 percent | Limit price against IBKR's last price, which must be fresh |
| Cash only | | Settled cash covers the order plus the fee buffer (USD 1). ANUM never borrows |
| No short selling | | A sell is at most what is held minus open sells |
| Allowed ETFs | Shariah-screened by default | Checked by IBKR contract id, not only by symbol |
| Reconciliation, session, kill switches | | Must be clean, logged in and off |

Code ceilings no edit can pass: capital USD 10,000, per order USD 1,000 (repeated by the database for live orders), daily loss USD 1,000, 20 orders a day. While live is on, limits can only be made stricter.

### Kill switch

`POST /kill-switch` (any person who manages their own finance can always stop): blocks every new order, lists every open order at IBKR for each active connection, cancels each, reads IBKR again and confirms each cancel (one retry). A cancel that is not confirmed raises an urgent alert telling the owner to check IBKR. Nothing held is sold. `POST /kill-switch/release` with the word `RESUME` allows orders again (owner only).

### Buy plans

A plan is an allowed ETF, an amount (at most the per-order limit) and a day of the month (1 to 28). On or after that day, when the owner opens the IBKR tab during US market hours, ANUM reads IBKR's price and **proposes** one buy (whole shares the amount can buy, limit at IBKR's price) into the approval queue. Once a month per plan. It never sends, never sells and never runs on its own schedule: an approval expires in five minutes, so proposals are only made while the owner is there. When the amount buys less than one whole share, ANUM says so and proposes nothing that month.

## Shariah screening

Shariah-screened ETFs on the list: SPUS (SP Funds S&P 500 Sharia Industry Exclusions ETF), HLAL (Wahed FTSE USA Shariah ETF), UMMA (Wahed Dow Jones Islamic World ETF), SPRE (SP Funds S&P Global REIT Sharia ETF), SPSK (SP Funds Dow Jones Global Sukuk ETF). The issuer pages (spfunds.com, funds.wahedinvest.com) could not be opened from the build session; the names and listings come from published comparisons ([ETF Central](https://www.etfcentral.com/compare-etfs/SPUS-vs-HLAL), [Zacks](https://zacks.com)) and are **NOT CONFIRMED** against the issuers. When the owner adds one, ANUM looks it up at IBKR and accepts it only as a US-listed stock-type contract, which confirms the US listing from IBKR's own data.

* Screening is done by each fund issuer's own Shariah board or index provider. **ANUM does not certify** any fund.
* Every symbol in the screens is marked Shariah-screened or Not Shariah-screened. Adding one that is not screened needs an explicit confirm.
* Purification of non-compliant income is shown when IBKR or the issuer data provides it. Today neither the IBKR data ANUM reads nor the Flex statement reports it, so the screen says "Not reported by IBKR or the fund issuer".

## Modes and the live locks

Paper is the default. Live orders need every gate, each tested on its own (`tests/test_finance_ibkr.py::test_each_live_gate_blocks_a_live_order_on_its_own`):

| Gate | What holds it |
|---|---|
| `deployment_flag` | `ANUM_FINANCE_LIVE_ENABLED=true` |
| `live_broker` | `ANUM_FINANCE_LIVE_BROKER=ibkr` names the reviewed executor |
| `live_connection` | An active, checked live IBKR connection |
| `paper_proof` | At least `ANUM_FINANCE_BROKER_PAPER_MIN_ORDERS` (20) paper orders reached IBKR, at least `ANUM_FINANCE_BROKER_PAPER_MIN_DAYS` (14) days since the first, zero unexplained differences |
| `limits` | At least one allowed ETF |
| `reconciliation` | Live records match |
| `kill_switches` | Organisation, person and IBKR kill switches all off |
| `owner_switch` | The owner turned live on with the code shown on screen (one-time, 10 minutes, stored only as a hash) |
| `cooling_off` | 24 hours after turning live on |

How each earlier lock changed, in the narrowest way:

* **Startup refusal** (`config.py`): `ANUM_FINANCE_LIVE_ENABLED=true` is still refused unless `ANUM_FINANCE_LIVE_BROKER=ibkr` is also set. Any other broker name is refused. The Helm chart mirrors both (`infra/helm/ci/lint.sh`). Stage 9 (wider live) stays refused.
* **Database** (migration 0122): `ck_finance_exec_intents_no_live` became `ck_finance_exec_intents_live_ibkr`. A live intent may run only as the owner's own buy or sell **limit** order for a US dollar security on an `ibkr:` connection, at most USD 1,000, approved by its owner. Every other live intent is refused exactly as before.
* **Guardian kernel** (`guardian.gate`): the `execution_not_available` deny is lifted only for a trade that carries a `LiveClearance` naming every live gate, which only `ibkr_service` makes after checking them. The owner's Guardian rules still decide; transfers, payments and spending stay denied.
* `LIVE_EXECUTORS` stays empty: the older generic live path still refuses everything.

## Screens

Finance, IBKR trading (English and Arabic): the PAPER or LIVE badge on every section, the kill switch, alerts, connect paper and live, account and positions, today's P&L with unrealised and realised apart, dividends and tax withheld from statements, reconciliation with the explain form, the order ticket (limit only, IBKR price labelled Delayed or real time), approvals showing the exact order and a countdown, orders with Check, Look up and send, and Cancel, limits with the daily loss explanation, allowed ETFs with Shariah labels, buy plans, the live switch with its gates and paper proof, and the audit trail. Every state is handled: loading, empty with a next action, error with a retry, permission denied, and paging.

## API

All under `/api/v1/finance/ibkr`. Reads need `finance:read`; every change also needs the workspace owner (`workspace:manage` or an organisation administrator). Turning live off and the kill switch need only `finance:manage`, so anyone can always stop their own trading.

`GET /overview`, `POST /connections`, `POST /connections/{id}/check`, `DELETE /connections/{id}`, `POST /sync`, `POST /statements`, `PUT /limits`, `POST /symbols`, `DELETE /symbols/{symbol}`, `PUT /fractional`, `PUT /plans`, `POST /plans/propose`, `GET /quote`, `POST /orders`, `GET /orders`, `POST /intents/{id}/approve`, `POST /intents/{id}/reject`, `POST /orders/{id}/send`, `POST /orders/{id}/refresh`, `POST /orders/{id}/cancel`, `POST /kill-switch`, `POST /kill-switch/release`, `POST /live/request`, `POST /live/confirm`, `POST /live/off`, `POST /reconciliation/accept`, `GET /events`, `POST /events/read`. The broker readiness preflight (`POST /finance/broker/readiness`) lists the IBKR live gates when the profile names Interactive Brokers.

Audit records carry ids and codes only: never an account id, amount, price, quantity or symbol.

## Storage

Migration `0122_finance_ibkr_live` (expand only): `finance_broker_settings`, `finance_broker_connections`, `finance_broker_orders`, `finance_broker_events`, each with forced RLS, the tenant policy, a restrictive owner-only policy and the tenant purge policy; listed in the tenant lifecycle registry. Deleting the person's finance data deletes them. Migration `0124_ibkr_purge_policy` lets the restrictive owner-only policy also admit `anum_tenant_purger` (as 0079 does for the other owner-only tables), so an organisation or workspace deletion removes these rows too; the purge role still reaches them only through its own purge policy and can never write. Database checks: one active connection per environment, gateway host from the fixed list, no secret kept on a revoked connection, positive sizes, filled never above the quantity.

## The owner's steps

1. **Open the IBKR account** (done 2 November, Saudi tax residency): an individual **cash** account, not margin. Read which IBKR entity your account agreement names: for Saudi residents sources disagree (Interactive Brokers LLC or a foreign IBKR entity, [IBKR: which entity carries my account](https://ibkb.interactivebrokers.com/es/node/1000)). Turn on trading permission for US stocks and ETFs only. Turn on two-factor login with the IBKR mobile app.
2. **Paper account**: in IBKR's Client Portal, open Settings, Account Settings, Paper Trading Account, and create it. IBKR shows a separate paper user name and an account id starting with `DU`. (Menu names as IBKR describes them; confirm in your portal.)
3. **Statements (optional)**: in Client Portal, Performance and Reports, Flex Queries, create an Activity Flex Query with Cash Transactions and Trades, and under Flex Web Service create a token. Keep the token for step 6.
4. **Run the gateway on the VM** (once): download IBKR's Client Portal Gateway, then on the VM `java` must be installed. Start it with `bin/run.sh root/conf.yaml`. It listens on port 5000.
5. **Log in to the gateway**: from your computer, `ssh -L 5000:localhost:5000 ubuntu@84.13.132.198`, then open `https://localhost:5000` in your browser, log in with the **paper** user name and approve on your phone. Get the certificate fingerprint: `openssl s_client -connect localhost:5000 </dev/null 2>/dev/null | openssl x509 -noout -fingerprint -sha256`.
6. **Connect in ANUM**: Finance, IBKR trading, Paper account, Connect. Pick the gateway address (`127.0.0.1` when the gateway runs on the same host as the API, `ibkr-gateway` when it runs as a container on ANUM's network), paste the fingerprint, type the `DU` account id, optionally the Flex token and query id, tick "cash account". Never paste any of this in chat.
7. **Allowed ETFs and limits**: add a Shariah-screened ETF, check the limits (raise the per-order limit if one share costs more than USD 10), set a buy plan if you want one.
8. **Paper period**: at least 20 paper orders over at least 14 days. Press Read IBKR now after orders. Explain any difference. Try the kill switch, a cancel and the approval flow on phone and web. Compare ANUM's figures with the IBKR paper statement.
9. **Go live** (only after the paper proof shows green): the operator sets `ANUM_FINANCE_LIVE_ENABLED=true` and `ANUM_FINANCE_LIVE_BROKER=ibkr` and restarts the API, after your written go-ahead. Log in to the gateway with your **real** IBKR user, connect the Live account in ANUM, Read IBKR now, press Turn live on, type the code. The first live order can be approved 24 hours later.
10. **How to stop**: Stop everything (cancels every open order at IBKR, sells nothing). Turn live off (new orders go back to paper). Disconnect (erases the saved details). In an emergency, log in to IBKR directly and cancel there.

## Risks in plain words

* You can lose money you put in. Prices go down as well as up.
* Unrealised is the change in value of what you still hold; it moves every day and is not money lost or made until you sell. Realised is money actually made or lost on sales.
* The daily loss stop only stops new orders. It never sells, so it cannot turn a normal dip into a loss taken.
* Software, the gateway, IBKR or the network can fail. Every order is a limit order you approve, inside small limits, and the kill switch cancels at IBKR.
* With free delayed prices the band check uses a price about 15 minutes old.
* Taxes: for a Saudi resident, US dividends are normally withheld at 30 percent (no income tax treaty on the [IRS list](https://www.irs.gov/businesses/international-businesses/united-states-income-tax-treaties-a-to-z)), and US estate tax can apply to US assets above USD 60,000. Ask a tax adviser.
* Regulation: [regulation notes](../services/api/anum_api/finance/intel/skills/trading-risk/references/regulation.md) cover the Saudi CMA and the UAE CMA (formerly SCA, renamed 1 January 2026). ANUM is your own tool for your own account; offering it to others or managing others' money would need a licence.

## Security review

Reviewed in the build against the OWASP checklist (the `owasp-security` and `full-review` skills) and the repository rules; an independent person must still review before live (research report gate 8).

| Threat | Control | Test |
|---|---|---|
| Credentials leak | Form only, Fernet at rest, never returned, masked hint, erased on disconnect, no password field | `test_connecting_keeps_the_details_encrypted_and_never_returns_them`, `test_the_form_refuses_unsafe_or_wrong_details` |
| Server-side request forgery | Fixed gateway hosts and port, Flex hosts only, no redirects, path values checked | `test_credentials_only_allow_the_fixed_gateway_hosts_and_hide_in_repr`, `test_flex_statements_only_go_to_ibkr_flex_hosts`, `test_unsafe_path_values_are_never_sent` |
| Man in the middle on the gateway | Pinned certificate checked before any byte is sent | `test_the_pin_is_checked_before_any_request_byte_is_written` |
| Someone else trades | Owner only, payload hash, single use, expiry, owner-only RLS, database approver check | `test_only_the_workspace_owner_connects_or_trades_and_viewers_read_nothing_else`, `test_records_stay_with_their_owner`, PostgreSQL tests |
| Agent trades or prompt injection | Agents propose paper only; no agent approval; database refuses agent live intents | `test_an_assistant_can_only_propose_paper_orders` |
| Double send | Claim once, order row unique per intent, lookup by `cOID` before any resend | `test_a_timeout_is_looked_up_by_the_client_order_id_and_never_sent_twice`, `test_order_state_after_restart_while_submitting_is_unknown_then_looked_up` |
| Limits bypassed | Checked at approval and right before sending, ceilings in code and database | `test_every_hard_limit_blocks_an_approval_on_its_own`, `test_limits_are_checked_again_right_before_sending` |
| Live turned on by mistake | Flag, broker name, paper proof, typed code, cooling-off, each tested alone | `test_each_live_gate_blocks_a_live_order_on_its_own` |
| IBKR asks to confirm a risky order | Always declined | `test_a_question_from_ibkr_is_declined_never_confirmed` |
| Hung event loop | Plain `def` routes in worker threads; no transaction open across an IBKR call | Code review (`ibkr_service.py` module docstring) |

## Open items

* Fractional and cash-quantity orders through the API: not confirmed; whole shares only (see above).
* Which IBKR entity serves a Saudi resident, and whether OAuth (instead of the gateway) is offered to individual accounts: not confirmed.
* Shariah issuer pages, IBKR pages and the market data price could not be opened from the build session.
* The gateway is not yet part of the Oracle compose stack; the owner runs it on the VM (step 4). A compose service and an egress allow-list for IBKR hosts are the next infrastructure step.
* Alerts are in the IBKR tab only; push and email for them are not built.
* No background job: the gateway session is kept alive while the tab is open, and buy plans propose only when the owner opens the tab.
* An assistant tool for paper order proposals exists in the service (`create_order(..., requested_by="agent")`) but is not registered as an agent tool yet.
