# Finance matching: automatic links, Tamara installments, history backfill and capture health

A payment arrives (a bank SMS, Apple Pay, a statement row, a Gmail-imported Tamara schedule) and ANUM links it to the obligation it paid: a due date of a bill, a Tamara installment, a credit card, a bank loan's month. A link is the ordinary [finance link](finance-links.md) (`links.create`, the owner's lock, idempotency, never more than the payment); nothing here posts money or writes the journal. The engine links on its own only what the owner's approved policy allows and only when the evidence is strong and unique; everything else waits for the owner with its reason.

Code: `services/api/anum_api/finance/matching.py` (the pure engine), `match_policy.py` (the policy), `matching_service.py` (load, decide, link, record, accept, reject, undo), `backfill.py` (history), `capture_health.py`, `match_store.py` with the rows kept by `links_store.py` and `db/finance_links_repository.py`, `matching_api.py` (routes); migration `0184_pf_matching`; contracts `packages/contracts/src/financeMatching.ts`; web `apps/web/src/FinanceMatching.tsx` on Finance settings; phone `apps/mobile/lib/features/finance/finance_matching_*.dart` (Finance, More, Automatic matching). Tests: `services/api/tests/test_finance_matching.py` (engine), `test_finance_matching_flow.py` (API), `test_postgres_finance_matching.py` (RLS), `apps/web/test/financeMatching.test.ts`, `apps/web/e2e/finance-matching.spec.ts`, `apps/mobile/test/finance/finance_matching_test.dart`.

## The policy

`GET, PUT /api/v1/finance/automation/matching` (`AutomationPolicy.matching`, stored in `finance_automation_settings`). Everything is off by default.

| Switch | What it allows on its own |
|---|---|
| `match_transactions` | The main switch. Off: every match waits for review |
| `reconcile_installments` | Tamara (and other provider) installments |
| `link_recurring_bills` | Bills |
| `link_card_repayments` | A repayment into the card account of a credit card debt |
| `link_bank_loan_repayments` | One month's instalment of a bank loan (never the found-payments batch) |
| `prevent_duplicates` (on) | A payment that may be a second report of another is never linked on its own |
| `reprocess_history` | The backfill may apply (preview always works) |
| `notify_exceptions` | One in-app notice a day (kind `finance_exceptions`) while matches wait; the day is claimed in `finance_notice_claims` (`match_review`) |

Also: `min_confidence` (0.8 to 1, default 0.9), `review_confidence` (0.3 to 0.79, default 0.5), `settled_sources` (default bank statement and bank SMS) and `source_account_ids` (only debits on these accounts are linked on their own; none means review only). **Nothing links on its own until the owner approves the policy** (`approve: true`); any later change withdraws the approval unless the same request approves it again. Each save bumps the revision; the policy version (`pfm-1.r<revision>`) is stored with every decision. Saves are audited (`finance.matching.policy`, switches and counts only). The older `PUT /automation` never changes this part.

**Never automated** (`rules.NEVER_AUTOMATED`, refused in code): posting adjustments, deleting records, changing bank mappings, approving payments, changing periods, a bank loan's found-payments batch (`link_loan_payments`), repayments of loans with people (`link_person_loan_payments`, only ever suggested by the existing producer) and changing a policy (`change_policy`). Record linking (`rules.LINKING_ACTIONS`) became automatable because a link moves no money, writes no journal entry and is undone with `links.void` while the transaction stays.

## How a match is decided

The engine reads canonical transactions (`statement_ledger.records`: one payment whatever reported it, with its sources) and open obligations, and scores each pair on fixed weights (`matching.WEIGHTS`): the exact amount (or a part), the posting date against the due date (a day either side, an installment up to 3 days after), the provider's name in English or Arabic, the provider's order number in the bank text, a bill's merchant words, the payments the owner linked to the same bill before, the bill's paying account, the card or loan account the money went into, a bank reference.

Gates rule a pair out whatever else agrees: another currency without the bank's own conversion figures ([across currencies](#across-currencies)), money in, an own-account transfer (for anything but a card or loan), a card repayment for anything but its card (a repayment is never a second expense), a debit naming another provider (Tabby is never a Tamara installment), a date outside the window (an installment: 5 days before to 7 after, or 60 before to 30 after when the text quotes the order number).

