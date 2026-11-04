# iPhone SMS banking and the double-entry ledger

Bank and card messages forwarded from the owner's iPhone by an Apple Shortcut are read, checked, de-duplicated and recorded in a double-entry journal inside the person's own Finance ledger ([Finance](finance.md)). Messages ANUM cannot read with confidence wait in a review queue, where the person corrects them and can teach ANUM the format. A ledger dashboard shows income, spending, balances, cards, loans, net worth and sync status from the journal.

**Off by default.** Nothing is accepted until the person runs the safety check and turns SMS forwarding on for their own ledger in that workspace. No bank format is claimed as supported: see [Banks](#banks).

Code: `services/api/anum_api/finance/sms_parser.py` (reading), `ledger.py` (journal and dashboard), `sms_service.py` (device tokens, ingest, posting, corrections), `sms_store.py` and `db/sms_repository.py` (storage), `sms_api.py` (routes), `sms_ai.py` (optional AI help); migration `0131_finance_sms_ledger`; contracts `packages/contracts/src/financeSms.ts`; web `apps/web/src/FinanceSms.tsx` (tabs `#finance-sms` and `#finance-ledger`).

## Audit: what existed and what was added

Checked before building (4 November 2026), so nothing is duplicated:

| Area | Existed | Added here |
|---|---|---|
| Ledger | Stage 2 single-entry ledger: accounts (bank, card, cash, loan, investment, last four digits only), signed transactions, CSV statement import with dedupe, categories, bills, debts, goals, holdings, per person and workspace under owner-only RLS ([Finance](finance.md#data-model)) | A double-entry journal (`finance_journal_entries`, `finance_postings`) on top of it; every posting on a finance account is also a stage 2 transaction, so the summary, safe-to-spend, Guardian, exports and the Flutter app keep working unchanged |
| Statements | CSV import (generic and UAE presets), undo | Reconciliation of an import against SMS-recorded transactions |
| Money and FX | `Decimal`, one rounding rule, per-currency totals, ECB and typed rates | FX kept on the entry: original amount and currency, the actual rate from the message, the fee |
| Credentials | Fernet vault for connector secrets; hashed link tokens for the customer portal (`business/portal_auth.py`); HMAC-signed automation webhooks | Device tokens in the portal's pattern: ids plus a 256-bit secret, SHA-256 at rest, shown once, rotatable, revocable |
| Approvals, audit, notifications | Payload-hash approvals; audit log; in-app notifications with email and push preferences | Audit rows for every device, settings, message and rule change and every ingest outcome (ids and codes only); in-app notices for review, duplicates, failures and unusual spending |
| SMS | Nothing: no SMS or bank-message API, model or screen existed | Everything on this page |
| Business invoicing | `docs/business.md`, separate tables | Untouched: personal and business books are never mixed |

Still not built (stated plainly): PDF statement import, bank APIs (Open Finance providers stay owner actions, [Finance](finance.md#owner-actions)), a Flutter screen for SMS review, and real bank formats (none are available to validate).

## How a message travels

1. The Shortcut posts `{"message": ..., "sender": ...}` to `POST /api/v1/finance/sms/ingest` with `Authorization: Bearer <device token>`, over HTTPS only.
2. **Who:** the token is resolved in its owner's own tenant, workspace and user scope; the secret is compared in constant time with the stored hash. The owner must still be an active member with `finance:manage` (a viewer's or a former member's devices stop working), and the workspace must not be archived. Nothing in the body can name a user, workspace or account: other fields are refused (422).
3. **Limits:** 30 messages a minute and 1,000 a day per device (429 with `Retry-After`).
4. **Refused without storing text:** one-time codes, verification and activation codes, passwords and PINs, promotions, and anything without money in it. Only a row with the status `rejected` and the reason code is kept, so the person can see that ANUM ignored something.
5. **Retries are safe:** the same device, sender, normalized text and minute make an idempotency key (SHA-256); a retried delivery gets the first answer and changes nothing. A processing failure keeps the masked message as `failed` and answers 503 with `Retry-After`; the retry processes it again.
6. **Reading** ([Parsing](#parsing)), then **resolving** which of the person's accounts it is about (the card and account numbers list, then an account's last four digits, then the default account).
7. **Duplicates:** a transaction already in the journal (same direction, amount, currency and key account; within 10 minutes when both messages give the time, else within 2 days; no conflicting bank reference) is stored as `duplicate` and posts nothing. When the second message names the account the first left in suspense (a card's "payment received" after the bank's "payment sent"), ANUM posts a clearing entry instead.
8. **Posting** only when the reading is confident, the format is confirmed by this person (or comes from a rule they taught) and auto-post is on. Everything else goes to the review queue with a reason code: `unknown_sender`, `format_unverified`, `missing_amount`, `unknown_kind`, `ambiguous_kind`, `account_unknown`, `loan_account_unknown`, `needs_converted_amount`, `currency_mismatch`, `auto_post_off`.

The answer is JSON the Shortcut can show: `status` (`posted`, `review`, `duplicate`, `rejected`, `test_ok`), `reason`, `message_id`, `replayed`, `text`.

## Parsing

`sms_parser.parse` is pure and deterministic: no clock, store, network or AI. It normalizes the text (NFKC, Arabic-Indic digits to Western, no control or direction characters, at most 1,000 characters), refuses codes and promotions, tries the person's own taught rules for that sender, then the bilingual engine: Arabic and English keywords decide the kind; regular expressions read the amount and currency (SAR, ر.س, ريال, AED, د.إ, درهم, USD and more), a second amount in another currency (the billed amount), the card or account's last four digits, "from" and "to" accounts, the merchant or other party, the reference, the date and time and the stated balance.

### Kinds

`salary`, `deposit`, `transfer_in`, `transfer_out`, `own_transfer`, `card_purchase`, `atm_withdrawal`, `card_repayment`, `loan_disbursement`, `loan_repayment`, `bank_fee`, `refund`, `reversal`, `unknown`. A foreign-currency transaction is any kind whose amount is in another currency than the account's.

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
| Salary, deposit, incoming transfer | the account | `income:<kind>` |
| Card purchase, outgoing transfer | `expense:<category>` (plus `expense:fees` for a stated fee) | the account (a card's debt goes up) |
| ATM withdrawal | the cash account, else `expense:cash` | the account |
| Bank fee | `expense:fees` | the account |
| Card repayment | the card (debt down) | the paying account; never an expense |
| Own-account transfer | the receiving account | the sending account; neither income nor expense |
| Loan proceeds | the account | the loan (debt up) |
| Loan repayment | the loan for the principal, `expense:interest` for interest | the account |
| Refund, reversal | the account | the original purchase's category (found by merchant within 120 days), else `expense:other` |

A side no message has named yet (the paying account of a card's "payment received") waits in `suspense:own_transfer`, shown as "in transit", until a later message names it. Stated balances are **observations** shown next to the ledger's figure, never posted.

Statement and hand-entered transactions have no stored entry; the dashboard builds their postings on the fly from their category, so every source is counted once.

## Review queue

`GET /sms/messages?status=review` lists messages waiting for the person. **Record** (`POST /sms/messages/{id}/post`) takes the corrected fields (kind, amount, currency, the billed amount for a foreign one, account, other account, merchant, category, date, reference, interest, fee) and records the entry; on a recorded message it reverses the old entry first. Options: `confirm_format`, `teach`, `allow_duplicate`. **Dismiss** drops a message that is not recorded; **Undo** reverses a recorded one.

## AI help

Off by default. It works only after the person turns it on with an explicit consent ("the masked text is sent to my workspace's own model"), only when they ask on one message, only with the workspace's own model and budget, and at most `ai.daily_cap` times a day (default 20). The model sees the masked text inside an untrusted-data block, and its answer only fills the correction form: it never records, confirms or teaches anything.

## Reconciliation

After a CSV statement import, `GET /sms/reconcile/{import_id}` matches each statement row with an SMS-recorded transaction on the same account with the same amount within 3 days. Confirming (`POST`) removes the statement's copy (the SMS entry stays) and marks the entry reconciled; statement rows no message reported stay as imported, and recorded messages missing from the statement are listed for checking.

## Dashboard

`GET /api/v1/finance/ledger/dashboard?month=&account_id=&currency=&category=&kind=`: income, expenses and surplus for the month; balances (with the bank's last stated balance and the difference); cards with what is owed, the limit and utilization; loans; assets, liabilities and net worth per currency (never added across currencies); spending by category and by merchant; the last 12 months and this year; money in transit; bills due; goals; debts with the months to clear them at the minimum payment; and sync status. `GET /ledger/entries` lists the journal.

**Personal and business:** each workspace has its own ledger per person, so books are never mixed. The ledger's kind (`personal` or `business`, Settings) labels it; keep personal banking in the workspace you use for yourself.

## The Shortcut

Apple gives apps no access to SMS history, so only messages that arrive after the automation is set up are forwarded; older ones come in as CSV statements or by pasting them (`POST /sms/paste`).

On the iPhone, after the safety check passed and a device token was created in ANUM (Finance, iPhone SMS):

1. Shortcuts, Automation, New Automation, **Message**.
2. Sender: choose your bank's sender names if they are available; Message Contains: leave empty. Choose **Run Immediately** and turn off "Notify When Run" if you like.
3. Add **If**: Shortcut Input, Sender, *contains* your bank's name (add one If per bank, or use Otherwise). This keeps personal messages on the phone.
4. Inside the If, add **Get Contents of URL**: the URL shown in ANUM; Method **POST**; Headers: `Authorization` = `Bearer ` followed by the token, `Content-Type` = `application/json`; Request Body **JSON**: `message` = Shortcut Input, Content; `sender` = Shortcut Input, Sender.
5. Test without real data: make a separate shortcut with the same Get Contents of URL and the JSON `message` = `ANUM TEST`, `sender` = `ANUM TEST`, and run it. ANUM answers "Connected" and records nothing; the device shows the time under Last success.

Limits to know: the token is stored inside the Shortcut (anyone with the unlocked phone can read it: rotate it in ANUM if the phone is lost, or disconnect the device); iOS may delay or skip an automation when the phone is locked or in Low Power Mode; a bank's sender name can differ between short codes, so check Last received after a real transaction; Shortcuts cannot sign requests, which is why duplicates are caught on the server.

## API

All under `/api/v1/finance`; `finance:read` reads, `finance:manage` changes; every row is the caller's own.

| Route | |
|---|---|
| `GET /sms/status`, `GET /sms/settings`, `GET /sms/banks`, `GET /sms/devices`, `GET /sms/messages`, `GET /sms/messages/{id}`, `GET /sms/rules`, `GET /sms/reconcile/{import_id}`, `GET /ledger/dashboard`, `GET /ledger/entries` | read |
| `PUT /sms/settings` (with `version`; turning on needs the safety check; AI help needs `ai_consent`), `POST /sms/self-test` | manage |
| `POST /sms/devices` (token shown once), `POST /sms/devices/{id}/rotate`, `/pause`, `/resume`, `/revoke` | manage |
| `POST /sms/paste`, `POST /sms/messages/{id}/post`, `/dismiss`, `/reverse`, `/suggest` | manage |
| `POST /sms/rules/{id}/activate`, `/disable`, `DELETE /sms/rules/{id}`, `POST /sms/reconcile/{import_id}` | manage |
| `POST /sms/ingest` | device token only ([How a message travels](#how-a-message-travels)) |

## Privacy and deletion

Every new table has forced RLS, the tenant policy, the restrictive owner policy and the tenant purge policy, and is listed in `tenant_lifecycle_tables.py`. "Delete my data" removes devices, messages, rules and the journal with the rest of finance; organisation and workspace deletion purge them. Device tokens are never stored, exported or logged.

## Owner steps after merge

1. Deploy as usual (migration `0131_finance_sms_ledger` runs with the release).
2. In ANUM: Finance, iPhone SMS: run the **safety check**, add your accounts and their last four digits, then turn SMS forwarding on.
3. Create a device token, set up the Shortcut ([The Shortcut](#the-shortcut)) and run the `ANUM TEST` shortcut.
4. Let one real message per bank arrive, check it in Review and record it with "This reading is right" (or teach the format). Until then nothing from that bank posts on its own.
