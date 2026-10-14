"""Personal finance: the ledger and the Financial Twin, read only (docs/finance.md).

Stage 2 of the Finance domain. Nothing here moves money: there is no payment, transfer,
order, bank, card or broker call anywhere in this package, and the agent tools only read.

* ``money``: exact amounts (``Decimal``), the one rounding rule, per-currency totals.
* ``models``: settings, accounts, transactions, imports, bills, debts, goals, holdings.
* ``categories``: the built-in, rule-based categories (no AI) and the owner's own rules.
* ``csv_import``: statement CSV parsing (bank presets, generic format), preview and dedupe.
* ``store``: the store protocol, the in-memory store and ``open_finance_store``.
* ``summary``: balances, the month's spend by category, income versus spend, bills, debts.
* ``tools``: the read-only agent tools (``finance.summary``, ``finance.transactions.search``,
  ``finance.bills.upcoming``) and the request parsing behind the finance skill.
* ``api``: the HTTP routes under ``/api/v1/finance``.

Every record is the signed-in person's own: a restrictive row policy on ``anum.user_id``
(migration ``0056_finance_ledger``) on top of the tenant and workspace isolation.
"""
