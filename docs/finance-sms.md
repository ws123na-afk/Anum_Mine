# iPhone SMS banking and the double-entry ledger

Bank and card messages and Apple Pay payments forwarded from the owner's iPhone by an Apple Shortcut are read, checked, de-duplicated and recorded in a double-entry journal inside the person's own Finance ledger ([Finance](finance.md)). Messages ANUM cannot read with confidence wait in a review queue, where the person corrects them and can teach ANUM the format. A ledger dashboard shows income, spending, balances, cards, loans, net worth and sync status from the journal.

**Off by default.** Nothing is accepted until the person runs the safety check and turns SMS forwarding on for their own ledger in that workspace. No bank format is claimed as supported: see [Banks](#banks).

Code: `services/api/anum_api/finance/sms_parser.py` (reading), `ledger.py` (journal and dashboard), `sms_service.py` (device tokens, ingest, posting, corrections), `sms_store.py` and `db/sms_repository.py` (storage), `sms_api.py` (routes), `sms_ai.py` (optional AI help); migration `0131_finance_sms_ledger`; contracts `packages/contracts/src/financeSms.ts`; web `apps/web/src/FinanceSms.tsx` (tabs `#finance-sms` and `#finance-ledger`).

## Audit: what existed and what was added

Checked before building (4 November 2026), so nothing is duplicated:

| Area | Existed | Added here |
|---|---|---|
| Ledger | Stage 2 single-entry ledger: accounts (bank, card, cash, loan, investment, last four digits only), signed transactions, CSV statement import with dedupe, categories, bills, debts, goals, holdings, per person and workspace under owner-only RLS ([Finance](finance.md#data-model)) | A double-entry journal (`finance_journal_entries`, `finance_postings`) on top of it; every posting on a finance account is also a stage 2 transaction, so the summary, safe-to-spend, Guardian, exports and the Flutter app keep working unchanged |
| Statements | CSV import (generic and UAE presets), undo | Reconciliation of an import against SMS-recorded transactions; PDF statements since 5 November 2026 ([PDF bank statements](finance-statements.md)) |
| Money and FX | `Decimal`, one rounding rule, per-currency totals, ECB and typed rates | FX kept on the entry: original amount and currency, the actual rate from the message, the fee |
| Credentials | Fernet vault for connector secrets; hashed link tokens for the customer portal (`business/portal_auth.py`); HMAC-signed automation webhooks | Device tokens in the portal's pattern: ids plus a 256-bit secret, SHA-256 at rest, shown once, rotatable, revocable |
| Approvals, audit, notifications | Payload-hash approvals; audit log; in-app notifications with email and push preferences | Audit rows for every device, settings, message and rule change and every ingest outcome (ids and codes only); in-app notices for review, duplicates, failures and unusual spending |
| SMS | Nothing: no SMS or bank-message API, model or screen existed | Everything on this page |
| Business invoicing | `docs/business.md`, separate tables | Untouched: personal and business books are never mixed |

Still not built (stated plainly): bank APIs (Open Finance providers stay owner actions, [Finance](finance.md#owner-actions)), the set-up on the phone (the safety check, devices and their tokens, see [On the phone](#on-the-phone)), and real bank formats (none are available to validate).

## How a message travels

1. The Shortcut posts `{"message": ..., "sender": ...}` to `POST /api/v1/finance/sms/ingest` with `Authorization: Bearer <device token>`, over HTTPS only.
2. **Who:** the token carries its **scopes** (`finance.sms`, `finance.wallet`, chosen when the device is connected; other iPhone integrations add theirs, all managed on Settings, Integrations, iPhone: [iPhone integrations](iphone-integrations.md)), and each ingest route accepts only a token with its scope (403 `scope_missing`). The token is resolved in its owner's own tenant, workspace and user scope; the secret is compared in constant time with the stored hash. The owner must still be an active member with `finance:manage` (a viewer's or a former member's devices stop working), and the workspace must not be archived. Nothing in the body can name a user, workspace or account: other fields are refused (422).
3. **Limits:** 30 messages a minute and 1,000 a day per device (429 with `Retry-After`).
4. **Refused without storing text:** one-time codes, verification and activation codes, passwords and PINs, promotions, and anything without money in it. Only a row with the status `rejected` and the reason code is kept, so the person can see that ANUM ignored something.
5. **Retries are safe:** the same device, sender, normalized text and minute make an idempotency key (SHA-256); a retried delivery gets the first answer and changes nothing. A processing failure keeps the masked message as `failed` and answers 503 with `Retry-After`; the retry processes it again.
6. **Reading** ([Parsing](#parsing)), then **resolving** which of the person's accounts it is about (the card and account numbers list, then an account's last four digits, then the default account).
7. **Duplicates:** a transaction already in the journal (same direction, amount, currency and key account; within 10 minutes when both messages give the time, else within 2 days; no conflicting bank reference) is stored as `duplicate` and posts nothing. Two messages that each state their own date are the same transaction only when the dates are the same: the same coffee at the same place on 3, 4 and 5 November is three purchases, and the same purchase twice 20 minutes apart is two. The 2 days are for a message that states no date (it is dated by when it arrived) and for a statement line that posts a day or two later. When the second message names the account the first left in suspense (a card's "payment received" after the bank's "payment sent"), ANUM posts a clearing entry instead.
8. **Posting** only when the reading is confident, the format is confirmed by this person (or comes from a rule they taught) and auto-post is on. Everything else goes to the review queue with a reason code: `unknown_sender`, `format_unverified`, `missing_amount`, `unknown_kind`, `ambiguous_kind`, `account_unknown`, `loan_account_unknown`, `needs_converted_amount`, `currency_mismatch`, `auto_post_off`.

The answer is JSON the Shortcut can show: `status` (`posted`, `review`, `duplicate`, `rejected`, `test_ok`), `reason`, `message_id`, `replayed`, `text`.

## Apple Wallet

Apple Pay (the Wallet "Transaction" automation) and a bank SMS often report the **same** payment. They are two **sources** of one canonical transaction (one journal entry), never two transactions (owner decision, 4 November 2026; `finance/wallet.py`).

- **Endpoint:** `POST /api/v1/finance/wallet/ingest` with the same device token. Body: `{"source": "apple_wallet", "amount": ..., "merchant": ..., "card": ..., "name": ...}`; nothing else is accepted, and there is no event time: ANUM stamps the server's reception time.
- **Amounts** may arrive as text with a currency symbol or code and either decimal mark (`SAR 125.00`, `125,00 ر.س`, `$1,234.56`, `1.234,56 €`, Arabic-Indic digits) or as a number. A negative amount is a refund. When only one kind of separator is written, one or two digits after it are decimals; three digits are decimals only for a currency with three (`KWD 1.250`) and thousands for the others (`SAR 1.250` is 1,250); without a currency that case is a guess and waits for review (`ambiguous_amount`), as do more decimals than any currency has. With no currency in the text, the mapped account's currency is shown (`currency_inferred`) but the event **waits for review** (`wallet_currency_missing`): the purchase may have been in another currency, and booking it in the card's currency would become a second purchase when the bank's message with the charged amount arrives. A currency that differs from the account's waits for review (`needs_converted_amount`).
- **Card mapping:** Apple Pay uses a virtual card number (the Device Account Number), so its last four digits differ from the physical card a bank SMS names. The owner maps each Wallet card or pass (its name as Wallet shows it, and optionally its Wallet last four) to the account its payments hit (Settings, Apple Wallet cards), and maps the physical card's last four to the same account (Card and account numbers). An unmapped card waits for review (`wallet_card_unknown`); ANUM never guesses.
- **Matching** (`wallet.same_payment`), never on the amount alone: the same account, direction, amount and currency; within 30 minutes when both carry a time (10 minutes when it is the same kind of source twice, rule 7 above: a bank's message sent again), else within 2 days (never across two different dates that both messages state themselves) and the merchants must share a meaningful word (generic banking words such as `purchase`, `online` or `شراء` never count: [how merchants agree](finance.md#how-matching-works)); the candidates are every posted entry of the same fingerprint within that window, read completely in a fixed order; merchants that do not disagree; no conflicting reference; and the existing transaction has no source of the same kind (a second Wallet event is a second purchase). One candidate attaches (status `attached`, reason `attached_to_existing`); more than one waits for review (`ambiguous_match`); none posts a new entry. Either source may arrive first, and a delayed SMS still attaches within the window.
- **Atomic:** ingestion for one person runs under a per-owner lock in the process and a PostgreSQL transaction-level advisory lock (`pg_advisory_xact_lock`), held only for the short unit of work that matches and writes (no model or provider call inside), so two reports arriving together cannot both post. A unique index allows at most one recorded or attached source of each kind per entry (`uq_finance_sms_messages_entry_source`), and the idempotency key makes a retried delivery a no-op.
- **Dashboard:** one expense; the journal lists the entry's sources (Apple Pay, bank message).

## Canonical transactions

Every payment is **one canonical transaction**, whatever reported it: an SMS, Apple Pay, a statement line or the person's own entry. Its id is `canonical_id` on the journal entry: the id of the first entry, kept by every reversal, correction and clearing entry of the same payment, so links from other modules (vehicles, purchases, documents, business P&L) point at `canonical_id` and survive corrections. `GET /api/v1/finance/ledger/transactions/{canonical_id}` returns the current entry, the whole chain and every source behind it.

- **Statement and hand-entered transactions are journalled too** (entry sources `statement` and `manual`, each linked to its stage 2 row). Editing such a row's amount, date or category reverses its entry and posts the new figures in the same chain; the row keeps its id. Deleting it, undoing its import or deleting its account reverses the entries. Rows recorded before this release have no stored entry and are counted from their category on the fly until they are edited.
- **Sources** are rows of `finance_sms_messages` with `source` `device` or `paste` (SMS), `wallet` (Apple Pay) or `statement`, linked by `entry_id`; at most one recorded or attached source of each kind per entry (unique index).

Other modules link to a canonical transaction instead of posting again, and their links follow its corrections, undo and reconciliation: [Finance links and business allocations](finance-links.md).

## Reconciliation on import

Importing a CSV or PDF statement ([PDF bank statements](finance-statements.md)) **reconciles instead of creating** through the [statement matching engine](finance.md#statement-matching) (under the owner's lock): each row is compared with the recorded transactions on that account (same direction, amount and currency, posted 0 to 3 days after the message, or the same foreign amount billed differently, which corrects the record to the billed amount). When exactly one matches with an agreeing merchant (or the entry's own words, such as "Salary") or reference, the row is attached to it as a `statement` source and the entry is marked reconciled; nothing is added. When it could be more than one, or only the amount, account and dates agree, the row waits in Review (`ambiguous_match`) and is not added: the person records it there or dismisses it. Everything else is imported as before. The import answers `reconciled` and `held_for_review`. An SMS or Apple Pay report that arrives after the statement attaches to the statement's transaction the same way. A PDF row also carries the bank's reference (an agreeing reference confirms a match, a conflicting one rules it out) and its printed time, which picks between candidates when the day alone leaves more than one; the time never turns a day match into a new transaction.

## Parsing

`sms_parser.parse` is pure and deterministic: no clock, store, network or AI. It normalizes the text (NFKC, Arabic-Indic digits to Western, no control or direction characters, at most 1,000 characters), refuses codes and promotions, tries the person's own taught rules for that sender, then the bilingual engine: Arabic and English keywords decide the kind; regular expressions read the amount and currency (SAR, ر.س, ريال, AED, د.إ, درهم, USD and more), a second amount in another currency (the billed amount), the card or account's last four digits, "from" and "to" accounts, the merchant or other party, the reference, the date and time and the stated balance.

### Kinds

`salary`, `deposit`, `transfer_in`, `transfer_out`, `own_transfer`, `card_purchase`, `atm_withdrawal`, `card_repayment`, `loan_disbursement`, `loan_repayment`, `bank_fee`, `refund`, `reversal`, `unknown`. A foreign-currency transaction is any kind whose amount is in another currency than the account's.

Each kind gives the stage 2 rows of the entry a [transaction type](finance.md#transaction-types) (source `sms`, `sms_service.sms_type`):

| Kind | Type of the row on the account |
|---|---|
| `salary` | `income` |
| `deposit`, `transfer_in` | `uncategorized` (held for review: money arrived, not why; a credit is not automatically income) |
| `transfer_out` | `expense`, filed by the other party's words like the same row from a statement (rent is housing, else `other`) |
| `card_purchase` | `expense` in the merchant's category |
| `own_transfer`, `atm_withdrawal` to the cash account | `internal_transfer` |
| `atm_withdrawal` with no cash account | `expense` (`cash`) |
| `card_repayment` | `card_payment` on both sides |
| `loan_disbursement` | `loan_received` |
| `loan_repayment` | `loan_principal` on the bank and the loan; the interest row on the loan is `interest` |
| `bank_fee` | `bank_fee` |
| `refund` | `refund` (`cashback` when filed as cashback) |
| `reversal` | `reversal` |

### Masking and retention

Account, card and IBAN numbers are cut to `***` and their last four digits (`mask`) before anything is stored. The masked text is kept for `body_retention_days` (default 30, 0 keeps none) for review and teaching, then erased; a refused message never keeps text (a database check). Logs never contain a message, an amount or a number; audit rows hold ids and codes only.

### Banks

**No bank format is verified.** The repository has no real bank messages and none may be committed, so the registry (`sms_parser.BANKS`: Al Rajhi, SNB, SAB, Riyad Bank, Alinma, Bank Albilad, ANB, BSF; Emirates NBD, FAB, ADCB, Mashreq, DIB, ADIB, CBD, RAKBANK) lists only the sender names a message is expected to come from, each `verified=False`, and all of them share the generic engine. There are no invented per-bank templates.

A bank becomes trusted for one person only by their own action:

- **Confirm the format:** when a reading from that sender is right, the person ticks "This reading is right" while recording it; from then on confident messages from that sender post on their own.
- **Teach a format:** for a message ANUM misread, the person corrects the fields and asks ANUM to learn it. ANUM builds a rule from the masked text (literal words escaped, other numbers generalized, the corrected fields as named groups; nothing the client sends becomes pattern syntax), checks that it reads the message back, and stores it as `proposed`. It reads nothing until the person activates it.
- **Allow a sender** that is not in the registry (Settings, other senders).

### Test fixtures

Tests use synthetic messages only, each marked `SYNTHETIC` in `tests/test_finance_sms.py`, written for this project and not copied from any bank. The self-test messages are synthetic too.

## Ledger

Every recorded message is a journal entry whose postings sum to zero in each currency (debits positive, credits negative). The database refuses an unbalanced entry when the transaction commits (a deferred constraint trigger) and refuses any update of a posting: a correction is a reversing entry plus a new one, and both stay.

| Message | Debit | Credit |
|---|---|---|
| Salary | the account | `income:salary` |
| Deposit | the account | `suspense:uncategorized` (not income until the person files it; before November 2026 `income:deposit`) |
| Incoming transfer | the account | `income:transfer_in` (a transfer: not counted as income) |
| Card purchase | `expense:<category>` (plus `expense:fees` for a stated fee) | the account (a card's debt goes up) |
| Outgoing transfer | `expense:<category>`, the other party's words deciding (rent is housing, else `other`): money sent to someone else is spending (audit C3; before November 2026 `expense:transfer`, a transfer) | the account |
| ATM withdrawal | the cash account, else `expense:cash` | the account |
| Bank fee | `expense:fees` | the account |
| Card repayment | the card (debt down) | the paying account; never an expense |
| Own-account transfer | the receiving account | the sending account; neither income nor expense |
| Loan proceeds | the account | the loan (debt up) |
| Loan repayment | the loan for the whole payment, and `expense:interest` for the interest (category `interest`, no longer `fees`) | the account, and the loan for the interest (charged on the loan) |
| Refund, reversal | the account | the original purchase's category (found by merchant within 120 days), else `income:refunds` (its own line, never income) |

A side no message has named yet (the paying account of a card's "payment received") waits in `suspense:own_transfer`, shown as "in transit", until a later message names it. Stated balances are **observations** shown next to the ledger's figure, never posted.

The stage 2 row of each account leg takes its category from the leg that balances it exactly: another own account makes it a transfer, so a loan repayment's bank row is a transfer and the loan's interest row is the only spending. The dashboard counts by the shared rules in [How figures are counted](finance.md#how-figures-are-counted): transfers are neither income nor expenses, cashback and unmatched refunds are their own lines, and the surplus is income plus those less expenses (the summary's net). Statement and hand-entered rows are read from the rows themselves by the current rules, so a row stored under older rules is counted the same way as a new one.

### Own transfers told by two messages

Some banks report one move between the person's own accounts as two messages: "transferred to (a name) from account 5678" and "received from (a name) to account 9999". Read alone, they are an outgoing and an incoming transfer. When the second one posts, `sms_service.pair_own_transfer` looks for exactly one opposite transfer of the same amount and currency on a different own bank, cash or investment account within 2 days; with one, both entries are reversed (the journal keeps them) and one `own_transfer` entry is posted in the first entry's canonical chain. The first message keeps that entry, the second is attached to it (`own_transfer_pair`). None or several candidates leave both as they are. The matching itself (`own_transfer_partner`) is pure, so the statement import can use it for its own rows.

Statement and hand-entered transactions recorded before this release have no stored entry; the dashboard builds their postings on the fly from their category, so every source is counted once ([Canonical transactions](#canonical-transactions)).

## Review queue

`GET /sms/messages?status=review` lists messages waiting for the person. **Record** (`POST /sms/messages/{id}/post`) takes the corrected fields (kind, amount, currency, the billed amount for a foreign one, account, other account, merchant, category, date, reference, interest, fee) and records the entry; on a recorded message it reverses the old entry first. Options: `confirm_format`, `teach`, `allow_duplicate`. **Dismiss** drops a message that is not recorded; **Undo** reverses a recorded one.

## AI help

Off by default. It works only after the person turns it on with an explicit consent ("the masked text is sent to my workspace's own model"), only when they ask on one message, only with the workspace's own model and budget, and at most `ai.daily_cap` times a day (default 20). The model sees the masked text inside an untrusted-data block, and its answer only fills the correction form: it never records, confirms or teaches anything.

## Manual reconciliation

For an import whose rows were added anyway, `GET /sms/reconcile/{import_id}` matches each statement row with an SMS-recorded transaction on the same account with the same amount within 3 days. Confirming (`POST`) reverses the statement copy's entry, attaches the line as a `statement` source of the recorded transaction and marks it reconciled; statement rows no message reported stay as imported, and recorded messages missing from the statement are listed for checking.

## Dashboard

`GET /api/v1/finance/ledger/dashboard?month=&account_id=&currency=&category=&kind=`: income, expenses and surplus for the month; balances (with the bank's last stated balance and the difference); cards with what is owed, the limit and utilization; loans; assets, liabilities and net worth per currency (never added across currencies); spending by category and by merchant; the last 12 months and this year; money in transit; `uncategorized_count` (the month's rows no rule recognised, in neither income nor expenses, shown as "N uncategorized, not counted"); bills due; goals; debts with the months to clear them at the minimum payment; and sync status. `GET /ledger/entries` lists the journal.

**Personal and business:** each workspace has its own ledger per person, so books are never mixed. The ledger's kind (`personal` or `business`, Settings) labels it; keep personal banking in the workspace you use for yourself.

## On the phone

Finance, More in Finance, **iPhone SMS review** and **Ledger** (`apps/mobile/lib/features/finance/finance_sms_screens.dart`). The review page shows forwarding (on or off, connected iPhones, the last message and problem, how many wait), **Paste a message**, the messages by status (Waiting for review first, every status, paged) and **Formats ANUM learned** with Switch on, Switch off and Delete. A message opens with its masked text and the fields ANUM read: change only what is wrong (an empty field keeps what was read, the web's `correctionBody`), tick **This reading is right**, **Learn this format** or, for a duplicate, **It is a separate transaction**, then **Record** (or **Record the correction** for a recorded one); **Ask the AI for the fields** appears only when AI help is on and the text is kept; **Dismiss**, or **Undo** with a confirmation for a recorded one. The ledger shows the month (previous and next) with the account, currency, category and kind filters, the figures per currency, forwarding, money in transit, rows left out of every figure, accounts, cards (limit used) and loans with what a message last said, spending by category and merchant, twelve months and the year, bills, debts and goals, and the journal with each entry's postings; an entry opens its chain of corrections (`GET /ledger/transactions/{canonical_id}`). Turning forwarding on, the safety check, connecting an iPhone (its token is shown once) and the Shortcut steps stay on the web, where the token can be copied to the iPhone's Shortcut. Tests: `apps/mobile/test/finance/finance_tools_test.dart`.

## The Shortcut

Apple gives apps no access to SMS history, so only messages that arrive after the automation is set up are forwarded; older ones come in as CSV statements or by pasting them (`POST /sms/paste`).

On the iPhone, after the safety check passed and a device token was created in ANUM (Finance, iPhone SMS):

1. Shortcuts, Automation, New Automation, **Message**.
2. Sender: choose your bank's sender names if they are available; Message Contains: leave empty. Choose **Run Immediately** and turn off "Notify When Run" if you like.
3. Add **If**: Shortcut Input, Sender, *contains* your bank's name (add one If per bank, or use Otherwise). This keeps personal messages on the phone.
4. Inside the If, add **Get Contents of URL**: the URL shown in ANUM; Method **POST**; Headers: `Authorization` = `Bearer ` followed by the token, `Content-Type` = `application/json`; Request Body **JSON**: `message` = Shortcut Input, Content; `sender` = Shortcut Input, Sender.
5. Test without real data: make a separate shortcut with the same Get Contents of URL and the JSON `message` = `ANUM TEST`, `sender` = `ANUM TEST`, and run it. ANUM answers "Connected" and records nothing; the device shows the time under Last success.

For Apple Pay: Shortcuts, Automation, New Automation, **Wallet** (Transaction), pick the cards, **Run Immediately**; add **Get Contents of URL** with the Apple Wallet address from ANUM (`.../api/v1/finance/wallet/ingest`), Method **POST**, the same two headers, Request Body **JSON**: `source` = the text `apple_wallet`, `amount` = Shortcut Input, Amount; `merchant` = Shortcut Input, Merchant; `card` = Shortcut Input, Card or Pass; `name` = Shortcut Input, Name. Then map the Wallet card to its account in ANUM. Check the first real payment in Review: Shortcuts may send the amount as text with a symbol or local separators, and ANUM shows when it took the currency from the mapped account.

Limits to know: the token is stored inside the Shortcut (anyone with the unlocked phone can read it: rotate it in ANUM if the phone is lost, or disconnect the device); iOS may delay or skip an automation when the phone is locked or in Low Power Mode; a bank's sender name can differ between short codes, so check Last received after a real transaction; Shortcuts cannot sign requests, which is why duplicates are caught on the server.

## API

All under `/api/v1/finance`; `finance:read` reads, `finance:manage` changes; every row is the caller's own.

| Route | |
|---|---|
| `GET /sms/status`, `GET /sms/settings`, `GET /sms/banks`, `GET /sms/devices`, `GET /sms/messages`, `GET /sms/messages/{id}`, `GET /sms/rules`, `GET /sms/reconcile/{import_id}`, `GET /ledger/dashboard`, `GET /ledger/entries`, `GET /ledger/transactions/{canonical_id}` | read |
| `PUT /sms/settings` (with `version`; turning on needs the safety check; AI help needs `ai_consent`), `POST /sms/self-test` | manage |
| `POST /sms/devices` (`{name, scopes}`, token shown once), `POST /sms/devices/{id}/rotate`, `/pause`, `/resume`, `/revoke` | manage |
| `POST /sms/paste`, `POST /sms/messages/{id}/post`, `/dismiss`, `/reverse`, `/suggest` | manage |
| `POST /sms/rules/{id}/activate`, `/disable`, `DELETE /sms/rules/{id}`, `POST /sms/reconcile/{import_id}` | manage |
| `POST /sms/ingest`, `POST /wallet/ingest` | device token only ([How a message travels](#how-a-message-travels), [Apple Wallet](#apple-wallet)) |

## Privacy and deletion

Every new table has forced RLS, the tenant policy, the restrictive owner policy and the tenant purge policy, and is listed in `tenant_lifecycle_tables.py`. "Delete my data" removes devices, messages, rules and the journal with the rest of finance; organisation and workspace deletion purge them. Device tokens are never stored, exported or logged.

## Owner steps after merge

1. Deploy as usual (migration `0131_finance_sms_ledger` runs with the release).
2. In ANUM: Finance, iPhone SMS: run the **safety check**, add your accounts and their last four digits, then turn SMS forwarding on.
3. Create a device token, set up the Shortcut ([The Shortcut](#the-shortcut)) and run the `ANUM TEST` shortcut.
4. For Apple Pay, add the Wallet automation and map each Wallet card (name and Wallet last four) and each physical card's last four to the same account.
5. Let one real message per bank arrive, check it in Review and record it with "This reading is right" (or teach the format). Until then nothing from that bank posts on its own.
