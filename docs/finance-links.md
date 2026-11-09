# Finance links and business allocations

One payment is **one canonical transaction** in the owner's Finance ledger ([iPhone SMS and the ledger](finance-sms.md#canonical-transactions)), whatever reported it: a bank SMS, Apple Pay, a statement line or the owner's own entry. Other modules (vehicles, purchases, the document vault, trips, business projects and the business books) **never post money**. They link to the canonical transaction. A link is a view of the one posting, so a fuel payment can be a vehicle cost **and** a business expense without a second journal entry.

Code: `services/api/anum_api/finance/links.py` (rules), `links_store.py` and `db/finance_links_repository.py` (storage), `link_hooks.py` (following corrections), `links_api.py` (routes); migration `0134_finance_links`; contracts `packages/contracts/src/financeLinks.ts`. Tests: `tests/test_finance_links.py`, `tests/test_postgres_finance_links.py`.

## What a link is

| Field | |
|---|---|
| `canonical_id` | The canonical transaction (`finance_journal_entries.canonical_id`), which every correction keeps. Never a stage 2 transaction id or one entry's id. |
| `dimension` | `vehicle`, `purchase`, `document`, `business`, `trip`, `project`, `bill` (a payment matched to one due date of a bill) `debt` ([Bills and budgets](finance.md#bills-and-budgets)), `person_loan` (money lent to or borrowed from a person and each repayment, [Lent and borrowed](finance.md#lent-and-borrowed)), `payslip` (the salary credit a payslip paid, [Payslips](finance.md#payslips)) or `remittance` (the payment for a transfer home, [Sending money home](finance.md#sending-money-home)). Each dimension is its own view: the vehicle part and the business part of one payment are counted separately. |
| `role` | `payment`, `refund`, `deposit` or `evidence` (a receipt or document: no amount). |
| `target_type`, `target_id` | The record in the module (`vehicle` / `veh_...`, `vault_document` / `doc_...`). For `business` it is the business workspace. |
| `amount`, `currency` | The part of the payment this link covers, in the payment's own currency (never converted). |
| `status` | `active`, `needs_review` (the payment changed under it) or `void` (removed, or the payment was undone). |
| `source_event_ids` | The SMS, Apple Pay and statement events behind the payment when the link was made (audit trail). |
| `idem_key`, `version`, `created_by` | Idempotency (a retried request returns the first link, `replayed: true`), optimistic concurrency and who made it. |

## Rules

- **Never more than the payment.** Active links of one dimension add up to at most the payment's debits in that currency (for a SAR 150 fuel purchase: SAR 150 of vehicle links and, separately, SAR 150 of business allocations). Checked under the owner's lock when the link is made (`409 over_allocated` with `remaining`) and again by the database when the transaction commits (a deferred constraint trigger, `finance_links_within_amount`).
- **No races.** Every link write takes the same locks as ingestion and corrections: the per-owner lock in the process and the PostgreSQL transaction-level advisory lock (`pg_advisory_xact_lock`). Concurrent attempts cannot exceed the amount (tested with parallel requests in PostgreSQL).
- **Never a posting.** No link writes `finance_journal_entries`, `finance_postings` or `finance_transactions`. The acceptance tests compare the whole journal before and after linking and allocating.
- **Modules register their targets.** `links.register_target(dimension, check)` lets a module say which of its records exist and belong to the owner. A dimension with no registered module answers `409 target_unavailable`; a module that registers with `single_payment` (vehicles, purchases) gets at most one live payment link per target, from any route (`409 target_linked`), and an `observer` that keeps its read copy of the link current in the link's own transaction, and a `vet` that refuses, on every route, a role or kind of transaction its targets do not take (purchases: a refund must give money back to a spending category); a target the check refuses answers `404 target_not_found`. On this release `business` is built in and `document` is connected to the document vault (`vault_document` targets the owner may edit; the vault's finance link becomes an `evidence` link through `VaultLinkAdapter`); [vehicles](vehicles.md#payments) and [purchases](purchases.md#payments-and-refunds) register theirs (a purchase's payment, and refunds with role `refund` on the return of the original purchase); trips and projects register in their own PRs.
- **Same currency.** A link in another currency than the payment is refused (`422 currency_mismatch`, with the payment's currencies).

## Corrections, undo and reconciliation

`sms_service.reverse` notes every canonical transaction it reverses, and statement reconciliation notes a statement copy merged into a recorded payment (`link_hooks.py`). Before that unit of work commits, `links.settle` brings the links in line, in the same transaction and under the owner's lock:

- **Corrected, same figures:** the links stay `active` on the same `canonical_id`.
- **Corrected to a smaller amount (or another currency):** the links of a dimension that no longer fits become `needs_review`; the owner confirms one that fits again (`POST /links/{id}/confirm`) or removes it. A business allocation shows `needs_review` to the business's members too.
- **Undone** (no live entry left in the chain): every link becomes `void` (`status_reason: transaction_undone`) and business allocation rows are removed.
- **Reconciled:** links on the statement copy move to the recorded payment (`moved_on_reconcile`), each keeping a key of its own. A non-business link identical to one the recorded payment already has is voided as merged. A business allocation is never merged away: it moves with its row (the row's source reference follows), and if the business dimension is now over the payment, all its allocations wait in review.

## Business allocations

Owner decision 1 (4 November 2026): the personal ledger stays private, and a business sees **allocations**.

The owner allocates a personal transaction, or part of it, to a business workspace where they hold `business:write` (an active membership in the same tenant, the workspace not archived). In one transaction ANUM writes the link (`business` dimension, in the owner's personal scope) and a row in the business workspace, `business_ledger_allocations`: date, amount, currency, category, project, memo, treatment (`owner_contribution` or `reimbursable`), who allocated it and a six character source reference. Never the bank, the account, the card, the merchant text or the message.

One payment can be split across a business's projects (one allocation per project; the total stays within the payment). A repeated request for the same business and project returns the first allocation.

Members of the business workspace with `business:read` read the rows (`GET /api/v1/business/ledger-allocations`, with totals per currency, never added across currencies). They cannot read the owner's journal, links or messages: those tables keep their restrictive owner policy. Removing the allocation, undoing the payment or the owner's privacy deletion removes the row. The same refusal (`403 workspace_not_allowed`) answers a workspace that does not exist, is in another tenant or where the owner cannot write.

## API

| Route | |
|---|---|
| `GET /api/v1/finance/links` (`canonical_id`, `dimension`, `target_type`, `target_id`, `status`, paging) | `finance:read`, own rows |
| `GET /api/v1/finance/links/transactions/{canonical_id}` | the payment's amounts, what is left per dimension, and its links |
| `POST /api/v1/finance/links` (optional `Idempotency-Key` header) | `finance:manage`; 201, or 200 with `replayed: true` |
| `POST /api/v1/finance/links/{id}/void`, `/confirm` (`{version}`) | `finance:manage` |
| `POST /api/v1/finance/allocations`, `DELETE /api/v1/finance/allocations/{link_id}` | `finance:manage`, plus `business:write` in the target workspace |
| `GET /api/v1/business/ledger-allocations` | `business:read` in the business workspace |

Refusals answer `{"detail": "<plain words>", "code": "<code>"}` plus figures where they help (`remaining`, `currency`). Audit rows hold ids and codes only.

## Storage, privacy and deletion

`finance_links` has forced RLS with the tenant policy, the restrictive `owner_only_finance_links` policy (which admits the tenant purge role) and `lifecycle_purge_finance_links`. `business_ledger_allocations` has the tenant policy and the purge policy (it belongs to the business workspace). Both are listed in `tenant_lifecycle_tables.py`. The privacy export includes the person's links (without the idempotency hash) and the allocation rows they made; "Delete my data" removes both.

Deleting the owner's **personal workspace** (a workspace deletion job) also removes the allocation rows they made in business workspaces: the registry marks `source_workspace_id` as the table's origin column (`tenant_lifecycle_tables.py`), the purge reads it, and the purge policy admits a workspace deletion job of either workspace. It runs as the narrow `anum_tenant_purger` role (no BYPASSRLS); rows other workspaces made stay.

Unlinking a document from a payment in the Document vault voids its evidence link here first (`FinanceLinkPort.withdraw`), so the vault and the ledger always agree.

## Not built here

Link screens inside the modules (each module PR adds its own) and automatic matching: a link is only ever proposed. [Attribution suggestions](finance.md#attribution-suggestions) propose business allocations, vehicle costs, purchases, debt and bill payments in the suggestion queue, and Accept goes through `links.create` or `links.allocate` with every rule above; no policy can link on its own. The business books' P&L reads the allocation rows ([Business books](business-books.md)); a business's own supporting documents are vault links in the business workspace, not finance links ([evidence](business-books.md#supporting-documents-and-evidence)).
