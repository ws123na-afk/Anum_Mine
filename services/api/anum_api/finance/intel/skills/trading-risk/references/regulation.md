# Regulatory and geographic restrictions

ANUM simulates, and since November 2026 it can also send owner-approved limit orders for US-listed ETFs to the owner's own Interactive Brokers account, behind every live lock (docs/finance-live-trading.md). These notes explain what matters for real trading and are not legal advice. Rules change: check the regulator's site and the date.

| Where | Regulator | What to know |
|---|---|---|
| United Arab Emirates, onshore securities | Capital Market Authority (CMA), called the Securities and Commodities Authority (SCA) until 1 January 2026 (Federal Decree-Laws 32 and 33 of 2025) | Licenses brokers for DFM and ADX; trade only through a licensed broker |
| Dubai, virtual assets | Virtual Assets Regulatory Authority (VARA) | Virtual asset service providers in Dubai (outside the DIFC) need a VARA licence |
| Dubai International Financial Centre | Dubai Financial Services Authority (DFSA) | Separate regime inside the DIFC |
| Abu Dhabi Global Market | Financial Services Regulatory Authority (FSRA) | Separate regime inside ADGM, including virtual assets |
| Saudi Arabia | Capital Market Authority (CMA) | Licenses Tadawul brokers. A Saudi resident using a foreign broker (for example Interactive Brokers) is served by one of that broker's foreign entities, not a CMA-licensed one: check which entity your account agreement names. The CMA opened a consultation on restricting algorithmic trading on 24 September 2026: wait for the final rules before any automated Tadawul orders. The Saudi Central Bank and the CMA warn that virtual currencies are not regulated in the Kingdom |
| Saudi residents, US securities | US Internal Revenue Service | The US has no income tax treaty with Saudi Arabia on the IRS treaty list, so US dividends paid to a Saudi resident are normally withheld at 30 percent; check with a tax adviser |
| United States | SEC and FINRA | Retail margin accounts that day trade often are "pattern day traders" with a 25,000 USD minimum equity (FINRA Rule 4210) |
| European Union | ESMA and national authorities | Retail leverage limits and restrictions on contracts for difference since 2018 |
| Crypto venues generally | Varies | Many venues restrict residents of some countries; check the venue's terms for your country of residence |

ANUM never grants withdrawal or transfer permissions and never holds broker credentials in prompts or memory. Its only live executor is the IBKR one: limit orders for US-listed ETFs in a cash account, each approved by the owner.

## Sources

| Source | Link | Source date | Licence | Limitations |
|---|---|---|---|---|
| Capital Market Authority (UAE), formerly the SCA | https://www.sca.gov.ae | Accessed October 2026 | Government publication; cited | Renamed 1 January 2026; rules change, check current circulars |
| UAE SCA renamed CMA (Regulation Tomorrow, January 2026; Cleary Gottlieb, 2026) | https://www.regulationtomorrow.com/2026/01/uae-sca-renamed-cma-and-other-landmark-legislations/ | January 2026 | Law firm publications; cited | Secondary sources |
| Saudi Exchange notice of the CMA algorithmic trading consultation | https://www.saudiexchange.sa | 24 September 2026 | Exchange publication; cited | Consultation, not final rules |
| IRS, United States income tax treaties A to Z | https://www.irs.gov/businesses/international-businesses/united-states-income-tax-treaties-a-to-z | Accessed November 2026 | Government publication; cited | Saudi Arabia is not on the list; check Publication 901 and an adviser |
| Virtual Assets Regulatory Authority (Dubai) | https://www.vara.ae | Accessed October 2026 | Government publication; cited | Dubai outside the DIFC only |
| Dubai Financial Services Authority | https://www.dfsa.ae | Accessed October 2026 | Regulator publication; cited | DIFC only |
| ADGM Financial Services Regulatory Authority | https://www.adgm.com | Accessed October 2026 | Regulator publication; cited | ADGM only |
| Capital Market Authority (Saudi Arabia) | https://cma.org.sa | Accessed October 2026 | Government publication; cited | Rules change |
| FINRA Rule 4210 (Margin Requirements), pattern day trader provisions (the research report notes an intraday margin model approved in April 2026; a cash account avoids margin either way) | https://www.finra.org/rules-guidance/rulebooks/finra-rules/4210 | Accessed October 2026 | FINRA rulebook; cited | U.S. margin accounts only; amendments under discussion |
| ESMA product intervention measures on CFDs (2018), later adopted by national regulators | https://www.esma.europa.eu | 2018 | EU publication; cited | EU retail clients only |
