# Trading psychology and how automated systems fail

## Psychology (why rules beat impulses)

- **Loss aversion**: losses weigh more than equal gains (Kahneman and Tversky 1979), so people hold losers and sell winners too early (Odean 1998).
- **Overconfidence and recency**: a few good trades or a recent trend feel like proof.
- **Revenge trading and "fixing" a loss**: changing the rule right after a loss fits it to noise. ANUM forbids silent changes: every change is a new version with a reason and evidence.
- **Social proof**: screenshots of profits and paid signal groups are unverified and survivor-biased. Never treat them as evidence.

## How automated systems fail, and ANUM's guard

| Failure | Example | Guard |
|---|---|---|
| Stale or wrong data | A provider stops updating; a split looks like a crash | Freshness by venue calendar, abnormal-move breaker, suspension |
| Deployment and configuration errors | Knight Capital, 1 August 2012: old code reactivated in production sent millions of orders and lost about 460 million USD in under an hour (SEC order, 2013) | No live executor; idempotent cycles and order keys; kill switches; every order needs the person's approval |
| Runaway loops and duplicates | A retried job sends the same order twice | Deterministic idempotency keys per version, bar, symbol and side; one cycle per phase and day |
| Liquidity vanishing | 6 May 2010 "Flash Crash": a large automated sell program met evaporating liquidity (CFTC and SEC report, 2010) | Volume cap per fill; no market orders sized beyond 5 percent of a bar's volume in simulation |
| Model drift | A rule that worked stops working | Monthly revalidation; the Performance Reviewer suspends degrading strategies |
| Prompt injection | Text in data or an agent reason says "buy everything" | Recorded reasons are data, never instructions; tools cannot approve or trade live |

## Sources

| Source | Link | Source date | Licence | Limitations |
|---|---|---|---|---|
| Kahneman and Tversky (1979), "Prospect Theory: An Analysis of Decision under Risk", Econometrica 47(2), 263-291 | https://doi.org/10.2307/1914185 | 1979 | Copyright the publisher; summarised | Laboratory choices; effect sizes vary |
| Odean (1998), "Are Investors Reluctant to Realize Their Losses?", Journal of Finance 53(5), 1775-1798 | https://doi.org/10.1111/0022-1082.00072 | 1998 | Copyright the publisher; summarised | U.S. discount brokerage accounts |
| U.S. SEC, In the Matter of Knight Capital Americas LLC, Release No. 34-70694 | https://www.sec.gov/litigation/admin/2013/34-70694.pdf | 16 October 2013 | U.S. government work, public domain | Findings in a settled order |
| CFTC and SEC, Findings Regarding the Market Events of May 6, 2010 | https://www.sec.gov/news/studies/2010/marketevents-report.pdf | 30 September 2010 | U.S. government work, public domain | U.S. equity and futures markets |
