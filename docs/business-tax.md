# Business tax: VAT settings, tax codes, the VAT return and ZATCA e-invoicing readiness

Part of [Business books](business-books.md) (Phase 7 of the Finance upgrade, November 2026). Each business keeps its own VAT settings, its own rate table and its own e-invoicing chain. ANUM prepares figures for the owner and their accountant. **It never files a return and never calls ZATCA or the FTA.**

> **Not verified with ZATCA. ANUM does not claim compliance until the business completes onboarding and the invoices pass ZATCA validation.** The web shows this notice (English and Arabic) on the e-invoicing panel, next to the e-invoicing switch, and the invoice PDF prints it under the QR code.

## Contents

- [Sources](#sources)
- [Who can do what](#who-can-do-what)
- [Settings and the rate table](#settings-and-the-rate-table)
- [Tax codes](#tax-codes)
- [Posting](#posting)
- [The VAT return](#the-vat-return)
- [Filing deadlines](#filing-deadlines)
- [Warnings in the Accountant review](#warnings-in-the-accountant-review)
- [ZATCA e-invoicing readiness](#zatca-e-invoicing-readiness)
- [API](#api)
- [Web](#web)
- [Storage and rollback](#storage-and-rollback)
- [Tests](#tests)
- [What is not built](#what-is-not-built)
- [Owner steps for ZATCA onboarding](#owner-steps-for-zatca-onboarding)

## Sources

Read on 9 November 2026. **The authorities' own sites could not be opened from the build sandbox** (`zatca.gov.sa` and `tax.gov.ae` do not resolve there), so the documents below were identified and summarised through a web search of those sites, not read in full. Every requirement taken from them is marked as such in the code and here, and the owner's accountant or the ZATCA SDK must confirm it before anything goes live. Nothing beyond these summaries was assumed.

| Source | Where | Version or date | Used for |
|---|---|---|---|
| ZATCA, Guide to Developed FATOORA Compliant QR Code | `https://zatca.gov.sa/en/E-Invoicing/SystemsDevelopers/Documents/QRCodeCreation.pdf` | as listed in November 2026 | Phase 1 QR: tags 1 to 5 (seller name, VAT number, time stamp in ISO 8601, total with VAT, VAT total), each field a tag byte, a length byte (UTF-8 byte length) and the UTF-8 value, concatenated without separators, then base64 |
| ZATCA, E-invoicing Detailed Technical Guidelines | `https://zatca.gov.sa/en/E-Invoicing/Introduction/Guidelines/Documents/E-invoicing-Detailed-Technical-Guideline.pdf` | Version 2, November 2022 | previous document hash (PIH) equal to the hash of the last generated document; documents may be submitted out of order as long as the PIH chain follows generation order |
| ZATCA, Detailed Guidelines for E-Invoicing | `https://zatca.gov.sa/en/E-Invoicing/Introduction/Guidelines/Documents/E-Invoicing_Detailed__Guideline.pdf` | Version 2, May 2023 | Phase 1 QR fields; Phase 2 needs a CSID and a stamped XML with a 9-tag QR |
| ZATCA, Electronic Invoice Security Features Implementation Standards | `https://zatca.gov.sa/ar/E-Invoicing/SystemsDevelopers/Documents/20230519_ZATCA_Electronic_Invoice_Security_Features_Implementation_Standards_vF.pdf` | Version 1.2, 19 May 2023 | SHA-256 hashing, ECDSA stamp, XAdES (ETSI EN 319 132-1) signature, the QR carries the XML hash (32 bytes) |
| ZATCA, Electronic Invoice XML Implementation Standard | `https://zatca.gov.sa/ar/E-Invoicing/SystemsDevelopers/Documents/20230519_ZATCA_Electronic_Invoice_XML_Implementation_Standard_%20vF.pdf` | Version 1.2, 19 May 2023 | UBL 2.1 structure of standard and simplified invoices and notes (element list reproduced in `zatca.build_xml` from the standard's structure as summarised; the full required-fields table was not read) |
| ZATCA, E-Invoicing Implementation Resolution | `https://zatca.gov.sa/en/E-Invoicing/Introduction/LawsAndRegulations/Documents/20230519_E-Invoicing%20Implementation%20Resolution%20English.pdf` | 19 May 2023 | the previous document hash is a mandatory field |
| ZATCA, The Reverse Charge Mechanism Application (VAT circular) | `https://zatca.gov.sa/en/MediaCenter/Publications/Documents/VAT%20Circular%20-%20RCM-EN.pdf` | January 2023 | reverse charge VAT goes in field 9 of the return and is treated as deductible input tax |
| ZATCA VAT return guidance (via the guidelines above) | zatca.gov.sa Help Center guidelines | 2021 to 2023 | fields 1 to 6 sales, 7 to 12 purchases, 13 to 16 net VAT; rows 1, 7, 9 and 13 named. The remaining row names follow the published return form as commonly reproduced and **must be checked on the ZATCA portal** |
| NBR (Bahrain), VAT return filing: when to file | `https://www.nbr.gov.bh/VAT/vat_return_filing/when_to_file` (and the NBR VAT General Guide, `https://www.nbr.gov.bh/publications/view/VAT_General_guide`) | read through a web search on 10 November 2026 | Bahrain filing deadline: the return and payment are due by the last day of the month after the VAT period; monthly periods above BHD 3 million of taxable supplies a year, quarterly at or below it (annual on request below BHD 100,000) |
| Oman Tax Authority, VAT Taxpayer Guide: VAT Return Filing, and the VAT Law (Royal Decree 121/2020) | `https://tms.taxoman.gov.om/portal/documents/20126/1414820/VAT+Taxpayer+Guide+-+VAT+Return+Filing.pdf`; `https://tms.taxoman.gov.om/portal/documents/20126/1414820/VAT+Law+.pdf/9cbe8926-066b-d48d-2b7f-14f41b4c19a8` | guides of 2022 to 2024, read through a web search on 10 November 2026 | Oman filing deadline: the return is filed within 30 days after the tax period ends; the tax period is normally a quarter. Some Oman guides word it as "the 30th day of the month following the period": the owner's accountant confirms which applies, and the owner's own due day overrides it |
| Kuwait and Qatar VAT status (news and adviser summaries, for example `https://www.vatupdate.com/2026/07/24/kuwait-keeps-vat-off-the-table-no-adoption-in-the-2026-2030-fiscal-plan/` and `https://www.fiscal-requirements.com/news/5769-qatar-vat-status`) | web search | 10 November 2026 | Kuwait and Qatar have no VAT law in force: ANUM says so and invents no deadline. The governments' own publications were not read |
| FTA, VAT Returns User Guide and "VAT 201" user manuals | `https://tax.gov.ae/-/media/Files/EN/PDF/Guides/VAT-Returns-User-Guide.pdf`; `https://tax.gov.ae/DownloadOpenTextFile?fileUrl=en%2FVAT_VAT_Guides%2FVAT_Returns_form%2FProcess_the_VAT_201_returns_form_EN.pdf` | May 2018, September 2021 (V4.0) and the current EmaraTax manuals | VAT 201 boxes: 1a to 1g standard rated supplies by emirate, 3 reverse charge supplies, 4 zero rated, 5 exempt (paired with 4 in the manual), 6 imports (pre-filled), 7 import adjustments, 8 totals, 9 standard rated expenses, 10 reverse charge (recoverable), 11 totals, 12 to 14 net |

The KSA standard rate (15 % from 1 July 2020, 5 % before) and the UAE standard rate (5 %) seed the rate table as data; the owner's accountant confirms them.

## Who can do what

| Action | Who |
|---|---|
| Read the tax settings, the rate table, the VAT return, its documents and CSV, the e-invoicing panel and the generated XML | owners and members (`business:read`); viewers are refused |
| Change the tax settings (including the ZATCA switch), add or remove a rate, generate e-invoice data | owners and the business's accountants ([Accountants](business-books.md#who-can-do-what)) |
| Send anything to ZATCA | nobody: refused with `zatca_onboarding_required` |

## Settings and the rate table

`PUT /books/tax/settings` (books must be set up first): jurisdiction `SA`, `AE` or `OTHER`; registered or not; the registration number, checked by format (KSA: 15 digits starting and ending with 3; UAE TRN: 15 digits; other: 3 to 30 plain characters); the registration date (not in the future); the filing frequency (monthly or quarterly, with the first month of a staggered quarter cycle); the ZATCA switch (Saudi and registered only, **off by default**); the seller fields a ZATCA e-invoice needs (commercial registration number, street, building number, district, city, postal code); for an `OTHER` business the **filing country** (`country`, two letters; Saudi Arabia and the UAE are refused there with `country_has_jurisdiction`, they are their own jurisdictions); and the owner's own **VAT return due day** (`filing_due_days`, 1 to 120 days after the filing period ends, empty for the built-in one, see [Filing deadlines](#filing-deadlines)). Saving seeds the jurisdiction's default rows of the rate table once (`tax_rules.DEFAULT_RATES`).

The rate table (`business_tax_rates`) holds a code's rate from a day (to a day). Reports and the invoice editor read the table, never constants. A rate change is a new row; a row that has started cannot be removed (`rate_in_use`), only a future one.

## Tax codes

Every invoice line and every bill may carry one code (`tax_rules.TaxCode`): `standard`, `zero_rated`, `export`, `exempt`, `out_of_scope`, and for bills also `reverse_charge` (a purchase from abroad on which the business accounts for the VAT) and `import_customs` (VAT paid at customs). With a code an invoice line takes its rate from the table on the day it is priced (the code wins over a typed rate); a bill with a no-tax code must carry no supplier tax, and a reverse-charge bill gets its self-assessed VAT at the standard rate of its date (`reverse_charge_tax`).

Backward compatible: a line or bill without a code keeps working exactly as before (same journal fingerprint, same invoice approval hash) and is **unknown** for the return, which lists it (see below). Codes can be set later on drafts (`PATCH /invoices/{id}` with `tax_codes` by position) and on bills in an open period. Recurring bill templates have no code yet: the bills they make are unknown until coded.

## Posting

The journal of [#422](business-books.md#what-is-posted-from-what) already posted invoice tax to 2100 (VAT payable) and bill tax to 1200 (VAT receivable). This release extends those rules, it does not add new ones:

- the revenue legs of an invoice are split by line kind **and tax code**, the 2100 leg by tax code;
- a credit note's 4800 and 2100 legs are split in the invoice's own proportion by code (exact to the cent);
- a bill's cost and 1200 legs carry its code; a reverse-charge bill adds Dr 1200 and Cr 2100 for the self-assessed VAT (nothing more is owed to the supplier);
- each journal line keeps its `tax_code` (migration `0154_business_tax`).

A leg without a code keeps the fingerprint it had, so turning tax on never re-posts the existing journal.

## The VAT return

`GET /books/tax/return?from&to` reads posted journal lines dated in the period (`tax_return.compute`), in the base currency:

- net amounts from the revenue lines of invoices and credit notes and the cost (or fixed asset) lines of bills, VAT from their 2100 and 1200 lines, each by tax code. The monthly depreciation of a capital bill (6100, posted under the bill) is not a purchase and is left out: a capital bill of 12,000.00 plus 1,800.00 VAT counts once, in the period it was bought, and its 1,000.00 a month is in no box and no "without a tax code" row;
- a document without a code but with VAT counts as standard rated and is flagged **inferred**; without VAT it is **unknown**, left out of the boxes and listed;
- an event of an earlier period posted late (a closed month) or a reversal of one lands in the **adjustment** columns of the period it is posted in; a closed period never changes;
- KSA layout: rows 1 to 16 (row 2, 14 and 15 are entered on the ZATCA portal; row 9 VAT is due and recovered, as the form does); UAE: VAT 201 boxes 1 to 14 (box 1 for all emirates together; boxes 2 and 7 not tracked; box 6 pre-filled by the FTA); other: a summary by code;
- rows outside the return (out of scope, unknown, UAE zero rated and exempt purchases) below it;
- **warnings** instead of silent totals: tax not set up or not registered, changes not posted yet, documents without an exchange rate, draft bills, inferred and unknown codes, member-paid costs with no VAT split (the audit's "allocation rows carry no tax split": their input VAT is not claimed), manual entries on the VAT accounts (a VAT payment, a correction), too many records, the base currency not the filing currency, and the period not closed;
- a tie-out: VAT in the return plus manual entries against the movement on the VAT accounts.

`GET /return/documents?from&to&box&limit&offset` pages the documents behind a box; `GET /return.csv` exports the boxes, rows outside the return, the completeness flags, warnings, notes and every document.

## Filing deadlines

The Accountant review reminds of the VAT return of the last ended filing period (`tax_rules.return_deadline`, migration `0159_finance_last_gaps`). The filing period is always the business's own setting (monthly, or quarterly with its cycle month). The due day comes from, in this order:

1. **The owner's own due day** (`filing_due_days`, days after the period ends). It always wins over a built-in deadline. The owner said they will enter their details themselves, so this is the main setting: Books, Tax, Settings, **VAT return due day**.
2. **A built-in deadline** for the country (`tax_rules.FILING_PRESETS`, each with its source URL in the code and in [Sources](#sources)):

| Country | Built-in deadline | Usual period |
|---|---|---|
| Saudi Arabia (`SA`) | the last day of the month after the period | monthly or quarterly, as ZATCA assigns |
| United Arab Emirates (`AE`) | 28 days after the period ends | quarterly, or monthly when the FTA assigns it |
| Bahrain (`OTHER` + `BH`) | the last day of the month after the period | monthly above BHD 3 million of taxable supplies a year, else quarterly |
| Oman (`OTHER` + `OM`) | 30 days after the period ends | quarterly |

3. **Kuwait and Qatar** (`KW`, `QA`) have no VAT: the review says so (`no_vat_country`) and invents no deadline. If the owner still files a VAT return there, their own due day applies.
4. **Anything else** (another country, or `OTHER` without a country) has no built-in deadline, so the review shows one item, **Set your VAT filing period and due day in Tax settings** (`filing_deadline_missing`), and never guesses.

`GET /books/tax` answers `filing_deadline` (`source`: `owner`, `preset`, `no_vat` or `none`; the country, the due day and the preset's rule and source), `filing_presets`, `no_vat_countries` and `max_due_days`. Every deadline is a reminder only: the authority's portal shows the binding date, and ANUM never files.

On the phone, Business books, the menu, **Tax settings** (`apps/mobile/lib/features/books/books_more_screens.dart`) edits the jurisdiction, the filing period and cycle month, the filing country of Other and the VAT return due day, with the same checks as the web, and says under the form which deadline applies as it changes. It sends the registration and ZATCA fields back as saved (they are changed on the web). Members read it with who can change it. Tests: `apps/mobile/test/books/books_more_test.dart`.

## Warnings in the Accountant review

`business/tax_review.py` registers the tax module's producer in the [Accountant review](business-books.md#accountant-review) (kind `vat`, section Tax), for a business marked VAT registered only. It reads the last ended filing period and the current one and lists:

- `return_due_soon` / `return_overdue`: the ended period is not closed in the books and its deadline ([Filing deadlines](#filing-deadlines): the owner's own due day, else the built-in one for KSA, the UAE, Bahrain or Oman) is within 14 days or has passed. Closing the period once the return is filed clears it. The authority's portal shows the binding date.
- `filing_deadline_missing`: no due day of the owner's own and no built-in deadline: "Set your VAT filing period and due day in Tax settings". ANUM never guesses a deadline.
- `no_vat_country`: the filing country is Kuwait or Qatar, which have no VAT, and the owner set no due day.
- `missing_tax_number`: bills in the period carrying VAT whose vendor has no tax number saved.
- `missing_tax_code`: invoices, credit notes or bills without a tax code (the return's `inferred` and `unknown` documents).
- `vat_unreconciled`: the VAT accounts' movements do not tie to the VAT in the return.

Every item says ANUM never files a return and does not check compliance with ZATCA or FTA rules. Tests: `services/api/tests/test_business_finance_depth.py`, `tests/test_business_finance_last_gaps.py`.

## ZATCA e-invoicing readiness

Behind the per-business switch (off by default; Saudi, registered businesses only, enforced by the API and by a database check).

**Phase 1 (generation).** `zatca.phase1_qr` builds the TLV QR (tags 1 to 5 above), base64. It is printed on the PDF of a sent invoice with VAT of a business that turned e-invoicing on, drawn by ANUM's dependency-free QR encoder (`anum_api/qr_code.py`, byte mode, level M), with the not-verified notice under it.

**Phase 2 (integration) without credentials.**
- UBL 2.1 XML (`zatca.build_xml`) for a standard (buyer has a VAT number: subtype `0100000`) or simplified (`0200000`) tax invoice (388) and for a credit note (381, with the invoice as billing reference); the counter (ICV) and the previous document hash (PIH) as additional document references; tax totals by category (S, Z, E, O) and the lines.
- The hash: SHA-256 of the canonical XML, base64. The XML has no signature, QR or extension to strip. Canonicalisation uses the Python standard library (C14N 2.0), which gives the same bytes as C14N 1.1 for this XML (no comments, DTD or `xml:` attributes); to confirm with the ZATCA SDK during onboarding.
- The chain: `business_zatca_chains` holds the last counter and hash per business. Generation takes the business's books lock and the chain row `for update`, so two generations never share a counter (tested under PostgreSQL with concurrent requests); the database refuses a counter that does not move forward by exactly one and any change to generated data. The first document's PIH is base64 of the hexadecimal SHA-256 of "0", the value ZATCA's SDK samples show (to confirm at onboarding). A UUID per document.
- A required-fields check (`zatca.missing_fields`): seller name, VAT number, commercial registration number and address parts (building number of 4 digits, postal code of 5), buyer name and a structured buyer address for standard invoices (ANUM keeps the buyer's address as one text, so this is always listed), the VAT total in SAR for another currency, tax codes on every line, exemption reason codes for zero rated, exempt or out of scope lines (not collected yet), the billing reference of a credit note, and always the cryptographic stamp, the CSID and the Phase 2 QR.
- Integration: the `ZatcaGateway` interface (`clear` for standard invoices, `report` for simplified ones) has one implementation in this release, `NotOnboarded`, which refuses with the onboarding steps. No ZATCA endpoint is called and no key, certificate, OTP or CSID is stored or asked for.

## API

Under `/api/v1/business/books/tax`. Refusals: `{"detail", "code"}`. Audit rows (`business.books.tax_*`, `business.books.zatca_generated`) hold ids, codes and dates, never a registration number or an address.

| Route | |
|---|---|
| `GET ` (the base path) | read: settings, rates, choices, the last eight filing periods, the notice |
| `PUT /settings` | owners and accountants (also `country` and `filing_due_days`, [Filing deadlines](#filing-deadlines)) |
| `POST /rates`, `DELETE /rates/{id}` | owners and accountants |
| `GET /return?from&to`, `GET /return/documents?from&to&box&limit&offset`, `GET /return.csv?from&to` | read; at most three years |
| `GET /zatca?limit&offset` | read: readiness, the chain position and the sent invoices with their e-invoice data |
| `POST /zatca/generate` (`{source_kind, source_id}`) | owners and accountants; 201 once, then 200 with `replayed` |
| `GET /zatca/documents`, `GET /zatca/documents/{id}/xml` | read |
| `POST /zatca/documents/{id}/submit` | always 409 `zatca_onboarding_required` |

Invoices (`/api/v1/business/invoices`): manual lines take `tax_code`; `PATCH` takes `tax_codes` (one per line after the edit). Bills (`/books/bills`) take `tax_code` and answer `reverse_charge_tax`.

## Web

Business, **Expenses and P&L**, section **Tax** (`#business-books/tax`, `src/BusinessBooksTax.tsx`), in three parts: **VAT return** (filing periods from the settings or custom dates, the boxes by section with the adjustment columns when there are any, the documents behind each box in a drawer with paging, warnings, rows outside the return, the journal tie-out, notes, Export CSV), **Settings** (the registration form; for Other, the filing country; the VAT return due day with what applies without it: the built-in deadline, "no VAT" for Kuwait and Qatar, or none; the ZATCA fields and switch with the notice, the rate table with add and remove for future rows) and **E-invoicing (ZATCA)** (the notice, status, what is missing, the chain position, sent invoices with Generate XML, Download XML and Send to ZATCA, which shows the refusal, with paging). Bills and invoice lines (new, and kept contract or milestone lines when editing a draft) have a tax code select. Loading, empty with a next action, errors with Try again, members read-only with who can change it; English and Arabic, right to left.

## Storage and rollback

Migration `0154_business_tax` (expand only, after `0153_finance_reconciliation`): `business_tax_settings`, `business_tax_rates`, `business_zatca_chains`, `business_zatca_documents` (forced RLS, `tenant_isolation_` and `lifecycle_purge_` policies, listed in `tenant_lifecycle_tables.py`); `business_book_bills.tax_code` (nullable) and `reverse_charge_tax` (default 0, only with the reverse charge code); `business_book_journal_lines.tax_code` (nullable); the closed-period trigger also compares a bill's tax code and reverse-charge VAT; the forward-only counter and frozen-documents triggers (invoker functions). The previous release ignores the new columns and tables. Rollback: `alembic downgrade 0153_finance_reconciliation` (test databases only; production restores a backup, because dropping the columns loses the codes). Business records, like invoices: removed with the business workspace or the organisation. Migration `0159_finance_last_gaps` (expand only, after `0158_finance_depth`) adds the nullable `business_tax_settings.country` (two capital letters) and `filing_due_days` (1 to 120), both checked by the database; the previous release ignores them. Rollback: `alembic downgrade 0158_finance_depth`.

## Tests

`services/api/tests/test_business_tax.py` (registration numbers, rates by day, exact splits; settings for owners and accountants with the rate table seeded and the switch off; another business reading nothing; VAT on invoices and bills posted to 2100 and 1200 by code, the reverse charge; an uncoded invoice keeping its posting and approval hash; invoice lines taking the rate of their code; the KSA return from a mixed period with credit notes, inferred and unknown documents, imports and the reverse charge, drill-down paging and CSV; the UAE VAT 201 boxes; a closed period unchanged and a late document in the adjustment column; the return without settings; the QR TLV round trip with an Arabic seller name; the XML's required elements and a deterministic hash; e-invoicing off by default, the chain and counter, idempotent generation, the credit note, the refused submission; the counter under racing threads; the PDF QR only when on), `tests/test_business_finance_last_gaps.py` (built-in deadlines for KSA, the UAE, Bahrain and Oman, none for Kuwait, Qatar or another country, the owner's due day winning, the review asking for the period and due day, refused values, members and viewers refused), `tests/test_postgres_finance_last_gaps.py` (the new columns' database checks), `tests/test_qr_code.py` (every symbol decoded back with its BCH and Reed-Solomon checks), `tests/test_postgres_business_tax.py` (forced RLS and invoker triggers, the counter under concurrent requests, the database refusing a jump and any change to generated data, other businesses reading nothing, tax codes kept on journal lines), the audit walk in `tests/test_business_books.py`, web `test/businessTax.test.ts`, `e2e/business-books-tax.spec.ts` and `e2e/finance-last-gaps.spec.ts`.

## What is not built

- Filing, clearance and reporting: no return is filed and nothing is sent to ZATCA or the FTA.
- The cryptographic stamp (XAdES with ECDSA), CSID onboarding, the Phase 2 QR (tags 6 to 9) and the compliance checks with the ZATCA SDK. The canonicalisation and the first PIH value are to be confirmed with the SDK.
- Exemption reason codes (VATEX) on zero rated, exempt and out of scope lines; structured buyer addresses; the VAT total in SAR on invoices in another currency; debit notes (383).
- E-invoice data generated automatically when an invoice is sent (it is generated from the panel).
- KSA row 2, corrections (14) and credit carried forward (15); the UAE split of box 1 by emirate, tourist refunds and import adjustments.
- A VAT split on costs members paid with their own money (allocation rows): their input VAT is not claimed and the return says so.
- Tax codes on recurring bill templates and on manual journal entries.
- Partial exemption (input tax apportionment) and capital goods adjustments.

## Owner steps for ZATCA onboarding

When the owner decides to go live (not before; each step is the owner's, in ZATCA's systems):

1. With the accountant, check the rate table, the tax codes of recent invoices and bills, and a full VAT return against the ZATCA portal.
2. Fill in the seller fields in Books, Tax, Settings until the e-invoicing panel lists only the stamp, CSID and Phase 2 QR.
3. Generate e-invoice XML for a few test invoices and validate them with ZATCA's own SDK or the Fatoora developer portal's sandbox; report any difference (canonicalisation, PIH of the first document, required fields) so ANUM can be corrected.
4. On the Fatoora portal, generate the one-time password for each e-invoice solution unit. The CSR, the private key and the CSID must be created and kept in a secret store chosen then (never in chat, the repository or the database); onboarding support in ANUM is a separate change that needs the owner's go-ahead.
