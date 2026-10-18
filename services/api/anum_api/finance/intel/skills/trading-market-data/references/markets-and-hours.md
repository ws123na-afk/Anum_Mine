# Markets, instruments and hours

## Instrument types ANUM models

| Type | What it is | Notes for simulation |
|---|---|---|
| Share (equity) | Part ownership of a listed company | Trades on its exchange's sessions; corporate actions (splits, dividends) change prices and must be adjusted in the data |
| ETF | A fund traded like a share, usually tracking an index | Diversified in one instrument; still has a spread and fees |
| Currency (forex) | One currency priced in another | Over the counter, 24 hours from Sunday 17:00 to Friday 17:00 New York; ANUM values currencies only through recorded rates and does not trade them in strategies |
| Crypto asset | A token traded on crypto venues | Every day around the clock; a daily bar closes at 00:00 UTC; spot only in ANUM (no leverage, futures or shorting) |
| Commodity | A raw material, usually through futures or ETFs | Treated as daily bars of the instrument actually held |

## Terms

- **Bid / ask**: the best price buyers offer and sellers ask; the **spread** is the difference.
- **OHLCV bar**: open, high, low, close and volume over one period (ANUM uses daily bars).
- **Session**: the hours a venue trades. A **daily bar** exists only for a session that happened.
- **Corporate action**: split, dividend or merger; an unadjusted split looks like an abnormal move.
- **Survivorship**: today's list of listed instruments omits those that were delisted; testing only on survivors overstates past results.

## Venues and hours (local time)

| Venue | Hours | Days | Holidays |
|---|---|---|---|
| New York Stock Exchange and Nasdaq | 09:30 to 16:00 | Monday to Friday | Published calendar; ANUM stores 2026 and 2027 |
| London Stock Exchange | 08:00 to 16:30 | Monday to Friday | England and Wales bank holidays |
| Saudi Exchange (Tadawul) | 10:00 to 15:00 | Sunday to Thursday | Eid and national holidays announced each year: not known in advance |
| Dubai Financial Market, Abu Dhabi Securities Exchange | 10:00 to 15:00 | Monday to Friday (since January 2022) | Eid holidays announced each year |
| Currencies | 24 hours | Sunday 17:00 to Friday 17:00 New York | None |
| Crypto | Always | Every day | None |

Half days and auction phases are not modelled: early closes are simulated as full days, and that limitation is stated.

## Sources

| Source | Link | Source date | Licence | Limitations |
|---|---|---|---|---|
| U.S. SEC, Investor.gov glossary | https://www.investor.gov/introduction-investing/investing-basics/glossary | Accessed October 2026 | U.S. federal government work, public domain (17 U.S.C. 105) | U.S. retail focus; definitions are general |
| NYSE, Holidays and trading hours | https://www.nyse.com/markets/hours-calendars | Accessed October 2026; covers 2026 and 2027 | Copyright NYSE; facts cited, page not reproduced | Changes yearly; early closes not modelled |
| London Stock Exchange, Business days | https://www.londonstockexchange.com/equities-trading/business-days | Accessed October 2026 | Copyright LSEG; facts cited | Changes yearly |
| Saudi Exchange, trading hours | https://www.saudiexchange.sa | Accessed October 2026 | Copyright Saudi Exchange; facts cited | Holidays announced per year; check before each Eid |
| Dubai Financial Market and Abu Dhabi Securities Exchange | https://www.dfm.ae and https://www.adx.ae | Accessed October 2026 | Copyright the exchanges; facts cited | Holidays announced per year |
| Brown, Goetzmann, Ibbotson and Ross (1992), "Survivorship Bias in Performance Studies", Review of Financial Studies 5(4), 553-580 | https://doi.org/10.1093/rfs/5.4.553 | 1992 | Copyright the publisher; summarised, not reproduced | Studies of fund returns; the bias applies to any universe chosen today |
