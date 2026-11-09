"""Reconciliation exceptions in the one suggestion queue (docs/finance.md#suggestions).

Each open exception of a reconciliation session (blocking or warning; ``info`` is left on the session page) and
each month with activity no statement covers becomes one ``Suggestion`` of kind ``reconciliation``: what was found,
why it matters, the evidence, the money it is about, and a Review action that opens the session. The queue never
resolves an exception: matching, attaching, marking pending and correcting entries happen on the session, where
the payload-hash approval and the owner's resolution rules apply. The queue refuses Accept on such an item (its
action is ``open``); dismissing it keeps it dismissed (its key is the session and the exception, so a new exception
in a later session shows again).

The producer only reads: it calls :func:`reconciliation_api.list_open_exceptions` in its own short unit of work.
"""

from __future__ import annotations

from decimal import Decimal

from . import suggestions as SG

PRODUCER = "reconciliation"
MAX_EXCEPTIONS = 500  # per queue build; the session page pages the rest

_WHY = {
    "blocking": "The reconciliation cannot be completed until this is resolved on its session.",
    "warning": "The reconciliation can be completed, but this difference is not explained yet.",
}
_DETECTED = {
    "not_in_ledger": "A statement row is not in your books.",
    "unmatched_payment": "A payment in your books is not confirmed by the statement.",
    "unmatched_deposit": "A deposit in your books is not confirmed by the statement.",
    "pending": "An SMS or Apple Pay record is not on a statement yet.",
    "possible_duplicate": "Two records may be the same payment.",
    "wrong_direction": "A record's direction looks wrong against the statement.",
    "reversal_pair": "A payment and its reversal look like a pair.",
    "unknown_currency": "A record's currency is not known.",
    "unrecognized_fee": "A fee is not recorded as a bank fee.",
    "missing_period": "A month with activity has no statement.",
    "opening_differs": "The statement's opening balance differs from your books.",
    "amount_due_differs": "The card's amount due differs from the card balance.",
}


def _href(reconciliation_id: str | None) -> str:
    return f"#finance-reconciliation?session={reconciliation_id}" if reconciliation_id else "#finance-reconciliation"


def produce(snap: SG.Snapshot) -> list[SG.Suggestion]:
    from .reconciliation_api import list_open_exceptions

    page = list_open_exceptions(snap.context, limit=MAX_EXCEPTIONS, offset=0)
    # A month with no statement and no session is already in the queue from the statements producer: list it once.
    built_in = {s.key for s in SG.missing_statements(snap)}
    out: list[SG.Suggestion] = []
    for item in page.items:
        exc = item.exception
        account = snap.accounts.get(item.account_id)
        if account is None:
            continue
        if item.reconciliation_id is None and exc.month and f"missing_statement:{account.id}:{exc.month}" in built_in:
            continue
        params = {"exception": exc.kind, "severity": exc.severity, "account": account.name[:80]}
        if item.period_start and item.period_end:
            params |= {"start": item.period_start.isoformat(), "end": item.period_end.isoformat()}
        if exc.month:
            params["month"] = exc.month
        evidence = [SG.Evidence(label=account.name[:120], ref_type="account", ref_id=account.id,
                                value=(f"{item.period_start.isoformat()} to {item.period_end.isoformat()}"
                                       if item.period_start and item.period_end else exc.month))]
        if exc.description or exc.transaction_id or exc.statement_ref:
            evidence.append(SG.Evidence(
                label=(exc.description or exc.statement_ref or "Ledger record")[:120],
                ref_type="transaction" if exc.transaction_id else "other",
                ref_id=exc.transaction_id or exc.statement_ref,
                value=" ".join(p for p in (exc.posted_on.isoformat() if exc.posted_on else "",
                                           f"{account.currency} {exc.amount}" if exc.amount is not None else "") if p) or None,
            ))
        if exc.other_transaction_id:
            evidence.append(SG.Evidence(label="The other record", ref_type="transaction", ref_id=exc.other_transaction_id))
        effect = []
        if exc.amount is not None and exc.amount != 0:
            effect = [SG.Effect(amount=abs(Decimal(exc.amount)), currency=account.currency, direction="in" if exc.amount > 0 else "out")]
        scope = item.reconciliation_id or account.id
        out.append(SG.Suggestion(
            key=f"reconciliation:{scope}:{exc.id}"[:200], producer=PRODUCER, kind="reconciliation",
            detected=_DETECTED.get(exc.kind, "A reconciliation exception needs your review."),
            why=("Import that month's statement, then reconcile it." if exc.kind == "missing_period"
                 else _WHY.get(exc.severity, _WHY["warning"])),
            params=params, evidence=evidence, effect=effect,
            # Review opens the session; nothing in the queue changes the ledger.
            action=SG.ProposedAction(kind="open", transaction_ids=[t for t in (exc.transaction_id, exc.other_transaction_id) if t]),
            href=_href(item.reconciliation_id), occurred_on=exc.posted_on or item.period_end,
        ))
    return out


SG.register_producer(PRODUCER, produce)
