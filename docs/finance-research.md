# Fund research: real data, an AI summary that can only cite the data, and backtests

Finance, Research looks into the funds the owner can trade: the price history, the worst falls and how long the price took to come back, how much it moves, fund facts, dividends, the Shariah status and recent news. Every figure shows where it came from and the date it describes; a figure that is missing says "Not available" and why. An AI summary reads only those figures, and ANUM rejects it if it states any number that is not in them. Simple rules can be tested on past prices, and a tested monthly rule can become a **paper** buy plan.

**This is research for your own decisions. It is not financial advice and there are no guaranteed returns. Prices go down as well as up, and past results do not predict the future.**

Code: `services/api/anum_api/finance/research_data.py` (sources and figures), `research_backtest.py` (backtests), `research_summary.py` (the AI summary and the number check), `research_service.py` (gathering, cache, paper run, portfolio), `research_api.py` (routes), the `finance.research` tool in `finance/tools.py`, and the web tab `apps/web/src/FinanceResearch.tsx` (its own lazy chunk). No new table: the AI summaries are stored as owner-only rows in `finance_agent_decisions` (`agent = market_research`, `action = symbol_research_summary`).

## Owner decisions this follows (2 November 2026)

Saudi tax resident, halal investing first, every order approved by the owner, USD 50 in total, long-term buy and hold ([Live trading](finance-live-trading.md#owner-decisions-2-november-2026)). So research starts with the Shariah-screened list (SPUS, HLAL, UMMA, SPRE, SPSK) plus any fund on the owner's allowed list, backtests use whole shares and the owner's limits, and dividends are counted after the 30 percent US withholding tax.

## Sources

Every source is reached only on fixed hosts, with no redirects followed, size caps and timeouts. Outside local and test, public sources go through the SSRF-guarded, DNS-pinned transport (`model_egress.PinnedEgressTransport`). No new secret is needed.

| Source | What it gives | Limits |
|---|---|---|
| IBKR historical bars, through the owner's own IBKR connection (`GET /iserver/marketdata/history?period=10y&bar=1w`) | Weekly closes for up to 10 years | Only when IBKR is connected and checked. Split-adjusted by IBKR, **not** dividend-adjusted. The path and its 10-year weekly limit come from IBKR's Web API reference: **NOT CONFIRMED** against a running gateway (IBKR's site could not be opened from the build session). IBKR may need market data permissions for some history |
| Stooq daily CSV (`https://stooq.com/q/d/l/?s=<symbol>.us&i=d`) | Daily closes, used only when IBKR gave no history | Free, no key, **personal use only**, no redistribution. Stooq does not state whether closes include dividends, so ANUM says "adjustment not stated by the source". Stooq may limit free downloads or ask for a key: then the page says so |
| SEC EDGAR (`www.sec.gov/files/company_tickers_mf.json`, `data.sec.gov/submissions/CIK##########.json`, the fund's newest `NPORT-P` `primary_doc.xml`) | Issuer (registrant), holdings count, top 10 holdings, net assets, the report date and a link to the report | Free, no key. The SEC asks every automated reader to send a contact e-mail in its User-Agent and to stay under 10 requests a second: the operator sets `ANUM_FINANCE_RESEARCH_CONTACT`; without it the fund facts say "Not available" and why. N-PORT reports are monthly and published with a delay |
| The server's private web search (SearXNG, `ANUM_SEARXNG_URL`, news category, last month) | Recent headlines: title, link, site and date | Untrusted outside text: shown with "ANUM has not checked them" and **never** passed to the AI model |
| The owner's IBKR Flex statement (already read by IBKR trading) | Dividends paid to the owner from this fund and tax withheld | Only after the owner connects Flex and reads a statement |
| Shariah list in code (`research_data.SHARIAH_FUNDS`) | Shariah-screened or not, the index and issuer the fund names state | ANUM shows the issuer's own label and **does not certify** any fund. The issuer's prospectus names its Shariah adviser |

The live system reached none of these hosts from the build session (its network blocks them), so the parsers were written to each source's documented format and are tested against fixtures in that format.

### Not available, and why

* **Expense ratio**: no free, machine-readable source is wired. The fund's prospectus states it. Shown as "Not available" with that reason.
* **The fund's own dividend history and yield**: none of the sources above gives it. Shown as "Not available"; the owner's own dividends appear once IBKR pays them.
* Anything a source did not answer is listed under Sources, "What did not answer", with the reason.

## Figures

* **Price change** over 1, 3, 5 and 10 years: total and per year, from the close on or before the start date to the last close. A period the history does not cover says when the history starts.
* **Worst falls**: the three biggest drops from a high to a low, with dates, the fall in percent, and the date the price first got back to that high and how many months that took, or "Not back yet".
* **Volatility**: the standard deviation of monthly price changes times the square root of 12, and the worst and best month. Needs 12 months.

The cache keeps gathered figures for `ANUM_FINANCE_RESEARCH_CACHE_MINUTES` (default 30) per tenant, workspace **and person**: finance is owner-only, and figures may come from the owner's own IBKR account, so one person's cache is never shown to another in the same workspace. Refresh gathers again.

## AI summary

"Write AI summary" asks the workspace's own model (the VM runs a real model through the OpenAI-compatible provider) through the model gateway, inside the workspace's monthly model budget, at most `ANUM_FINANCE_RESEARCH_SUMMARY_DAILY_LIMIT` (10) a day per person. The prompt holds only the gathered figures as an untrusted data block (no news, no links). The model answers in a fixed form: the good, the bad, the history, a realistic range of outcomes stated as uncertainty, a suggestion of buy, wait or avoid, and the reasons.

Before anyone sees it, `research_summary.check`:

1. reads the form (a suggestion other than buy, wait or avoid is refused);
2. finds **every number** the answer states and refuses the answer if any is not in the data it was given (`1,200` and `1200` are the same number; `66.8` is not `66.7`);
3. refuses any promise or prediction ("guaranteed", "risk-free", "will rise", "target price" and the like).

A refused answer is stored as **withheld** with the reason (and the numbers that were not in the data) and is never shown as text. The model is asked with no database session open; the outcome is written afterwards in its own short unit of work. The audit row (`finance.research.summary`) carries the outcome only: never a symbol or a figure. A summary written from older figures is marked so.

The suggestion is a plain opinion for a buy and hold investor. It is not advice and it never places or proposes an order.

## Backtests

`POST /backtests` replays one rule month by month on the real monthly closes (`research_backtest.py`, pure and deterministic):

| Rule | What it does |
|---|---|
| `monthly` | A fixed amount each month (dollar-cost averaging), buying whole shares |
| `buy_hold` | The whole amount at the start, then held |
| `rebalance` | Two funds: monthly money goes to the one below its target share; every 3, 6 or 12 months the mix is set back to the target (this sells some of the fund that grew) |
| `dip` | The monthly buying plus one extra buy when the price is a set percent below its highest close so far (once per fall, armed again at a new high) |

Rules applied: **whole shares only** (IBKR's API has no confirmed fractional orders), at most the per-order limit per order and never more money in than the total limit (both at most the owner's IBKR limits; higher values are refused), an IBKR-like commission estimate the owner can change (default USD 0.005 a share, minimum USD 1.00, capped at 1 percent of the order), each trade at the month's last close, and dividends, when the data has them, paid in cash after the US withholding tax (30 percent for a Saudi resident). Today no source gives fund dividend history, so the result says plainly that dividends are not included.

The result: final value, money put in, total return, the worst fall (time-weighted, so new money does not hide a fall), the months the account was worth less than the money put in, commission paid, orders, buys skipped because one share cost more than the order allowed, a month-by-month chart, the same money simply held in the first fund from the start, and the warning that past results do not predict the future.

## Paper run

A tested `monthly` rule can become a buy plan with "Create paper plan" (`POST /paper-run`, owner only). It is an ordinary IBKR buy plan ([buy plans](finance-live-trading.md#buy-plans)) marked `paper_only`: each month it **proposes** one paper buy into the owner's approval queue. It never sends anything itself, and:

* it is refused while live is on, and a paper-only plan proposes nothing while live is on (`ibkr_service.create_order(..., paper_only=True)` refuses a live intent);
* editing the plans in IBKR trading never drops `paper_only` (once set it stays set);
* the fund must be on the owner's allowed list first.

Other rules show "Only monthly buying can run as a paper buy plan today".

## Assistant

The read-only tool `finance.research` (`action_kind="read"`, low risk): with a `symbol`, the fund's figures with their sources and its Shariah label; with no symbol, how the owner's IBKR account is doing, exactly as IBKR last reported it (net value, today's profit or loss, unrealised and realised, holdings). The answer text is built from the data only, and adds a stored AI summary only when it passed the number check and matches the figures shown now. In chat, `finance.research` is a data-only tool (`chat_tools.DATA_ONLY_TOOLS`): no free model answer is written above its card, so no number that is not in the data can appear. The planner reads the user's own words: "research SPUS", "analyse HLAL", "ابحث عن SPUS", "how is my portfolio doing", "كيف حال محفظتي". The `anum.finance` skill's triggers include ETF, IBKR, Shariah, halal and the Shariah-screened tickers.

## API

All under `/api/v1/finance/research`. Reads, backtests and summaries need `finance:read` (owners and members; viewers have no finance). The paper run also needs the workspace owner, like every IBKR change. Routes are plain `def` (worker threads); outside sources and the model are called with no database session open.

| Route | What it does |
|---|---|
| `GET /symbols` | The owner's allowed funds then the Shariah-screened list, each with its Shariah label; mode (PAPER or LIVE), limits, the daily summary cap and the disclaimer |
| `GET /portfolio` | The owner's IBKR figures from the last read |
| `GET /symbols/{symbol}?refresh=` | The research report (cached); `refresh=true` gathers again (audited) |
| `POST /symbols/{symbol}/summary` | Ask for an AI summary: `ok`, `withheld` (with the reason) or `failed`; 409 no model, 429 daily cap or model budget |
| `POST /backtests` | Run one rule; 422 above the owner's limits, 409 not enough price history |
| `POST /paper-run` | `{symbol, amount, day_of_month}`: a paper-only buy plan; 201 |

## Operator settings

| Setting | Default | Meaning |
|---|---|---|
| `ANUM_FINANCE_RESEARCH_CONTACT` | empty | Contact e-mail sent to the SEC in the User-Agent (not a secret). Empty: SEC fund facts are not read |
| `ANUM_FINANCE_RESEARCH_CACHE_MINUTES` | `30` | How long gathered figures are kept per person |
| `ANUM_FINANCE_RESEARCH_TIMEOUT_SECONDS` | `20` | Timeout for each source and the model |
| `ANUM_FINANCE_RESEARCH_SUMMARY_ENABLED` | `true` | AI summaries on or off |
| `ANUM_FINANCE_RESEARCH_SUMMARY_DAILY_LIMIT` | `10` | AI summaries per person per UTC day, inside the workspace's model budget |

The API must reach `stooq.com`, `www.sec.gov` and `data.sec.gov` over HTTPS (the Oracle `egress` network allows it; add them to `appEgress` where egress is restricted).

## Tests

`services/api/tests/test_finance_research.py` (fake sources through `httpx.MockTransport`, the fake IBKR gateway, a fake model; real services are never called): figures with sources and dates, missing data with reasons, parsers refusing bad input and XML entities, Shariah labels, the number check, backtest math on fixed price series (monthly buying, commission minimum and whole shares, dividends with withholding, the dip and rebalance rules, too little history), the API's permissions, per-person cache and summary isolation, the daily cap, the model budget, no model, the paper run (owner only, paper-only kept, refused while live), and the assistant tool. Web: `apps/web/test/financeResearch.test.ts` and `apps/web/e2e/finance-research.spec.ts` (every state, English and Arabic, axe, at 1440x900, 1366x768, 1024x768, 768x1024 and 390x844).

## Open items

* Expense ratio and the fund's own dividend history: no free machine-readable source wired (both shown as "Not available"). A licensed fund data source, or parsing the funds' tailored shareholder reports (inline XBRL in N-CSR), would add them.
* The IBKR history path and Stooq's current free-download rules could not be checked from the build session.
* Messages that come from the server (reasons a source did not answer, Shariah index names, the AI summary text) are in English on the Arabic page.
* Backtests trade at month-end closes; real limit orders, spreads and settlement differ.
* `paper_only` is a new field in the stored IBKR settings: a rollback to a release before this one would refuse to read settings that hold a paper-only plan. Delete such a plan before rolling back.
