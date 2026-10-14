"""Personal finance: the ledger, safe-to-spend, Guardian rules and read-only connectors (docs/finance.md).

Stages 2 to 5 of the Finance domain. Nothing here moves money: there is no payment,
transfer, order or withdrawal call anywhere in this package, connectors only read, and the
Guardian ``gate`` denies every money-moving action. Agent tools read, or propose a stricter
rule that the person confirms.

* ``money``: exact amounts (``Decimal``), the one rounding rule, per-currency totals.
* ``models``: settings, accounts, transactions, imports, bills, debts, goals, holdings.
* ``categories``: the built-in, rule-based categories (no AI) and the owner's own rules.
* ``csv_import``: statement CSV parsing (bank presets, generic format), preview and dedupe.
* ``store``: the store protocol, the in-memory store and ``open_finance_store``.
* ``summary``: balances, the month's spend by category, income versus spend, bills, debts.
* ``tools``: the read-only agent tools (``finance.summary``, ``finance.transactions.search``,
  ``finance.bills.upcoming``) and the request parsing behind the finance skill.
* ``api``: the HTTP routes under ``/api/v1/finance`` (stage 2).
* ``safe_to_spend`` and ``fx``: stage 3, what is safe to spend, and recorded exchange rates.
* ``guardian`` and ``policy``: stage 4, the pure rules evaluator and the gate, and the ledger state.
* ``connectors``, ``sync`` and ``config``: stage 5, read-only connectors and their sync.
* ``plan_api``: the HTTP routes of stages 3 to 5.

Every record is the signed-in person's own: a restrictive row policy on ``anum.user_id``
(migration ``0056_finance_ledger``) on top of the tenant and workspace isolation.
"""