Automatic only when all hold: the approved policy allows the kind, the score reaches `min_confidence`, the amount is exact, the payment is on a source account, the match is **unique both ways** (no other obligation for this payment and no other payment for this obligation at review level), and nothing flags it. Within one Tamara order the oldest unpaid installment is the one paid (providers collect in order). Two installments of the same amount on one day from two orders, with nothing to tell them apart, always wait: the record names the reason (`ambiguous_obligation`) and every candidate is listed so the owner picks one. A payment that pays exactly two or three installments of one order together is a combined payment; other combinations wait. A part payment waits (`partial_amount`); accepted, the installment shows partly paid with what is still due.

**Evidence states.** A link made on a settling source (`settled_sources`) is **verified** and the due date counts as paid. On any other source (Apple Pay alone, a hand entry) it is **provisional**: the link waits as `needs_review` (reason `provisional_match`), so the due date shows pending, never paid and never overdue, and no overdue reminder is sent for it. When a settling source of the same payment arrives (the bank SMS or the statement row attaches to the canonical transaction), the next run confirms the link and the record becomes verified. Without a link the obligation is **pending**.

**Card repayments against the statement.** When the card's debt holds its last statement (`card_statement_on` and `card_statement_balance` on the debt's profile, plus `card_statement_minimum` when known), every repayment to the card posted after the statement date is checked against it (`matching.card_coverage`): the repayments linked to the card since the statement, up to and including this one, are `full` (exactly the balance), `minimum` (exactly the minimum), `partial` (less) or `over` (more). The result is an evidence item on the match record (`card_statement_full`, `_minimum`, `_partial`, `_over`, with the figures) and an over-payment adds the flag `card_overpaid`; neither changes whether it links (an over-payment is a valid repayment that leaves the card in credit, so it is flagged for the owner, not held). A repayment on or before the statement date paid an earlier cycle and is not compared; a card without a stored statement says nothing. Repayments are checked oldest first, so two in one run add up in the order they were made.

**Signals.** A possible duplicate (the same account, amount and currency within 3 days, agreeing merchant or provider, different canonical transactions, no conflicting reference) is held (`duplicate_event`); the one already linked is the original. Money back of the same amount from the same merchant or provider within 45 days, or a reversal, flags the payment (`refund_signal`; the other leg of the same movement, such as a card repayment's credit on the card account, is never its refund): a new match waits, and an automatic link already made is flagged for the owner (Tamara schedules after a refund are handled by [Tamara refunds](finance.md#tamara-refunds)). A link removed on another page, or whose payment was corrected under it, makes the record `stale` (`link_removed`, `source_changed`); the engine never links that pair again on its own.

## Across currencies

A bill, installment or bank loan in one currency paid from an account in another (a USD installment from a SAR card) is matched only on figures the bank itself gave, read by the same reader as the transaction's [card FX details](finance.md#card-fx-and-fees) (`card_fx.details`): the original amount and currency printed in the SMS or on the statement row, the billed amount, the rate (printed, from a mapped column, or implied by the two amounts and labelled so) and an FX fee or markup. ANUM never looks up, stores or guesses a rate for this.

- **Compared:** the bank's original amount against what is still due, in the obligation's currency. Every other rule is the same (provider, order number, date window, uniqueness both ways, the policy, the source account, signals); exact and unique links on its own (reason `fx_original_amount`), a part waits (`partial_amount`), two obligations it fits wait (`ambiguous_obligation`).
- **Settled:** the link keeps the bank side as its own amount, in the account's currency (checked against the payment by the ledger as always, so a payment is never used twice), and records what it paid of the obligation in the obligation's currency (`settled_amount`, `settled_currency`) with the evidence (`fx`). The bill schedule, installment status, debt payments and balances read the settled amount (`links.settled`); two currencies are never added together.
- **Fees apart:** an FX fee or markup the bank included in the billed amount is left out of the bank side, so it stays on the payment as a bank fee and is not part of what the obligation was paid; a separate fee row is its own transaction. The payment itself is spending once, in the account's currency, whatever it is linked to.
- **No original amount:** a debit in the account's currency that names the provider, the order, the bill's merchant or the loan account, but gives no original amount, waits with `fx_evidence_missing` and is never linked on its own. Accepting it is the owner's statement that this whole debit paid what was due: the link settles the due amount and records no rate (`basis: owner_confirmed`). A part of a foreign purchase is never split across obligations.
- **Card repayments across currencies** (gap/finance, November 2026): a card in one currency repaid from an account in another is repaid on the bank's printed original amount in the card's currency, by the same rules (the card's own account or name, a repayment, the policy); the card's statement check counts it in the card's currency. Without the original amount it waits with `fx_evidence_missing` and the record keeps the bank side (what it paid of the card is not known); accepting it is refused with that reason (import the card's statement or a bank message with both amounts, run matching again, or reject it).
- **Combined payments across currencies:** two or three obligations in the currency the bank printed the original amount in, adding up to that amount exactly and unique, as for one currency. Each part's bank side is its share of the purchase at the bank's own conversion (the last part takes the rounding, so the parts add up to the purchase exactly); the FX evidence goes with each part. A foreign purchase without the original amount, or partly linked already, is never combined; its obligations wait with `fx_evidence_missing`.
- **Provider emails:** Tamara's emails and statement give the installment in its own currency (the obligation), never what the card was charged, so they are not conversion evidence on their own.

Where the owner sees it: each match (web Finance settings, Automatic matching; phone Finance, More, Automatic matching) shows "Pays USD 100.00 with SAR 375.00", the original and billed amounts, the rate with its kind and the fee kept apart, or says the bank gave no original amount; the transaction's currency details list the bills or debts it paid in another currency. Migration `0188_pf_fx_matching` (nullable columns on `finance_links` and `finance_match_records`); tests `services/api/tests/test_finance_matching_fx.py`, `test_postgres_finance_matching_fx.py`.

## Records, undo and the owner's choices

`finance_match_records`: one per person, canonical transaction and target (unique), with the outcome (`auto_linked`, `review`, `accepted`, `rejected`, `undone`, `stale`), the evidence state, the reason code, the confidence, the evidence, the flags, the number of competing candidates, the policy version, the run and the link. A rerun updates the record; an owner's choice (accepted, rejected, undone) is never changed by the engine. Every automatic link is audited (`finance.matching.auto_link`, actor `anum.automation`, ids and codes only).

| Route | |
|---|---|
| `POST /matching/run` | Match the last 45 days now (the daily job does the same) |
| `GET /matching/records?outcome=&kind=&run_id=` | The decisions, with counts per outcome |
| `POST /matching/records/{id}/accept`, `/reject`, `/undo` (`{version}`) | Link through `links.create`; never propose again; void the link and keep the transaction |
| `GET /matching/installments` | Every provider order with its installments and match status |
| `GET /capture-health` | SMS and Apple Pay ingestion health |

### One place for each match

The engine's records are the one source for a payment and obligation pair. The Suggestions queue's other producers read them (`suggestions.Snapshot.match_records`): the bills producer leaves out a bill due date whose payment waits in the engine's review (`review` or `stale`), so the owner sees it once, in Matches, and never proposes a payment for a due date the engine already decided for that pair (linked, rejected or undone; after a rejection the due date shows as an open item without that payment); the debt attribution producer skips a payment and debt pair the engine holds (card repayments, bank loan months). Transaction detail shows the engine's stored evidence state on a link it made or the owner accepted (`evidence_source: matching`, with `match_id`), and the display rule only for links made by hand ([Transaction detail](finance.md#transaction-detail)).

**Card repayments and the statement, whoever linked them** (gap/finance): a card repayment linked by hand from Debts (Match a payment) is compared with the card's last statement like an engine link: a record of the pair waiting for review (or rejected before) becomes `accepted` with the hand link, and a pair with no record gets one (`accepted`, reason and evidence `hand_link`, the owner's policy version `owner`) when the card has a stored statement. Unmatching it from Debts sets the record `undone`. Accepting a card match later works the comparison out again (repayments linked since then count), instead of keeping the one from when the record was made (`matching_service.card_check`). Tests `services/api/tests/test_finance_gap_matching.py`.

A payment recorded by the iPhone (SMS or Apple Pay) is matched right after ingestion commits, in its own short units of work, when the policy is on; a failure there never fails the ingestion (the daily job retries). Every route is a plain `def` route (thread pool, never the event loop) and needs finance read or manage.

## Tamara

What ANUM reads: the Gmail schedule, repayment and refund emails (`tamara_email.py`, SAR and AED) and the CSV statement, both approved by the owner before anything is saved ([Gmail receipts](finance.md#gmail-receipts-alongside-manual-and-csv-import)). Each scheduled installment is a one-time bill whose notes carry `Order:` and `Installment:`; the engine reads the order and number from them and the provider from the statement row. A bank debit naming Tamara on the installment's due date, for its exact amount, from a source account, with no other open installment of that amount, is linked to that order and installment; the bill schedule, upcoming payments, Safe to spend and reminders then count it paid. A refunded installment (by the statement or an applied refund) is not an obligation; an installment the provider reports paid but whose bill was paused can still be verified by its bank debit.

**Installment status** (`GET /matching/installments`, web Finance settings, phone): verified paid, partly paid, provisionally matched, a match waits for you, paid per Tamara with no bank payment found, refunded, overdue, due soon, upcoming, not in Bills.

### Counting installment purchases

One rule (`spending_policy: installments_when_paid`): an installment purchase counts as spending when each installment's payment leaves the owner's account, under that payment's category. The Tamara order itself is a schedule, never a transaction, so the purchase is never counted twice.

### Other providers

`matching.register_provider` is the seam. Tamara is the only provider ANUM reads (`reads: gmail, statement_csv`). Tabby, Postpay and Spotii are recognised in bank text only, so their debits are never taken for a Tamara installment; ANUM reads nothing from them and the policy page says so.

## History

`POST /matching/backfill` (`mode`: `preview` or `apply`), `POST /matching/backfill/{id}/continue`, `GET /matching/backfill[/{id}]`, `POST /matching/backfill/{id}/undo`. A run covers all history, from the oldest recorded payment to today, one calendar month per batch, at most 6 months per call; after each month the checkpoint (`cursor_on`) and counts are saved, so a run that stops resumes after the last finished month. A month that fails is retried by the next call and fails the run after 5 attempts. The daily job carries on a running run. One running run per person (a partial unique index).

Preview changes nothing and counts what apply would do: payments read, matched, linked automatically (verified and provisional), ambiguous, waiting for you, no source record (rows from before the journal, which no link can point at), possible duplicates, corrected, refused by the ledger, already linked, the owner's choices kept, errors. Apply needs the approved policy with `reprocess_history` on and runs the same engine, so a second run creates nothing new. Undo voids every link the run made (the transactions stay) and marks those records undone.

## Capture health

`GET /capture-health`, read only from what ingestion keeps: each device (name, state, scopes, last seen, last accepted, last refusal code; never the token), per channel (bank SMS, Apple Pay) the last captured and last failed message, refusals of a known device in 7 days and messages waiting with their reasons, unknown senders, Apple Pay payments waiting for a card mapping, the card and Wallet card mappings, accounts no message is mapped to, and gaps of 72 hours or more with nothing captured in the last 30 days. Never a message body, a token, a full card number or a balance.

**What iOS Shortcuts can and cannot expose.** The SMS automation passes the message text and sender only for messages the automation matched and only when it runs (some iOS versions ask to confirm, and the phone must be on); Apple Pay's Wallet automation gives the amount, merchant, card name and a name, with no time and no account number. ANUM cannot see a message the Shortcut never sent, and a request with an unknown or wrong token is refused before ANUM knows whose it is, so it is never recorded against anyone. Gaps show where capture may have stopped.

## Storage and privacy

Both tables have forced RLS with the tenant policy, a restrictive owner-only policy (admitting the purge role) and the purge policy, and are in `tenant_lifecycle_tables.py`. The privacy export includes them with the links; "Delete my data" removes them.
